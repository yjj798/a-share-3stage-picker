"""P1-4 涨停名单日更链路：quote_clean → limit_up_day_v2（含连板数、板型、可买性、涨停原因）。

为什么不用旧的 limit_up_day 直接续写：
  旧表停在 20261026，且依赖同花顺涨停池抓取（首封时间 / 封单额 / 涨停原因）。
  抓取器挂一天，名单就断一天，而「今天几只涨停」是复盘引擎最基础的输入。
  本链路以**清洗后的行情**为准，自己算得出名单，抓取成功时再用平台字段增强（reason/seal/first_time）。

三个来源，优先级明确：
  derived  : quote_clean 可独立算出的（close/pct/amount/board_type/连板数/一字板）
  enriched : 8888 快照（reason/seal_amount/first_time，仅 11-07/11-08 两天）
           → 同花顺涨停池 API（按日期可回，reason_type + 首封时间；见 pool_enrich）
           → legacy.limit_up_day（20250901~20261026，带封单额）

为什么必须有 pool_enrich（2026-11-13 补）：
  legacy 源停在 20261026，之后 reason_raw 全空 13 个交易日；
  另一条替代路径 ingest/fetch_limit_up_iwencai.py 依赖 pandas，本机未装 ⇒ 日更链里跑不起来。
  实测 dataapi/limit_up/limit_up_pool 带 date 参数可返回历史（20261112 返 37 只，与库内 35 一致），
  纯 urllib 即可，是「每天都能拿到涨停原因」的唯一稳定路径。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DST_DB = ROOT / "data" / "quant_v2.db"
SNAP_DIR = ROOT / "raw" / "legacy_snap"      # 旧仓 v1 快照已随并库迁入（原 D:/AI/8888/data）

# 同花顺涨停池（data 站 dataapi，无需登录）。field 串抄自 ingest/fetch_limit_up_realtime.py，
# 少一个 id 就少一列，别自己拼。
POOL_API = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
POOL_FIELDS = ("199112,10,9001,330323,330324,330325,9002,330329,"
               "133971,133970,1968584,3475914,9003,9004")
POOL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html",
}

DDL = """
CREATE TABLE IF NOT EXISTS limit_up_day_v2 (
    trade_date      TEXT NOT NULL,
    code            TEXT NOT NULL,
    name            TEXT,
    close           REAL,
    pct_chg         REAL,
    amount          REAL,
    turnover_rate   REAL,
    circ_mv         REAL,
    board_type      TEXT,      -- 主板 / 创业板 / 科创板 / 北交所 / ST板
    limit_pct       REAL,
    consec_boards   INTEGER,   -- 连续涨停天数（含当日）
    is_first_board  INTEGER,
    one_word_board  INTEGER,
    is_tradable     INTEGER,   -- 0 = 一字板，当日买不进
    seal_amount     REAL,      -- 封单额（平台增强，缺则空）
    first_time      TEXT,      -- 首封时间（平台增强）
    reason_raw      TEXT,      -- 涨停原因原文（平台增强）
    reason_src      TEXT,
    price_suspect   INTEGER,
    src_kind        TEXT,      -- derived | enriched
    created_at      TEXT,
    PRIMARY KEY (trade_date, code)
);
CREATE INDEX IF NOT EXISTS ix_ludv2_date ON limit_up_day_v2(trade_date);
"""


def board_type(lim: float, is_st: int | None, board: str) -> str:
    if board == "北交所":
        return "北交所"
    if lim >= 0.20:
        return "创业板" if board == "创业板" else "科创板"
    if lim <= 0.05:
        return "ST板"
    return "主板"


def _pool_get(params, timeout=20):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(POOL_API + "?" + q, headers=POOL_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _hhmmss(ts):
    """同花顺首封时间是 unix 秒（如 "1794447024"）→ "HH:MM:SS"。按东八区，不看本机 TZ。"""
    try:
        v = int(float(ts))
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return dt.datetime.fromtimestamp(v + 8 * 3600, dt.timezone.utc).strftime("%H:%M:%S")


def pool_enrich(date, retries=2):
    """同花顺涨停池按日期取 {code: {reason, first_seal, limit_type}}。

    失败一律返已取到的部分，不抛异常 —— 日更不能因为一个增强源挂掉。
    """
    out = {}
    page, limit = 1, 100
    while page <= 20:
        params = {"page": page, "limit": limit, "field": POOL_FIELDS,
                  "filter": "HS,GEM2STAR", "order_field": "330324",
                  "order_type": "0", "date": date}
        data = None
        for attempt in range(retries + 1):
            try:
                data = _pool_get(params)
                break
            except Exception as e:                       # noqa: BLE001
                if attempt == retries:
                    print("  ⚠ 涨停池 %s 第 %d 页失败，已取 %d 条：%s"
                          % (date, page, len(out), str(e)[:60]))
                    return out
        inner = (data or {}).get("data") or {}
        items = inner.get("info") or []
        for it in items:
            code = str(it.get("code") or "").strip()
            if not code:
                continue
            out[code] = {"reason": (it.get("reason_type") or "").strip() or None,
                         "first_seal": _hhmmss(it.get("first_limit_up_time")),
                         "limit_type": (it.get("limit_up_type") or "").strip() or None}
        if len(items) < limit:
            break
        page += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", nargs="*", help="指定交易日；缺省 = 全量重算最近 30 个交易日")
    ap.add_argument("--all", action="store_true", help="全量重算所有日期")
    ap.add_argument("--dst", default=str(DST_DB))
    a = ap.parse_args()

    con = sqlite3.connect(a.dst)
    cur = con.cursor()
    cur.executescript(DDL)
    now = dt.datetime.now().isoformat(timespec="seconds")

    all_dates = [r[0] for r in cur.execute(
        "select distinct trade_date from quote_clean order by trade_date")]
    if a.dates:
        dates = a.dates
    elif a.all:
        dates = all_dates
    else:
        dates = all_dates[-30:]

    # 连板数：一次读全量涨停集合，逐票数连续段
    zt = {(d, c) for d, c in cur.execute(
        "select trade_date, code from quote_clean where is_limit_up=1")}

    def consec(code: str, date: str) -> int:
        i = dpos[date]
        n = 0
        while i >= 0 and (all_dates[i], code) in zt:
            n += 1
            i -= 1
        return n

    dpos = {d: i for i, d in enumerate(all_dates)}

    # 平台增强①：8888 快照（watch-<date>.json 的 zt_rows），只有 11-07/11-08 两天
    def enrich(date: str) -> dict[str, dict]:
        p = SNAP_DIR / f"watch-{date}.json"
        if not p.exists():
            return {}
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return {r["code"]: r for r in (d.get("zt_rows") or []) if r.get("code")}

    # 平台增强②：源库 limit_up_day（20250901~20261026，21,857 行，带 first_time/seal_amount）
    #   全历史回测的 `limit` 项（首封时间 + 封单额）只能靠它，否则 285 天里只有 30 天能算。
    # 本机 Python 的 SQLite 未编译 SQLITE_USE_URI，ATTACH 'file:...?mode=ro' 会报
    # "unable to open database"；改用普通路径挂载，脚本只 SELECT 不写，源库一字节不改。
    cur.execute("ATTACH DATABASE ? AS lsrc", ((ROOT / "data" / "legacy.db").as_posix(),))
    legacy_enrich: dict[str, dict[str, dict]] = {}
    for d, c, ft, sa, rr in cur.execute(
            "SELECT trade_date, code, first_time, seal_amount, reason_raw FROM lsrc.limit_up_day"):
        legacy_enrich.setdefault(d, {})[c] = {"first_seal": ft, "seal_amt": sa, "reason": rr}

    total = 0
    for date in dates:
        rows = cur.execute("""select code,name,close,pct_recalc,amount,
                                     COALESCE(turnover_rate, turnover_est),circ_mv,
                                     board,is_st,limit_pct,one_word_board,price_suspect
                              from quote_clean where trade_date=? and is_limit_up=1""",
                           (date,)).fetchall()
        en = enrich(date)
        legacy = legacy_enrich.get(date, {})
        # 已有值兜底：本脚本是「先 DELETE 当日再整批 INSERT」，
        # 若跳过抓取却没保留旧值，重跑一次就把原因清空（2026-11-13 实测踩到）。
        prev = {c: {"reason": rr, "first_seal": ft, "seal_amt": sa, "src": sc}
                for c, rr, ft, sa, sc in cur.execute(
                    "SELECT code, reason_raw, first_time, seal_amount, reason_src "
                    "FROM limit_up_day_v2 WHERE trade_date=?", (date,))}
        # legacy 覆盖不到的日子（20261027 起）走涨停池；legacy 有数据就不动历史口径。
        # 已有原因覆盖率 ≥90% 就跳过抓取，避免日更链每天重拉同样的历史页。
        pool = {}
        if not legacy:
            done, tot = cur.execute(
                "select sum(case when reason_raw<>'' then 1 else 0 end), count(*) "
                "from limit_up_day_v2 where trade_date=?", (date,)).fetchone() or (0, 0)
            if not tot or (done or 0) * 1.0 / tot < 0.9:
                pool = pool_enrich(date)
        out = []
        for (code, name, close, pct, amt, tor, cmv, board, is_st, lim,
             ow, sus) in rows:
            e = en.get(code)
            esrc = "8888_watch" if e else None
            if not e and code in pool:
                e, esrc = pool[code], "ths_pool"
            if not e and code in legacy:
                e, esrc = legacy[code], "ths_limit_up_day"
            if not e and code in prev and (prev[code].get("reason") or prev[code].get("seal_amt")):
                e, esrc = prev[code], prev[code].get("src") or "ths_pool"
            e = e or {}
            cb = consec(code, date)
            out.append((date, code, name, close, pct, amt, tor, cmv,
                        board_type(lim or 0, is_st, board), lim, cb,
                        1 if cb <= 1 else 0, ow, 0 if ow else 1,
                        e.get("seal_amt"), e.get("first_seal"), e.get("reason"),
                        esrc, sus,
                        "enriched" if e else "derived", now))
        cur.execute("DELETE FROM limit_up_day_v2 WHERE trade_date=?", (date,))
        cur.executemany("INSERT OR REPLACE INTO limit_up_day_v2 VALUES "
                        f"({','.join('?' * 21)})", out)
        total += len(out)

    con.commit()
    cur.execute("INSERT OR REPLACE INTO meta_v2 VALUES (?,?)",
                ("limit_up_day_v2.built_at", now))
    con.commit()

    print(f"limit_up_day_v2 写入 {total} 行（{len(dates)} 个交易日，{dates[0]}~{dates[-1]}）")
    for r in cur.execute("""select trade_date, count(*),
                                   sum(is_first_board), max(consec_boards),
                                   sum(one_word_board),
                                   sum(case when src_kind='enriched' then 1 else 0 end)
                            from limit_up_day_v2 where trade_date>=? group by 1
                            order by 1 desc limit 12""", (dates[0],)):
        print(f"  {r[0]}  涨停 {r[1]:3d}  首板 {r[2]:3d}  最高 {r[3]} 板  "
              f"一字 {r[4]}  平台增强 {r[5]}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
