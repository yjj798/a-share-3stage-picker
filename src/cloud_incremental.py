#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端当日增量：在精简种子基础上追加最新交易日，滚动保持窗口固定。

设计（轻量优先）：
  - 行情：东财 clist 一次请求拿全市场当日 OHLC + 昨收（不是逐只抓 5000 次）。
  - 热度：同花顺公开人气榜；拿不到全量时用东财成交额/涨幅排序做排名代理，
           只写当日一行，够评分用即可，不追历史深度。
  - 窗口：K线保留最近 60 个交易日、热度保留最近 20 个交易日，追加新的同时删最老的。
  - 纯标准库；评分只用到的字段写全，其余列留空。

用法（Actions）:
  python cloud_incremental.py                 # 抓当日并增量；周末/节假日休市自动跳过
本地验证（不联网）:
  python cloud_incremental.py --simulate      # 复制种子最后一天当"新交易日"，验证滚动逻辑

休市判定（不依赖人工维护节假日表）：
  1) 周六/周日：直接跳过，不联网；
  2) 工作日：抓一次东财快照后，用成交额前 20 活跃股比对库内最新交易日，
     若价格+成交量与库内最新日完全一致，说明数据源仍停在上一交易日
     （法定节假日/临时休市），判定为无新交易日 → 跳过写库。
  跳过时写 STOCKPOOL_SKIP_FLAG 标记文件，Actions 据此不重新生成页面、
  不产生空提交，页面保持上一交易日数据，开市后自然接续，不留空档。

环境变量:
  STOCKPOOL_DB          种子库路径（默认 engine/data/quant_v2.db）
  STOCKPOOL_SKIP_FLAG   休市跳过标记文件（可选，Actions 传入）
