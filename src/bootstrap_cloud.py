# -*- coding: utf-8 -*-
"""
bootstrap_cloud.py — GitHub Actions 云端从零建库（唯一行情源：同花顺）

设计：不依赖本地数据库、种子库或跨 run 缓存。每次运行先删掉旧库，
     从同花顺公开接口重新抓取 → 建最小库 → 交给 simple_pool.py 评分出页面。
     全程纯 Python 标准库，云端 runner 独立可跑。

口径（用户 60/20 要求）：
    · K线：每只票最多 60 根日线
    · 热度：同花顺真实热榜 + 最近 20 个交易日热度序列
    · 其余历史一律不要，控制抓取量与库体积

数据源（全部 10jqka.com.cn / 10jqka.com.cn 子域）：
    · d.10jqka.com.cn/v6/line/hs_{code}/01/last.js  → 代码枚举 + 名称 + 60 根日线
    · dq.10jqka.com.cn/fuyao/hot_list_data/...      → 真实热度排名
    · data.10jqka.com.cn/dataapi/limit_up/...       → 涨停/连板辅助
    · data.10jqka.com.cn/dataapi/limit_up/block_top → 概念板块榜
    · basic.10jqka.com.cn/fuyao/f10_stock_index/... → 个股概念

代码表生成：按 A 股主板/创业板代码段枚举，用 K 线接口一次请求同时拿到
名称与日线（无效代码秒回 404），不依赖任何其他行情站点。

建表（对齐 simple_pool.py 的实际查询）：
    quote_clean(trade_date, code, name, open, high, low, close, volume,
                pct_recalc, board, is_st, is_bj)
    heat_rank_daily(trade_date, code, rank)
    ths_concept_rank_daily(snap_time, rank, concept_name, change_pct,
                           limit_up_num, days)
    tag_stat_daily(trade_date, tag_kind, tag)
    limit_up_day_v2(trade_date, code, reason_raw)

用法：
    python bootstrap_cloud.py                    # 全市场建库
    python bootstrap_cloud.py --limit 500        # 只枚举前 500 个候选代码（自测）
    python bootstrap_cloud.py --workers 16       # 并发数
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

DB = paths.DB
BARS = 60          # K线最多 60 根
HEAT_HIST = 20     # 热度 20 个交易日

UA = {"User-Agent": "Mozilla/5.0 (compatible; stockpool-ci/1.0)"}
UA_Q = {**UA, "Referer": "http://q.10jqka.com.cn/"}
UA_D = {**UA, "Referer": "https://stockpage.10jqka.com.cn/"}

THS_KLINE = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
THS_HOT = ("https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
           "?stock_type=a&type={kind}&list_type=normal")
THS_LIMITUP = ("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
               "?page=1&limit=200&field=199112,10,9001,330323,330324"
               "&order_field=330324&order_type=0")
THS_LIANBAN = "https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up?page=1&limit=200"
THS_CONCEPT = ("https://data.10jqka.com.cn/dataapi/limit_up/block_top"
               "?filter=HS,GEM2STAR&page=1&limit=100"
               "&field=199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
               "1968584,3475914,9003,9004&order_field=330324&order_type=0&date={date}")
F10_CONCEPT = ("https://basic.10jqka.com.cn/fuyao/f10_stock_index/concept/v1/"
               "stock_concept_list?market_id={mid}&code={code}")

# 主板 + 创业板代码段（科创板/北交所不入池，不枚举）
CODE_RANGES = ((1, 3999), (300000, 302999), (600000, 605999))

DDL = """
CREATE TABLE IF NOT EXISTS quote_clean (
    trade_date TEXT, code TEXT, name TEXT, open REAL, high REAL, low REAL,
    close REAL, volume REAL, pct_recalc REAL, board TEXT, is_st INTEGER,
    is_bj INTEGER, PRIMARY KEY (code, trade_date)
);
CREATE TABLE IF NOT EXISTS heat_rank_daily (
    trade_date TEXT, code TEXT, rank INTEGER, PRIMARY KEY (trade_date, code)
);
CREATE TABLE IF NOT EXISTS ths_concept_rank_daily (
    snap_time TEXT, rank INTEGER, concept_name TEXT, change_pct REAL,
    limit_up_num INTEGER, days INTEGER, PRIMARY KEY (snap_time, concept_name)
);
CREATE TABLE IF NOT EXISTS tag_stat_daily (
    trade_date TEXT, tag_kind TEXT, tag TEXT
);
CREATE TABLE IF NOT EXISTS limit_up_day_v2 (
    trade_date TEXT, code TEXT, reason_raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_qc_date ON quote_clean(trade_date);
"""


def _get(url, headers, timeout=12, retries=2):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if i == retries - 1:
                return None
            time.sleep(0.3 * (i + 1))
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(0.3 * (i + 1))
    return None


def _board_of(code):
    if code.startswith("30"):
        return "创业板"
    return "主板"


def parse_last_js(txt):
    """同花顺 last.js JSONP → (name, bars, today)。
    bars: [[YYYYMMDD, open, close, high, low, volume], ...]（最多 BARS 根）。"""
    i, j = txt.find("("), txt.rfind(")")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(txt[i + 1:j])
    except Exception:
        return None
    data = d.get("data")
    if not isinstance(data, str) or not data:
        return None
    bars = []
    for row in data.split(";"):
        p = row.split(",")
        if len(p) < 6:
            continue
        try:
            day = p[0].replace("-", "")
            if len(day) != 8 or not day.isdigit():
                continue
            op, hi, lo, cl, vol = (float(p[1]), float(p[2]), float(p[3]),
                                   float(p[4]), float(p[5]))
            bars.append([day, op, cl, hi, lo, vol])
        except (ValueError, IndexError):
            continue
    if not bars:
        return None
    return str(d.get("name") or "").strip(), bars[-BARS:], str(d.get("today") or "")


def _probe_code(code):
    """一次请求拿代码有效性 + 名称 + 60 根日线。无效代码（404）直接丢弃。"""
    b = _get(THS_KLINE.format(code=code), UA_D, timeout=10, retries=2)
    if not b:
        return None
    got = parse_last_js(b.decode("utf-8", "ignore"))
    if not got:
        return None
    name, bars, today = got
    return {"code": code, "name": name, "bars": bars, "today": today}


def enumerate_universe(limit=None, workers=24):
    """同花顺 K线接口枚举主板/创业板全市场代码，返回带日线的快照列表。"""
    candidates = ["%06d" % i for a, b in CODE_RANGES for i in range(a, b + 1)]
    if limit:
        candidates = candidates[:limit]
    t0, done, out = time.time(), 0, []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_probe_code, c): c for c in candidates}
        for fu in as_completed(futs):
            done += 1
            snap = fu.result()
            if snap:
                out.append(snap)
            if done % 2000 == 0:
                print(f"  代码枚举 {done}/{len(candidates)}，有效 {len(out)}，"
                      f"{time.time()-t0:.0f}s", flush=True)
    out.sort(key=lambda x: x["code"])
    print(f"[代码表] 枚举 {len(candidates)} → 有效 {len(out)} 只，用时 {time.time()-t0:.0f}s",
          flush=True)
    return out


def fetch_hot():
    """同花顺真实热榜（小时榜 + 日榜合并，同一代码取靠前名次）。"""
    merged = {}
    for kind in ("hour", "day"):
        b = _get(THS_HOT.format(kind=kind), UA, timeout=12)
        if not b:
            continue
        try:
            rows = (json.loads(b.decode("utf-8", "ignore")).get("data") or {}).get("stock_list") or []
        except Exception:
            continue
        for r in rows:
            c, rank = str(r.get("code", "")), r.get("order")
            if len(c) == 6 and c.isdigit() and rank:
                rank = int(rank)
                if c not in merged or rank < merged[c]:
                    merged[c] = rank
    out = sorted(merged.items(), key=lambda x: x[1])
    print(f"[热榜] 同花顺真实排名 {len(out)} 只", flush=True)
    return out


def fetch_pool_codes():
    """涨停/连板池辅助代码（只作诊断，不参与主枚举）。"""
    out = set()
    for url in (THS_LIMITUP, THS_LIANBAN):
        b = _get(url, UA, timeout=12)
        if not b:
            continue
        try:
            d = json.loads(b.decode("utf-8", "ignore"))
        except Exception:
            continue
        data = d.get("data") or {}
        info = data.get("info") if isinstance(data, dict) else data
        if not info and isinstance(data, dict):
            for grp in data.get("data") or []:
                info = (info or []) + list(grp.get("code_list") or [])
        for r in info or []:
            c = str(r.get("code", ""))
            if len(c) == 6 and c.isdigit():
                out.add(c)
    return sorted(out)


def fetch_concepts(date):
    b = _get(THS_CONCEPT.format(date=date), UA, timeout=12)
    if not b:
        return []
    try:
        rows = json.loads(b.decode("utf-8", "ignore")).get("data") or []
    except Exception:
        return []
    out = []
    for i, r in enumerate(rows, 1):
        name = r.get("name") or r.get("concept_name")
        if not name:
            continue
        try:
            chg = float(r.get("change_pct") or r.get("199112") or 0)
            lu = int(r.get("limit_up_num") or r.get("330324") or 0)
            days = int(r.get("days") or 0)
        except Exception:
            chg, lu, days = 0.0, 0, 0
        out.append((date, i, name, chg, lu, days))
    print(f"[概念榜] {len(out)} 个板块", flush=True)
    return out


def _f10_market(code):
    if code.startswith(("6", "9")):
        return 17
    return 33


def build_concept_cache(con, workers):
    """同花顺 F10 个股概念 → cache/f10_concepts.json。"""
    def one(code):
        url = F10_CONCEPT.format(mid=_f10_market(code), code=code)
        b = _get(url, {**UA, "Accept": "application/json"}, timeout=10, retries=2)
        if not b:
            return code, None
        try:
            items = json.loads(b.decode("utf-8", "ignore")).get("data") or []
            concepts = [[it["name"], 1 if "走势最相关" in (it.get("tags") or []) else 0]
                        for it in items if it.get("name")]
            return code, (concepts or None)
        except Exception:
            return code, None

    codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM quote_clean")]
    data, t0, done = {}, time.time(), 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fu in as_completed([ex.submit(one, c) for c in codes]):
            code, items = fu.result()
            done += 1
            if items:
                data[code] = items
            if done % 1000 == 0:
                print(f"  概念进度 {done}/{len(codes)}，{time.time()-t0:.0f}s", flush=True)
    path = paths.CONCEPT_CACHE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"date": dt.date.today().strftime("%Y%m%d"),
                   "src": "10jqka f10", "data": data}, f, ensure_ascii=False)
    print(f"[概念缓存] {len(data)} 只，用时 {time.time()-t0:.0f}s，"
          f"{os.path.getsize(path)//1024}KB", flush=True)
    return data


def build(limit=None, workers=16):
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    date = (dt.datetime.utcnow() + dt.timedelta(hours=8)).strftime("%Y%m%d")

    snaps = enumerate_universe(limit, max(workers, 24))
    if not snaps:
        print("[FATAL] 同花顺代码枚举为空", flush=True)
        con.close()
        return False

    # 日线落库
    buf = []
    for s in snaps:
        code, name = s["code"], s["name"]
        board = _board_of(code)
        is_st = 1 if "ST" in name.upper() else 0
        for d, op, cl, hi, lo, vol in s["bars"]:
            pct = round((cl - op) / op * 100, 4) if op else 0.0
            buf.append((d, code, name, op, hi, lo, cl, vol, pct, board, is_st, 0))
    con.executemany("INSERT OR REPLACE INTO quote_clean VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
    con.commit()
    qdate = con.execute("SELECT MAX(trade_date) FROM quote_clean").fetchone()[0] or date

    # 休市判定
    today_d, qdate_d = (dt.datetime.strptime(x, "%Y%m%d").date() for x in (date, qdate))
    lag = (today_d - qdate_d).days
    force = os.environ.get("FORCE_BUILD") == "1"
    if (today_d.weekday() >= 5 or lag > 4) and not force:
        reason = "周末休市" if today_d.weekday() >= 5 else f"节假日休市(行情最新{qdate}落后{lag}天)"
        print(f"[跳过] {reason}，不生成页面", flush=True)
        flag = os.environ.get("STOCKPOOL_SKIP_FLAG")
        if flag:
            with open(flag, "w", encoding="utf-8") as f:
                f.write(reason)
        con.close()
        return True

    # 热度：真实热榜名次 + 最近 HEAT_HIST 个交易日的动量排名序列
    hot = fetch_hot()
    hot_map = dict(hot)
    con.executemany("INSERT OR REPLACE INTO heat_rank_daily VALUES (?,?,?)",
                    [(qdate, c, r) for c, r in hot])
    con.executemany("INSERT INTO tag_stat_daily VALUES (?,?,?)",
                    [(qdate, "hot", c) for c, _ in hot])
    # 当日未上榜的票按近 5 日涨幅给代理名次 101~3000（仅热度维近似）
    extra = [(s["code"], _momentum(s["bars"])) for s in snaps if s["code"] not in hot_map]
    extra.sort(key=lambda x: -x[1])
    con.executemany("INSERT OR IGNORE INTO heat_rank_daily VALUES (?,?,?)",
                    [(qdate, c, 101 + i) for i, (c, _) in enumerate(extra[:2900])])

    all_dates = sorted({b[0] for s in snaps for b in s["bars"]}, reverse=True)
    hist_dates = all_dates[1:HEAT_HIST + 1]
    for hd in hist_dates:
        ranks = []
        for s in snaps:
            bars = [b for b in s["bars"] if b[0] <= hd]
            if len(bars) >= 2:
                ranks.append((_momentum(bars), s["code"]))
        ranks.sort(reverse=True)
        con.executemany("INSERT OR IGNORE INTO heat_rank_daily VALUES (?,?,?)",
                        [(hd, c, 1 + i) for i, (_, c) in enumerate(ranks[:3000])])
    print(f"[热度] 真实 {len(hot)} + 序列 {len(hist_dates)} 日，"
          f"共 {con.execute('SELECT COUNT(*) FROM heat_rank_daily').fetchone()[0]} 行", flush=True)

    pool_codes = fetch_pool_codes()
    print(f"[辅助] 涨停/连板池 {len(pool_codes)} 只", flush=True)

    build_concept_cache(con, workers)
    concepts = fetch_concepts(qdate)
    if concepts:
        con.executemany("INSERT OR REPLACE INTO ths_concept_rank_daily VALUES (?,?,?,?,?,?)", concepts)
        for snap, rank, name, chg, lu, days in concepts:
            con.execute("INSERT INTO tag_stat_daily VALUES (?,?,?)", (snap, "concept", name))
    con.commit()

    n_q = con.execute("SELECT COUNT(*) FROM quote_clean").fetchone()[0]
    n_c = con.execute("SELECT COUNT(DISTINCT code) FROM quote_clean").fetchone()[0]
    print(f"[建库完成] {n_q} 行 / {n_c} 只，库 {os.path.getsize(DB)//1024}KB", flush=True)
    con.close()
    return n_c > 0


def _momentum(bars):
    """近 5 根日线涨幅（代理热度）。"""
    if len(bars) < 2:
        return 0.0
    base = bars[-min(6, len(bars))][2]
    return ((bars[-1][2] - base) / base * 100) if base else 0.0


def main(argv=None):
    ap = argparse.ArgumentParser(description="云端自建最小行情库（同花顺唯一数据源）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args(argv)
    ok = build(a.limit, a.workers)
    print("[OK] 云端库就绪" if ok else "[FAIL] 建库失败", flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
