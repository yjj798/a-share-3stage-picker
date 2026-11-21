# -*- coding: utf-8 -*-
"""fetch_quote.py — V2 自有行情抓取（P6 / TODO#48 补齐，2026-11-10 落地）。

背景：旧仓 zt-quant 2026-11-08 归档只读后，`ingest/clean_quote.py` 的上游
`src.daily_quote` 不再有新增 → quote_clean / limit_up_day_v2 / 评分全链停在
20261106，页面静默出过期数据。本模块把旧仓 `tools/quote_fill_tencent.py`
的腾讯/新浪日线通道迁入 V2，行情生产自给。

数据通道（与旧工具同口径，实测可用）：
    腾讯  https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,{n},
    新浪  https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/
          CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}
    逐股一次、线程池并发；腾讯短时间 5500+ 请求会触发 WAF 风控页
    （容易误判成「没这只股票」），空结果自动切新浪重试。

写入 `daily_quote_src`（V2 自有表，schema 与 clean_quote 消费列对齐），
**不写** quote_clean —— 清洗/涨停价/连板/复权全部仍由 `clean_quote.py`
重算，本表只是"原始日线暂存"，口径单一不会分叉。

诚实声明（别当实测值用）：
    · 开高低收/成交量 = 腾讯或新浪原始值（volume 单位「手」，新浪返回「股」已 ÷100）；
    · 成交额是估算 ((high+low+close)/3)×volume×100（两源日线都不给成交额），
      data_source 标 `tencent_day_est` / `sina_day_est` 可区分；
    · 换手/流通市值按「股本不变」从基准日（库内最后有数据日）推算。

用法：
    python ingest/fetch_quote.py --dry-run          # 看会补哪些日期
    python ingest/fetch_quote.py                    # 补基准日之后所有缺失交易日
    python ingest/fetch_quote.py --dates 20261109,20261110
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

TENCENT_URL = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
               "?param={sym},day,,,{n},")
SINA_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}")
UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
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


def _sym(code: str, is_bj: bool) -> str:
    if is_bj or code.startswith(("4", "8", "9")):
        return "bj" + code
    return ("sh" if code.startswith("6") else "sz") + code


def _fetch_bars(sym: str, source: str, tries: int = 2) -> list:
    """→ [[YYYYMMDD, open, close, high, low, volume(手)], ...] 原始序。"""
    url = (TENCENT_URL.format(sym=sym, n=BARS) if source == "tencent"
           else SINA_URL.format(sym=sym, n=BARS))
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
            if source == "tencent":
                node = (body.get("data") or {}).get(sym) or {}
                bars = node.get("day") or node.get("qfqday") or []
                # 腾讯顺序：日期, 开, 收, 高, 低, 量(手)
                return [[str(b[0]).replace("-", ""), float(b[1]), float(b[2]),
                         float(b[3]), float(b[4]), float(b[5])] for b in bars]
            # 新浪 volume 单位是「股」，÷100 折手
            return [[str(b.get("day", "")).replace("-", ""), float(b["open"]),
                     float(b["close"]), float(b["high"]), float(b["low"]),
                     float(b["volume"]) / 100.0] for b in (body or [])]
        except Exception:                                # noqa: BLE001
            if i == tries - 1:
                return []
            time.sleep(0.3)
    return []


def _fetch_both(sym: str, primary: str = "sina") -> tuple[list, str]:
    """primary 优先，空/异常切另一源。→ (bars, source)。

    2026-11-10 实测：本机出口对腾讯 fqkline 连发几千请求会被 WAF 拦 501 风控页
    （可能持续一段时间），新浪逐股 0.1s 级且含最新交易日 ⇒ 默认 sina 优先。
    """
    other = "tencent" if primary == "sina" else "sina"
    for src in (primary, other):
        bars = _fetch_bars(sym, src)
        if bars:
            return bars, src
    return [], ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V2 行情日线抓取 → daily_quote_src")
    ap.add_argument("--dates", help="逗号分隔 YYYYMMDD；缺省=基准日后所有缺失交易日")
    ap.add_argument("--base-date", help="指定基准截面日（基准日恰好残缺时用它取全市场股票列表）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--source", default="sina", choices=("sina", "tencent"),
                    help="主源；空结果自动切另一源")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    cur.executescript(DDL)

    base_date = a.base_date or cur.execute("SELECT MAX(trade_date) FROM quote_clean").fetchone()[0]
    if not base_date:
        print("[FATAL] quote_clean 为空，先跑全量 clean_quote")
        return 2
    # 库内已有的交易日序列（幂等：已清洗进 quote_clean 的日子不重复补）
    known = {r[0] for r in cur.execute("SELECT DISTINCT trade_date FROM quote_clean")}

    if a.dates:
        want = [d.strip() for d in a.dates.split(",") if d.strip()]
    else:
        # 基准日之后、今天及之前的所有工作日（节假日无 bar，抓不到自然落空）
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

    # 基准日截面：名称/ST/北交/收盘/换手/流通市值（换手与市值按股本不变推算）
    base = {r[0]: r for r in cur.execute(
        "SELECT code,name,is_st,is_bj,close,"
        "COALESCE(turnover_rate, turnover_est),COALESCE(circ_mv, circ_mv_est) "
        "FROM quote_clean WHERE trade_date=?", (base_date,))}
    print(f"基准截面 {len(base)} 只")
    want_set = set(want)
    codes = list(base)

    hits: dict[str, tuple] = {}
    done = [0]

    def work(c):
        r = base[c]
        bars, src = _fetch_both(_sym(c, bool(r[3])), a.source)
        done[0] += 1
        if done[0] % 800 == 0:
            print(f"  取数进度 {done[0]}/{len(codes)}（命中 {len(hits)} 只）", flush=True)
        return c, bars, src

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for c, bars, src in ex.map(work, codes):
            if bars:
                hits[c] = (bars, src)
    print(f"取数完成：{len(hits)}/{len(codes)} 只命中，用时 {time.time()-t0:.1f}s")

    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stat = {d: 0 for d in want}
    n_ins = 0
    for code, (bars, src) in hits.items():
        _, name, is_st, is_bj, b_close, b_tor, b_cmv = base[code]
        if not b_close:
            continue
        circ_shares = (b_cmv / b_close) if (b_cmv and b_close) else None
        # 逐 bar：pre_close 用 kline 里前一根的 close（第一根目标日用基准日收盘兜底）
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
                     f"{src}_day_est", now))
            prev_close = b[2]
    if not a.dry_run:
        con.commit()
    for d in want:
        print(f"  {d}: {stat[d]} 行")
    print("[dry-run] 未写库" if a.dry_run else f"写库完成 {n_ins} 行 → daily_quote_src")
    # 没命中的票：留痕（多半是停牌/退市，下次基准日截面自然淘汰）
    miss = len(codes) - len(hits)
    if miss:
        print(f"未命中 {miss} 只（停牌/退市/源无此票，不影响清洗）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
