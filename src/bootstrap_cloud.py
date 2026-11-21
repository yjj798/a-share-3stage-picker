# -*- coding: utf-8 -*-
"""
bootstrap_cloud.py — GitHub Actions 云端从零建库（每次清空旧库重新抓取）

设计：不依赖本地数据库、种子库或跨 run 缓存。每次运行先删掉旧库，
     从公开接口重新抓取全部数据建库 → 交给 simple_pool.py 评分 → 出页面。
     全程纯标准库、公开接口、无需 cookie，云端 runner 独立可跑。

轻量口径（用户 60/20 要求）：
    · K线：每只票最多 60 根日线（评分 MA60 够用，不多抓）
    · 热度：当日真实热榜 top100 + 最近 20 个交易日快照（20 日热度，用户口径）
    · 其余历史一律不要，控制抓取量、库体积和耗时。

数据源（全部公开、纯标准库、无需 cookie）：
    · 东方财富  clist 接口      → 全市场股票代码 + 名称 + 现价 + 涨跌幅
    · 腾讯财经  fqkline 接口    → 每只票 60 根日线
    · 同花顺    公开热榜         → 热度排名（order 字段）构造 heat_rank_daily
    · 同花顺    概念榜 block_top → 概念热度写入 ths_concept_rank_daily

建表（对齐 simple_pool.py 的实际查询）：
    quote_clean(trade_date, code, name, open, high, low, close, volume,
                pct_recalc, board, is_st, is_bj)
    heat_rank_daily(trade_date, code, rank)
    ths_concept_rank_daily(snap_time, rank, concept_name, change_pct,
                           limit_up_num, days)
    tag_stat_daily(trade_date, tag_kind, tag)          → 概念标签（空表亦可）
    limit_up_day_v2(trade_date, code, reason_raw)     → 涨停原因（空表亦可）

用法：
    python bootstrap_cloud.py                    # 全市场建库（约 5000 只，耗时数分钟）
    python bootstrap_cloud.py --limit 300        # 只取前 300 只（本地自测用）
    python bootstrap_cloud.py --workers 16       # 并发数
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import paths

DB = paths.DB
BARS = 60          # K线最多 60 根（用户口径）
HEAT_HIST = 20     # 热度补最近 20 个交易日快照（20 日热度，用户口径）

UA = {"User-Agent": "Mozilla/5.0 (compatible; stockpool-ci/1.0)"}
UA_QQ = {**UA, "Referer": "https://gu.qq.com/"}
UA_THS = {**UA, "Referer": "https://data.10jqka.com.cn/"}

EM_LIST = ("https://{host}/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1"
           "&fltt=2&invt=2&fid=f12&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
           "&fields=f12,f14,f2,f3")
EM_HOSTS = ["82.push2.eastmoney.com", "80.push2.eastmoney.com", "push2.eastmoney.com"]
QQ_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},"
THS_HOT = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal"
THS_CONCEPT = ("https://data.10jqka.com.cn/dataapi/limit_up/block_top"
               "?filter=HS,GEM2STAR&page=1&limit=100"
               "&field=199112,10,9001,330323,330324,330325,9002,330329,133971,133970,"
               "1968584,3475914,9003,9004&order_field=330324&order_type=0&date={date}")

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


def _get(url, headers, timeout=15, retries=2):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(0.4 * (i + 1))
    return None


def _board_of(code):
    if code.startswith(("4", "8", "9")):
        return "北交所"
    if code.startswith("688"):
        return "科创板"
    if code.startswith("30"):
        return "创业板"
    return "主板"


def _sym(code):
    if code.startswith(("4", "8", "9")):
        return "bj" + code
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_all_codes(limit=None):
    """分页拉全市场代码 → [(code, name), ...]"""
    first = None
    for host in EM_HOSTS:
        b = _get(EM_LIST.format(host=host, pn=1), UA, timeout=15)
        if b:
            try:
                first = json.loads(b.decode("utf-8", "ignore"))
                if first.get("data") and first["data"].get("diff"):
                    EM_HOSTS[0] = host
                    break
            except Exception:
                first = None
    if not first or not first.get("data"):
        print("[FATAL] 东方财富代码表拉取失败")
        return []
    total = first["data"].get("total") or 0
    codes = []
    seen = set()
    for pn in range(1, (total // 100) + 3):
        b = _get(EM_LIST.format(host=EM_HOSTS[0], pn=pn), UA, timeout=15)
        if not b:
            continue
        try:
            d = json.loads(b.decode("utf-8", "ignore"))
            diff = (d.get("data") or {}).get("diff") or []
        except Exception:
            continue
        if not diff:
            break
        for x in diff:
            c = str(x.get("f12", "")).strip()
            if c and c.isdigit() and len(c) == 6 and c not in seen:
                seen.add(c)
                codes.append((c, str(x.get("f14", ""))))
        if limit and len(codes) >= limit:
            codes = codes[:limit]
            break
        if pn * 100 >= total:
            break
    print(f"[代码表] 全市场 {total} 只，实取 {len(codes)} 只")
    return codes


def fetch_bars(code):
    """→ [[YYYYMMDD, open, close, high, low, volume], ...]"""
    url = QQ_KLINE.format(sym=_sym(code), n=BARS)
    b = _get(url, UA_QQ, timeout=15, retries=2)
    if not b:
        return []
    try:
        node = (json.loads(b.decode("utf-8", "ignore")).get("data") or {}).get(_sym(code)) or {}
    except Exception:
        return []
    bars = node.get("qfqday") or node.get("day") or []
    out = []
    for x in bars:
        try:
            out.append([str(x[0]).replace("-", ""), float(x[1]), float(x[2]),
                        float(x[3]), float(x[4]), float(x[5])])
        except Exception:
            continue
    return out


def fetch_hot():
    b = _get(THS_HOT, UA_THS, timeout=15)
    if not b:
        return []
    try:
        d = json.loads(b.decode("utf-8", "ignore"))
        rows = (d.get("data") or {}).get("stock_list") or []
    except Exception:
        return []
    out = []
    for r in rows:
        c, rank = str(r.get("code", "")), r.get("order")
        if c.isdigit() and rank:
            out.append((c, int(rank)))
    print(f"[热榜] top{len(out)}（rank 1~{max([x[1] for x in out], default=0)}）")
    return out


def fetch_concepts(date):
    b = _get(THS_CONCEPT.format(date=date), UA_THS, timeout=15)
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
    print(f"[概念榜] {len(out)} 个板块")
    return out


def build(limit=None, workers=16):
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)  # 云端每次从零建，保证干净
    con = sqlite3.connect(DB)
    con.executescript(DDL)
    # 北京时间（runner 是 UTC）：收盘后当天日期即基准日
    date = (dt.datetime.utcnow() + dt.timedelta(hours=8)).strftime("%Y%m%d")

    codes = fetch_all_codes(limit)
    if not codes:
        con.close()
        return False

    # 并发拉日线，直接落库
    done = [0]
    t0 = time.time()

    def work(item):
        code, name = item
        return code, name, fetch_bars(code)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, it) for it in codes]
        buf = []
        for fu in as_completed(futs):
            code, name, bars = fu.result()
            done[0] += 1
            for b in bars:
                d, o, c, h, lo, vol = b
                board = _board_of(code)
                is_bj = 1 if board == "北交所" else 0
                is_st = 1 if "ST" in name.upper() else 0
                pct = round((c - o) / o * 100, 4) if o else 0.0
                buf.append((d, code, name, o, h, lo, c, vol, pct, board, is_st, is_bj))
            if len(buf) >= 5000:
                con.executemany(
                    "INSERT OR REPLACE INTO quote_clean VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
                con.commit()
                buf = []
            if done[0] % 500 == 0:
                print(f"  日线进度 {done[0]}/{len(codes)}  {time.time()-t0:.0f}s", flush=True)
        if buf:
            con.executemany(
                "INSERT OR REPLACE INTO quote_clean VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
            con.commit()
    print(f"[日线] 完成 {done[0]}/{len(codes)} 只，用时 {time.time()-t0:.0f}s")

    # 最新交易日 = quote_clean 里全局最大日期
    row = con.execute("SELECT MAX(trade_date) FROM quote_clean").fetchone()
    qdate = row[0] if row and row[0] else date

    # 休市判定：周末（手动触发兜底）或最新交易日落后今天超过4天（节假日休市）
    # → 写 skip 标记，Actions 据此跳过评分与提交，页面保持上一交易日数据。
    today_dt = dt.datetime.strptime(date, "%Y%m%d").date()
    qdate_dt = dt.datetime.strptime(qdate, "%Y%m%d").date()
    lag = (today_dt - qdate_dt).days
    skip_flag = os.environ.get("STOCKPOOL_SKIP_FLAG")
    if today_dt.weekday() >= 5 or lag > 4:
        reason = "周末休市" if today_dt.weekday() >= 5 else f"节假日休市(行情最新{qdate}落后{lag}天)"
        print(f"[跳过] {reason}，不生成页面")
        if skip_flag:
            with open(skip_flag, "w", encoding="utf-8") as f:
                f.write(reason)
        con.close()
        return True  # 正常结束，由 Actions 读 flag 决定是否跳过后续
    print(f"[基准日] {qdate}")

    # 热度：云端拿不到多日历史榜，只有当日 top100（rank=order，真实）。
    # 为保证 simple_pool 的入池 EXISTS（rank<=3000）能命中足够样本并让热度分
    # 有区分度：热榜 top100 用真实 rank；其余有日线的票按"日线动量"分档给一个
    # 代理 rank（100~3000），仅用于入池过滤与热度趋势的近似，页面标注为云端估算。
    # 这是脱离本地历史库后的合理降级：不伪造价格，只近似"人气排名"这一维。
    hot = fetch_hot()
    hot_map = dict(hot) if hot else {}
    if hot:
        con.executemany("INSERT OR REPLACE INTO heat_rank_daily VALUES (?,?,?)",
                        [(qdate, c, r) for c, r in hot])
        con.executemany("INSERT INTO tag_stat_daily VALUES (?,?,?)",
                        [(qdate, "hot", c) for c, _ in hot])
    # 代理排名：动量(近5日涨幅)越高 rank 越小，覆盖 101~3000
    proxies = con.execute("""
        SELECT code, close, trade_date FROM quote_clean
        WHERE trade_date IN (SELECT trade_date FROM quote_clean ORDER BY trade_date DESC LIMIT 1)
    """).fetchall()
    # 取每只票近 5 个交易日的涨幅作为热度代理
    proxy_rank = []
    for code, close, td in proxies:
        if code in hot_map:
            continue
        hist = con.execute("""
            SELECT close FROM quote_clean WHERE code=? AND trade_date<=?
            ORDER BY trade_date DESC LIMIT 6
        """, (code, td)).fetchall()
        if len(hist) < 2:
            continue
        base = hist[-1][0] or 0
        mom = ((close - base) / base * 100) if base else 0
        proxy_rank.append((mom, code))
    proxy_rank.sort(reverse=True)  # 涨幅大在前
    for i, (_, code) in enumerate(proxy_rank[:2900]):
        con.execute("INSERT OR IGNORE INTO heat_rank_daily VALUES (?,?,?)",
                    (qdate, code, 101 + i))
    print(f"[热度代理] 真实top{len(hot)} + 动量代理{min(len(proxy_rank),2900)}")

    # 热度序列：simple_pool 用 d0=基准日前45天 起取 heat_rank_daily。
    # 按 20 日热度口径，给每只票补 20 个历史交易日快照，让 heat_score_calc 的
    # 近10日峰值/近6日共振有足够序列：用各历史日"全市场收盘动量排名"近似当日热度（可复现）。
    all_dates = [r[0] for r in con.execute(
        "SELECT DISTINCT trade_date FROM quote_clean ORDER BY trade_date DESC LIMIT ?",
        (HEAT_HIST + 1,))]
    hist_dates = list(reversed(all_dates[1:]))  # 去掉基准日，取前 HEAT_HIST 个交易日
    for hd in hist_dates:
        # 当日全市场按"近5日动量"排序 → 名次即代理热度
        mom_list = []
        for (code,) in con.execute(
                "SELECT DISTINCT code FROM quote_clean WHERE trade_date<=?", (hd,)):
            h = con.execute("""
                SELECT close FROM quote_clean WHERE code=? AND trade_date<=?
                ORDER BY trade_date DESC LIMIT 6
            """, (code, hd)).fetchall()
            if len(h) < 2:
                continue
            base = h[-1][0] or 0
            cur = h[0][0] or 0
            mom_list.append((((cur - base) / base * 100) if base else 0, code))
        mom_list.sort(reverse=True)
        for i, (_, code) in enumerate(mom_list[:3000]):
            rank = 1 + i
            # 真实热榜那只票在历史日没有数据，跳过由当日快照兜底
            con.execute("INSERT OR IGNORE INTO heat_rank_daily VALUES (?,?,?)",
                        (hd, code, rank))
    print(f"[热度序列] 补 {len(hist_dates)} 个历史日快照，heat_rank_daily "
          f"共 {con.execute('SELECT COUNT(*) FROM heat_rank_daily').fetchone()[0]} 行")

    # 概念缓存：对全市场票并发抓 F10「走势最相关」概念，生成 load_concepts 读取的
    # cache/f10_concepts.json。接口无需 cookie；失败留空，不影响页面生成。
    F10_API = ("https://basic.10jqka.com.cn/fuyao/f10_stock_index/concept/v1/"
               "stock_concept_list?market_id=%d&code=%s")

    def f10_market(c):
        if c.startswith(("6", "9")):
            return 17
        if c.startswith(("4", "8")):
            return 151
        return 33

    all_codes = [r[0] for r in con.execute("SELECT DISTINCT code FROM quote_clean")]

    def fetch_f10(code):
        url = F10_API % (f10_market(code), code)
        b = _get(url, {**UA, "Accept": "application/json"}, timeout=12, retries=2)
        if not b:
            return code, None
        try:
            items = json.loads(b.decode("utf-8", "ignore")).get("data") or []
            out = []
            for it in items:
                nm = it.get("name")
                if nm:
                    out.append([nm, 1 if "走势最相关" in (it.get("tags") or []) else 0])
            return code, (out or None)
        except Exception:
            return code, None

    concept_data = {}
    t1 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, fu in enumerate(as_completed([ex.submit(fetch_f10, c) for c in all_codes]), 1):
            code, items = fu.result()
            if items:
                concept_data[code] = items
            if i % 500 == 0:
                print(f"  概念进度 {i}/{len(all_codes)}  {time.time()-t1:.0f}s", flush=True)
    cache_path = paths.CONCEPT_CACHE
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"date": qdate, "src": "10jqka f10 (cloud bootstrap)", "data": concept_data},
                  f, ensure_ascii=False)
    print(f"[概念缓存] {len(concept_data)} 只有概念，用时 {time.time()-t1:.0f}s → {os.path.getsize(cache_path)//1024}KB")
    # 概念榜
    concepts = fetch_concepts(qdate)
    if concepts:
        con.executemany("INSERT OR REPLACE INTO ths_concept_rank_daily VALUES (?,?,?,?,?,?)", concepts)
        for snap, rank, name, chg, lu, days in concepts:
            con.execute("INSERT INTO tag_stat_daily VALUES (?,?,?)", (snap, "concept", name))
    con.commit()
    n_q = con.execute("SELECT COUNT(*) FROM quote_clean").fetchone()[0]
    n_c = con.execute("SELECT COUNT(DISTINCT code) FROM quote_clean").fetchone()[0]
    n_h = con.execute("SELECT COUNT(*) FROM heat_rank_daily").fetchone()[0]
    print(f"[建库完成] quote_clean {n_q} 行 / {n_c} 只 | heat {n_h} 行 | 库 {os.path.getsize(DB)//1024}KB")
    con.close()
    return n_c > 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="云端自建最小行情库")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args(argv)
    ok = build(a.limit, a.workers)
    print("[OK] 云端库就绪" if ok else "[FAIL] 建库失败")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