"""
from __future__ import annotations

import os
import sys
import json
import time
import urllib.request
import urllib.error
import datetime
import sqlite3
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB = os.environ.get("STOCKPOOL_DB", os.path.join(ROOT, "engine", "data", "quant_v2.db"))

KEEP_KLINE = 60   # K线交易日窗口
KEEP_HEAT = 20    # 热度交易日窗口

# 唯一权威写入列清单：to_quote_rows / apply_incremental / simulate 全部引用它。
# 顺序即 to_quote_rows 生成 tuple 的顺序，避免手写错位。
QCOLS = (
    "trade_date,code,name,board,is_st,is_bj,open,high,low,close,"
    "pre_close_raw,pre_close,pre_close_src,pct_recalc,pct_db,limit_pct,"
    "limit_up_price,limit_down_price,one_word_board,price_suspect,suspect_reason,"
    "band_override,adj_ratio,adj_cum,open_adj,high_adj,low_adj,close_adj,"
    "volume,amount,turnover_rate,circ_mv,data_source,turnover_est,turnover_src,"
    "circ_mv_est,vol_ma5"
).split(",")

HEAT_COLS = "trade_date,code,name,rank,src,src_date,aligned_date,align_rule,synced_at".split(",")

UA = {"User-Agent": "Mozilla/5.0 (compatible; stockpool-cloud/1.0)"}

# 东财全市场（沪深主板+创业板+科创板），一次拿全
EM_URL = (
    "https://82.push2.eastmoney.com/api/qt/clist/get"
    "?pn={pn}&pz=6000&po=1&np=1&fltt=2&invt=2&fid=f12"
    "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
    "&fields=f2,f3,f5,f6,f12,f14,f15,f16,f17,f18"
)
# 字段: f2现价 f3涨幅 f5成交量(手) f6成交额 f12代码 f14名称 f15最高 f16最低 f17今开 f18昨收


def board_of(code: str) -> tuple[str, int]:
    """返回 (board, is_bj)，与 clean_quote.board_of 同口径。"""
    if code[:1] in ("4", "8") or code[:3] == "920":
        return "北交所", 1
    if code[:2] in ("30",):
        return "创业板", 0
    if code[:3] in ("688", "689"):
        return "科创板", 0
    return "主板", 0


def is_st_name(name: str) -> int:
    n = (name or "").upper().replace(" ", "")
    return 1 if ("ST" in n or "退" in n) else 0


def num(v):
    """东财 '-' 或 None → None。"""
    if v in ("-", "", None):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def http_json(url: str, timeout: int = 30, referer: str | None = None):
    headers = dict(UA)
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def fetch_eastmoney_all() -> list[dict]:
    """一次（必要时分页）拿全市场当日快照。"""
    out: list[dict] = []
    pn = 1
    while True:
        d = http_json(EM_URL.format(pn=pn))
        diff = (d.get("data") or {}).get("diff") or []
        if not diff:
            break
        out.extend(diff)
        total = (d.get("data") or {}).get("total") or 0
        if len(out) >= total or len(diff) < 100:
            break
        pn += 1
        time.sleep(0.3)
    return out


def to_quote_rows(raw: list[dict], trade_date: str) -> list[tuple]:
    """东财快照 → quote_clean 行（只填评分需要的列，其余默认）。"""
    rows = []
    for x in raw:
        code = str(x.get("f12") or "").strip()
        if len(code) != 6 or not code.isdigit():
            continue
        close = num(x.get("f2"))
        pre = num(x.get("f18"))
        if close is None or close <= 0:
            continue  # 停牌/无报价
        board, is_bj = board_of(code)
        name = str(x.get("f14") or "")
        pct = num(x.get("f3"))
        if pct is None and pre and pre > 0:
            pct = round((close - pre) / pre * 100, 3)
        pct_recalc = round(pct, 3) if pct is not None else None
        vol = num(x.get("f5"))       # 手
        amt = num(x.get("f6"))       # 元
        o, h, l = num(x.get("f17")), num(x.get("f15")), num(x.get("f16"))
        # 用 dict 按列名填值，再按 QCOLS 顺序生成 tuple —— 列数/顺序绝不错位
        d = {
            "trade_date": trade_date, "code": code, "name": name,
            "board": board, "is_st": is_st_name(name), "is_bj": is_bj,
            "open": o, "high": h, "low": l, "close": close,
            "pre_close_raw": pre, "pre_close": pre, "pre_close_src": "em_realtime",
            "pct_recalc": pct_recalc, "pct_db": pct,
            "limit_pct": None, "limit_up_price": None, "limit_down_price": None,
            "one_word_board": None, "price_suspect": None, "suspect_reason": None,
            "band_override": None, "adj_ratio": 1.0, "adj_cum": 1.0,
            "open_adj": o, "high_adj": h, "low_adj": l, "close_adj": close,
            "volume": vol, "amount": amt, "turnover_rate": None, "circ_mv": None,
            "data_source": "em_realtime", "turnover_est": None, "turnover_src": None,
            "circ_mv_est": None, "vol_ma5": None,
        }
        rows.append(tuple(d[c] for c in QCOLS))
    return rows


def fetch_heat_proxy(trade_date: str, em_raw: list[dict]) -> list[tuple]:
    """构造当日热度排名：优先同花顺公开人气榜，失败则用东财成交额排序代理。

    只写一行当日快照，够 heat_score_calc(近10条 + min) 用即可，不追历史深度。
    """
    ranked: list[tuple[str, int]] = []  # (code, rank)
    try:
        d = http_json(
            "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
            "?stock_type=a&type=hour&list_type=normal",
            referer="https://data.10jqka.com.cn/",
        )
        data = d.get("data")
        lst = data if isinstance(data, list) else (data.get("list") if isinstance(data, dict) else [])
        for i, x in enumerate(lst[:200], 1):
            code = str(x.get("code") or x.get("stock_code") or "").strip()
            if len(code) == 6 and code.isdigit():
                ranked.append((code, i))
        src = "ths_hot_public"
    except Exception as e:
        print(f"[热度] 同花顺公开榜不可用({type(e).__name__})，改用东财成交额代理")
        # 成交额降序作人气代理（真实成交活跃度，非编造）
        cands = []
        for x in em_raw:
            code = str(x.get("f12") or "")
            amt = num(x.get("f6")) or 0
            if len(code) == 6 and code.isdigit():
                cands.append((code, amt))
        cands.sort(key=lambda t: t[1], reverse=True)
        ranked = [(c, i) for i, (c, _) in enumerate(cands[:2000], 1)]
        src = "em_amount_proxy"

    if not ranked:
        return []
    return [(trade_date, code, "", rank, src, trade_date, trade_date, "identity", "") for code, rank in ranked]


def latest_dates(con) -> tuple[str | None, str | None]:
    q = con.execute("SELECT MAX(trade_date) FROM quote_clean").fetchone()[0]
    h = con.execute("SELECT MAX(trade_date) FROM heat_rank_daily").fetchone()[0]
    return q, h


def snapshot_is_stale(con, qmax: str | None, em_raw: list[dict], sample: int = 30) -> bool:
    """判断东财快照是否仍是库内最新交易日的数据（即今日休市、无新交易日）。

    东财 clist 不直接返回交易日期，但休市日它展示的就是上一交易日收盘快照。
    取成交额最大的若干活跃股，把快照收盘价同库内 qmax 那天逐只比对：
    多数一致 => 快照未前进 => 视为休市。只比价不比量：种子与实时接口成交量
    口径未必一致，拿量比对反而可能把休市日误判为交易日而写入假数据；
    活跃股 30 只收盘价全部与昨日相同的概率极低，比价已足够稳。
    """
    if not qmax or not em_raw:
        return False
    # 按成交额取前 sample 只活跃股：(amount, code, close)
    cands = []
    for x in em_raw:
        code = str(x.get("f12") or "")
        if len(code) != 6 or not code.isdigit():
            continue
        amt = num(x.get("f6")) or 0.0
        cands.append((amt, code, num(x.get("f2"))))
    cands.sort(key=lambda t: t[0], reverse=True)
    cands = cands[:sample]
    if not cands:
        return False

    # 库内 qmax 那天：code -> close
    codes = [c[1] for c in cands]
    ph = ",".join("?" * len(codes))
    rows = con.execute(
        f"SELECT code, close FROM quote_clean "
        f"WHERE trade_date=? AND code IN ({ph})", [qmax] + codes).fetchall()
    db = {r[0]: r[1] for r in rows if r[1] is not None}
    if not db:
        return False  # 库里没对上（种子结构不同）时不敢误判休市，照常更新

    def close_enough(a, b):
        if a is None or b is None:
            return False
        return abs(a - b) <= max(0.01, abs(b) * 0.001)  # 价差≤0.1% 视为同一收盘

    same = 0
    compared = 0
    for _amt, code, close in cands:
        if code not in db:
            continue
        compared += 1
        if close_enough(close, db[code]):
            same += 1
    if compared < 5:
        return False
    ratio = same / compared
    print(f"[休市判定] 活跃股样本 {compared} 只，与库内 {qmax} 一致 {same} 只 ({ratio:.0%})")
    return ratio >= 0.8


def write_skip_flag(reason: str):
    flag = os.environ.get("STOCKPOOL_SKIP_FLAG")
    if not flag:
        return
    try:
        with open(flag, "w", encoding="utf-8") as f:
            f.write(reason)
    except OSError as e:
        print(f"[警告] 无法写跳过标记 {flag}: {e}")


def skip_and_exit(con, reason: str):
    print(f"[跳过] {reason}")
    write_skip_flag(reason)
    con.close()
    sys.exit(0)


def trade_days(con, table: str, n: int) -> list[str]:
    col = "trade_date"
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT {col} FROM {table} ORDER BY {col} DESC LIMIT ?", (n,))]


def apply_incremental(con, trade_date: str, quote_rows: list[tuple], heat_rows: list[tuple]):
    """追加新交易日并滚动裁剪窗口。返回统计。"""
    stat = {"insert_quote": 0, "insert_heat": 0, "del_quote_dates": [], "del_heat_dates": []}

    # 幂等：若该交易日已有行情，先删（重跑不叠加）
    con.execute("DELETE FROM quote_clean WHERE trade_date=?", (trade_date,))
    ph = ",".join("?" * len(QCOLS))
    con.executemany(f"INSERT INTO quote_clean ({','.join(QCOLS)}) VALUES ({ph})", quote_rows)
    stat["insert_quote"] = len(quote_rows)

    if heat_rows:
        con.execute("DELETE FROM heat_rank_daily WHERE trade_date=?", (trade_date,))
        hph = ",".join("?" * len(HEAT_COLS))
        con.executemany(f"INSERT INTO heat_rank_daily ({','.join(HEAT_COLS)}) VALUES ({hph})", heat_rows)
        stat["insert_heat"] = len(heat_rows)

    # 滚动：K线保留最近 KEEP_KLINE 个交易日
    days = trade_days(con, "quote_clean", KEEP_KLINE + 50)
    for old in days[KEEP_KLINE:]:
        con.execute("DELETE FROM quote_clean WHERE trade_date=?", (old,))
        stat["del_quote_dates"].append(old)

    # 滚动：热度保留最近 KEEP_HEAT 个交易日
    hdays = trade_days(con, "heat_rank_daily", KEEP_HEAT + 50)
    for old in hdays[KEEP_HEAT:]:
        con.execute("DELETE FROM heat_rank_daily WHERE trade_date=?", (old,))
        stat["del_heat_dates"].append(old)

    con.commit()
    stat["quote_window"] = trade_days(con, "quote_clean", KEEP_KLINE)
    stat["heat_window"] = trade_days(con, "heat_rank_daily", KEEP_HEAT)
    return stat


def simulate(con):
    """本地验证：复制种子最后一天行情/热度，改日期当'新交易日'，验证滚动。"""
    qmax, hmax = latest_dates(con)
    assert qmax, "种子库无行情"
    new_date = (datetime.datetime.strptime(qmax, "%Y%m%d") + datetime.timedelta(days=1)).strftime("%Y%m%d")

    # 用最后一天行情整体作为"新交易日"快照（按权威列清单取，保证列数一致）
    rows = con.execute(f"SELECT {','.join(QCOLS)} FROM quote_clean WHERE trade_date=?", (qmax,)).fetchall()
    quote_rows = [(new_date,) + r[1:] for r in rows]
    # 热度：复制最近热度日
    hrows = con.execute(f"SELECT {','.join(HEAT_COLS)} FROM heat_rank_daily WHERE trade_date=?",
                        (hmax,)).fetchall() if hmax else []
    heat_rows = [(new_date,) + r[1:] for r in hrows]

    print(f"[模拟] 源交易日={qmax} 热度日={hmax} → 新交易日={new_date}")
    print(f"[模拟] 构造 {len(quote_rows)} 行行情 / {len(heat_rows)} 行热度")
    st = apply_incremental(con, new_date, quote_rows, heat_rows)
    print(f"[模拟] 写入行情{st['insert_quote']} 热度{st['insert_heat']}")
    print(f"[模拟] 删除超窗口行情日 {st['del_quote_dates']}")
    print(f"[模拟] 删除超窗口热度日 {st['del_heat_dates']}")
    print(f"[模拟] 行情窗口 {st['quote_window'][0]}~{st['quote_window'][-1]} 共{len(st['quote_window'])}日")
    print(f"[模拟] 热度窗口 {st['heat_window'][0]}~{st['heat_window'][-1]} 共{len(st['heat_window'])}日")
    return st


def main():
    simulate_mode = "--simulate" in sys.argv
    con = sqlite3.connect(DB)
    qmax, hmax = latest_dates(con)
    print(f"[种子] {DB} | 行情最新={qmax} 热度最新={hmax}")

    if simulate_mode:
        st = simulate(con)
        ok = (len(st["quote_window"]) <= KEEP_KLINE and len(st["heat_window"]) <= KEEP_HEAT)
        print("SIM_OK" if ok else "SIM_FAIL")
        con.close()
        sys.exit(0 if ok else 1)

    # ① 周末：直接跳过，不联网（cron 已只排工作日，此处为手动触发兜底）
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    if now.weekday() >= 5:
        wd = "六日"[now.weekday() - 5]
        skip_and_exit(con, f"今日 {now:%Y-%m-%d} 为周{wd}，A股休市，无新交易日")

    trade_date = now.strftime("%Y%m%d")
    if qmax and trade_date <= qmax:
        skip_and_exit(con, f"当日 {trade_date} <= 库内最新 {qmax}，数据未前进")

    # ② 抓一次东财快照；节假日休市时它返回的仍是上一交易日收盘数据
    print(f"[抓取] 东财全市场当日 {trade_date} ...")
    em = fetch_eastmoney_all()
    print(f"[抓取] 东财返回 {len(em)} 只")

    # ③ 法定节假日/临时休市判定：快照与库内最新日一致 => 无新交易日，跳过写库
    if snapshot_is_stale(con, qmax, em):
        skip_and_exit(con, f"{trade_date} 为节假日休市（行情快照仍停在 {qmax}），无新交易日，不改库")

    quote_rows = to_quote_rows(em, trade_date)
    print(f"[构造] 有效行情 {len(quote_rows)} 行")
    if len(quote_rows) < 1000:
        print("[中止] 行情不足1000行，疑似数据源异常，不改库")
        con.close()
        sys.exit(1)

    heat_rows = fetch_heat_proxy(trade_date, em)
    print(f"[构造] 热度 {len(heat_rows)} 行")

    st = apply_incremental(con, trade_date, quote_rows, heat_rows)
    print(f"[完成] 写入行情{st['insert_quote']} 热度{st['insert_heat']}")
    print(f"[完成] 行情窗口 {st['quote_window'][0]}~{st['quote_window'][-1]} ({len(st['quote_window'])}日)")
    print(f"[完成] 热度窗口 {st['heat_window'][0]}~{st['heat_window'][-1]} ({len(st['heat_window'])}日)")
    con.close()


if __name__ == "__main__":
    main()
