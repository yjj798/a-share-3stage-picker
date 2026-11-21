# -*- coding: utf-8 -*-
"""fetch_quote.py — V2 自有行情抓取（唯一行情源：同花顺）。

数据通道：
    同花顺  https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js
    一次请求返回 JSONP，data 字段分号分隔的日线，字段序：
        日期, 开, 高, 低, 收, 成交量(股), 成交额, 换手率, ...
    本模块转成内部统一格式 [YYYYMMDD, open, close, high, low, volume(手)]，
    volume 从「股」除以 100 折算成「手」，与下游 clean_quote 口径一致。

写入 `daily_quote_src`（V2 自有表，schema 与 clean_quote 消费列对齐），
**不写** quote_clean —— 清洗/涨停价/连板/复权全部仍由 clean_quote.py 重算。

用法：
    python ingest/fetch_quote.py --dry-run
    python ingest/fetch_quote.py
    python ingest/fetch_quote.py --dates 20261120,20261121
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                        # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "quant_v2.db"

THS_KLINE = "https://d.10jqka.com.cn/v6/line/hs_{code}/01/last.js"
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://stockpage.10jqka.com.cn/"}
BARS = 15          # 一次取 15 根日线，够覆盖多日回补 + pre_close 锚

DDL = """
CREATE TABLE IF NOT EXISTS daily_quote_src (
    trade_date      TEXT NOT NULL,
    code            TEXT NOT NULL,
    name            TEXT,
    pre_close       REAL,
    is_st           INTEGER,
    is_bj           INTEGER,
    open            REAL,
    high            REAL,
    low             REAL,
    close           REAL,
    pct_chg         REAL,
    volume          REAL,          -- 手
    amount          REAL,          -- 估算
    turnover_rate   REAL,          -- 估算（股本不变推算）
    circ_mv         REAL,          -- 估算（股本不变推算）
    data_source     TEXT,
    ingested_at     TEXT,
    PRIMARY KEY (code, trade_date)
);
"""


def _fetch_bars(code: str, tries: int = 2) -> tuple[list, str]:
    """同花顺日线 → ([[date, open, close, high, low, volume(手)], ...], name)。"""
    url = THS_KLINE.format(code=code)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                txt = r.read().decode("utf-8", "ignore")
            a, b = txt.find("("), txt.rfind(")")
            if a < 0 or b <= a:
                raise ValueError("bad jsonp")
            d = json.loads(txt[a + 1:b])
            raw = d.get("data") or ""
            out = []
            for row in raw.split(";"):
                p = row.split(",")
                if len(p) < 6:
                    continue
                day = p[0].replace("-", "")
                if len(day) != 8 or not day.isdigit():
                    continue
                out.append([day, float(p[1]), float(p[4]), float(p[2]),
                            float(p[3]), float(p[5]) / 100.0])
            if not out:
                raise ValueError("empty bars")
            return out[-BARS:], str(d.get("name") or "")
        except Exception:                                # noqa: BLE001
            if i == tries - 1:
                return [], ""
            time.sleep(0.3)
    return [], ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="同花顺日线抓取 → daily_quote_src")
    ap.add_argument("--dates", help="逗号分隔 YYYYMMDD；缺省=基准日后所有缺失交易日")
    ap.add_argument("--base-date", help="指定基准截面日")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    cur.executescript(DDL)

    base_date = a.base_date or cur.execute("SELECT MAX(trade_date) FROM quote_clean").fetchone()[0]
    if not base_date:
        print("[FATAL] quote_clean 为空，先跑全量建库")
        return 2
    known = {r[0] for r in cur.execute("SELECT DISTINCT trade_date FROM quote_clean")}

    if a.dates:
        want = [d.strip() for d in a.dates.split(",") if d.strip()]
    else:
        want = []
        d = dt.datetime.strptime(base_date, "%Y%m%d").date() + dt.timedelta(days=1)
        end = dt.date.today()
        while d <= end:
            ds = d.strftime("%Y%m%d")
            if d.weekday() < 5 and ds not in known:
                want.append(ds)
            d += dt.timedelta(days=1)
    if not want:
        print(f"[OK] 无需补数：quote_clean 最新 {base_date}，无缺失交易日")
        return 0
    print(f"基准日 {base_date} → 目标 {','.join(want)}")

    base = {r[0]: r for r in cur.execute(
        "SELECT code,name,is_st,is_bj,close,"
        "COALESCE(turnover_rate, turnover_est),COALESCE(circ_mv, circ_mv_est) "
        "FROM quote_clean WHERE trade_date=?", (base_date,))}
    print(f"基准截面 {len(base)} 只")
    want_set = set(want)
    codes = list(base)

    hits, done = {}, [0]

    def work(c):
        bars, name = _fetch_bars(c)
        done[0] += 1
        if done[0] % 800 == 0:
            print(f"  取数进度 {done[0]}/{len(codes)}（命中 {len(hits)} 只）", flush=True)
        return c, bars, name

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for c, bars, name in ex.map(work, codes):
            if bars:
                hits[c] = (bars, name)
    print(f"取数完成：{len(hits)}/{len(codes)} 只命中，用时 {time.time()-t0:.1f}s")

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stat = {d: 0 for d in want}
    n_ins = 0
    for code, (bars, ths_name) in hits.items():
        _, name, is_st, is_bj, b_close, b_tor, b_cmv = base[code]
        name = name or ths_name
        if not b_close:
            continue
        circ_shares = (b_cmv / b_close) if (b_cmv and b_close) else None
        prev_close = None
        for b in bars:
            d = b[0]
            if d > base_date and d in want_set:
                o, c, h, lo, vol = b[1], b[2], b[3], b[4], b[5]
                pre = prev_close if prev_close else b_close
                if not pre or not c:
                    continue
                pct = (c - pre) / pre * 100
                amt = (h + lo + c) / 3.0 * vol * 100.0        # 估算
                tr = (vol * 100 / circ_shares * 100) if circ_shares else None
                cmv = (c * circ_shares) if circ_shares else None
                stat[d] += 1
                n_ins += 1
                if a.dry_run:
                    continue
                cur.execute(
                    "INSERT OR REPLACE INTO daily_quote_src VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d, code, name, pre, is_st, is_bj, o, h, lo, c,
                     round(pct, 4), vol, round(amt, 2),
                     round(tr, 4) if tr is not None else None,
                     round(cmv, 2) if cmv is not None else None,
                     "ths_day", now))
            prev_close = b[2]
    if not a.dry_run:
        con.commit()
    for d in want:
        print(f"  {d}: {stat[d]} 行")
    print("[dry-run] 未写库" if a.dry_run else f"写库完成 {n_ins} 行 → daily_quote_src")
    miss = len(codes) - len(hits)
    if miss:
        print(f"未命中 {miss} 只（停牌/退市/接口无此票，不影响清洗）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
