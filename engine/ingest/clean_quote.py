"""P1-1 行情清洗：重建 pre_close / 涨停价 / is_limit_up / 一字板 / price_suspect + 前复权因子。

设计要点（对应 SCHEMA §5.5 与 R5）：
  1. 库内 `limit_up_price` / `is_limit_up` / `pct_chg` 一律不信任，全部按交易所规则重算。
  2. `pre_close` 三种来源，逐一判定并留痕（pre_close_src）：
       - prev_close        : 与上一交易日 close 一致（>99% 的行）
       - prev_close_fixed  : 原始 pre_close 等于 T-2/T-3/T-5 的 close（＝行情商取错了日期，改回 T-1）
       - exdiv             : 与上一交易日 close 差 >0.5% 且不是取错日 → 判定为除权除息，保留原值并产出复权系数
  3. 前复权因子由除权系数连乘得到（无需外部复权数据）：
       ratio[t] = pre_close_exdiv[t] / close[t-1]
       adj_cum[t] = ∏ ratio[s]  (s > t)        → 以最新交易日为 1.0 的前复权锚
       close_adj[t] = close[t] * adj_cum[t]
  4. 涨跌幅超板块限幅 → price_suspect=1（不判一字板、不评 B/C 级，评级落 D）。

写入独立库 data/quant_v2.db，源库 data/legacy.db 只读挂载（ATTACH），原库一个字节不改。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 2026-11-10 并库：原读 D:/ai/zt-quant/data/quant.db（旧仓已归档）。改为本仓快照
# data/legacy.db（由 tools/build_legacy_db.py 从旧仓整表复制：daily_quote 等 5 表）。
SRC_DB = ROOT / "data" / "legacy.db"
DST_DB = ROOT / "data" / "quant_v2.db"

ADJ_GAP = 0.005          # 0.5% 以上视为缺口（正常日间只有跳空，不会改 pre_close）
WRONGDAY_TOL = 0.011     # 取错日判定容差（1 分钱）
BAND_TOL = 0.003         # 超限幅容差 0.3 个百分点
PRICE_TOL = 0.005        # 涨停价比对容差（半分钱）

DDL = """
CREATE TABLE IF NOT EXISTS adj_factor_daily (
    code        TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    ratio       REAL NOT NULL,      -- pre_close_exdiv / 上一交易日 close
    kind        TEXT NOT NULL,      -- exdiv | unclassified
    PRIMARY KEY (code, trade_date)
);

CREATE TABLE IF NOT EXISTS quote_clean (
    trade_date      TEXT NOT NULL,
    code            TEXT NOT NULL,
    name            TEXT,
    board           TEXT,           -- 主板 / 创业板 / 科创板 / 北交所
    is_st           INTEGER,
    is_bj           INTEGER,
    open            REAL, high REAL, low REAL, close REAL,
    pre_close_raw   REAL,           -- 库里原值（留痕）
    pre_close       REAL,           -- 清洗后（重算基准）
    pre_close_src   TEXT,           -- prev_close | prev_close_fixed | exdiv | first
    pct_recalc      REAL,           -- 按清洗后 pre_close 重算
    pct_db          REAL,           -- 库里原值（留痕）
    limit_pct       REAL,
    limit_up_price  REAL,
    limit_down_price REAL,
    is_limit_up     INTEGER,
    is_limit_down   INTEGER,
    one_word_board  INTEGER,
    price_suspect   INTEGER,
    suspect_reason  TEXT,
    band_override   TEXT,           -- 非空 = 限幅换档（摘帽/戴帽），如 "10%"
    adj_ratio       REAL,           -- 当日除权系数（无除权 = 1）
    adj_cum         REAL,           -- 前复权累计因子（最新日 = 1）
    open_adj REAL, high_adj REAL, low_adj REAL, close_adj REAL,
    volume          REAL,
    amount          REAL,
    turnover_rate   REAL,
    circ_mv         REAL,
    data_source     TEXT,
    PRIMARY KEY (trade_date, code)
);
CREATE INDEX IF NOT EXISTS ix_qc_code ON quote_clean(code, trade_date);
CREATE INDEX IF NOT EXISTS ix_qc_date ON quote_clean(trade_date);

CREATE TABLE IF NOT EXISTS meta_v2 (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def board_of(code: str, is_bj: int | None) -> str:
    if is_bj or code[:1] in ("4", "8") or code[:3] == "920":
        return "北交所"
    if code[:3] in ("300", "301"):
        return "创业板"
    if code[:3] in ("688", "689"):
        return "科创板"
    if code[:1] in ("6", "0"):
        return "主板"
    return "主板"


def limit_pct_of(code: str, board: str, is_st: int | None) -> float:
    if board == "北交所":
        return 0.30
    if board in ("创业板", "科创板"):
        return 0.20          # 创业/科创 ST 仍是 20%
    return 0.05 if is_st else 0.10


def _f(v):
    return float(v) if v not in (None, "") else None


def price_tick(x: float) -> float:
    """按交易所规则四舍五入到分。

    不能用内置 round()：`round(5.005, 2)` 因浮点表示会得到 5.0，
    而交易所是四舍五入 → 5.01。差 1 分钱会让「收盘价 == 涨停价」判不成立，
    凭空漏掉一批涨停（实测漏 1~5 只/日）。
    """
    return float(Decimal(repr(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def clean(days: list[tuple], since: str) -> tuple[list[tuple], list[tuple]]:
    """单只票的全部日线 → (quote_clean 行, adj_factor 行)。"""
    out: list[tuple] = []
    adjs: list[tuple] = []
    prev_close: float | None = None
    recent: list[float] = []          # 最近 6 个 close，用于「取错日」判定
    ratios: list[float] = []          # 与 out 等长：当日除权系数

    for (td, code, name, board, is_st, is_bj, o, h, l, c, pre_raw,
         pct_db, vol, amt, tor, cmv, src) in days:
        o, h, l, c = _f(o), _f(h), _f(l), _f(c)
        pre_raw = _f(pre_raw)
        pc = None
        pc_src = "first"
        ratio = 1.0

        if prev_close and prev_close > 0 and c is not None:
            if pre_raw is None:
                pc, pc_src = prev_close, "prev_close"
            else:
                gap = abs(pre_raw / prev_close - 1)
                if gap <= ADJ_GAP:
                    pc, pc_src = prev_close, "prev_close"
                else:
                    # 取错日：原值恰好等于前 2~6 个交易日中的某个 close
                    hit = any(abs(pre_raw - x) <= WRONGDAY_TOL for x in recent[-5:])
                    if hit:
                        pc, pc_src = prev_close, "prev_close_fixed"
                    else:
                        pc, pc_src = pre_raw, "exdiv"
                        ratio = pre_raw / prev_close
        else:
            pc = pre_raw

        if ratio != 1.0:
            adjs.append((code, td, round(ratio, 8), "exdiv"))
        ratios.append(ratio)

        lim = limit_pct_of(code, board, is_st)
        lu = price_tick(pc * (1 + lim)) if pc and pc > 0 else None
        ld = price_tick(pc * (1 - lim)) if pc and pc > 0 else None

        pct = (c / pc - 1) * 100 if (pc and pc > 0 and c is not None) else None
        suspect = 0
        reason = ""
        band_override = ""
        if pc is None or pc <= 0:
            suspect, reason = 1, "no_pre_close"
        elif c is not None:
            # 判限幅用「价格」而不是「百分比」：pre_close 本身带四舍五入，
            # 低价股 1 分钱就能把涨幅推出 0.1 个百分点，用百分比会大面积误判。
            over = lu is not None and c > lu + PRICE_TOL
            under = ld is not None and c < ld - PRICE_TOL
            if over or under:
                # 摘帽/戴帽当日限幅会换档，ST 标记常滞后一天。判据：收盘价是否
                # 精确等于另一档涨跌停价（分位级相等，非巧合可得）。命中则改档。
                alt = ""
                for b in (0.05, 0.10, 0.20, 0.30):
                    if abs(b - lim) < 1e-9:
                        continue
                    if over and c is not None and abs(c - price_tick(pc * (1 + b))) < PRICE_TOL:
                        alt = b
                        break
                    if under and c is not None and abs(c - price_tick(pc * (1 - b))) < PRICE_TOL:
                        alt = b
                        break
                if alt:
                    lim = alt
                    lu = price_tick(pc * (1 + lim))
                    ld = price_tick(pc * (1 - lim))
                    band_override = f"{int(lim * 100)}%"
                else:
                    suspect = 1
                    reason = "close_above_limit_up" if over else "close_below_limit_down"
        if None in (o, h, l, c) or (o is not None and o <= 0):
            suspect, reason = 1, "bad_price"
        elif l is not None and h is not None and l > h:
            suspect, reason = 1, "ohlc_invalid"

        is_lu = 1 if (not suspect and lu and c is not None and abs(c - lu) < PRICE_TOL) else 0
        is_ld = 1 if (not suspect and ld and c is not None and abs(c - ld) < PRICE_TOL) else 0
        one_word = 1 if (is_lu and all(
            x is not None and abs(x - lu) < PRICE_TOL for x in (o, h, l, c))) else 0

        out.append([td, code, name, board, is_st, is_bj,
                    o, h, l, c, pre_raw, pc, pc_src, pct, pct_db,
                    lim, lu, ld, is_lu, is_ld, one_word, suspect, reason, band_override,
                    None, None, None, None, None, None,
                    vol, amt, tor, cmv, src])

        prev_close = c if c is not None else prev_close
        if c is not None:
            recent.append(c)

    # 前复权：adj_cum[i] = ∏ ratios[i+1:]
    acc = 1.0
    for i in range(len(out) - 1, -1, -1):
        out[i][24] = round(ratios[i], 8)          # adj_ratio
        out[i][25] = round(acc, 8)                # adj_cum（本日之后所有除权系数连乘）
        for k, col in ((6, 26), (7, 27), (8, 28), (9, 29)):
            v = out[i][k]
            out[i][col] = round(v * acc, 4) if v is not None else None
        acc *= ratios[i]
    return [tuple(r) for r in out if r[0] >= since], adjs


COLS = ("trade_date,code,name,board,is_st,is_bj,open,high,low,close,pre_close_raw,pre_close,"
        "pre_close_src,pct_recalc,pct_db,limit_pct,limit_up_price,limit_down_price,"
        "is_limit_up,is_limit_down,one_word_board,price_suspect,suspect_reason,band_override,"
        "adj_ratio,adj_cum,open_adj,high_adj,low_adj,close_adj,"
        "volume,amount,turnover_rate,circ_mv,data_source")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20250101", help="最早的交易日")
    ap.add_argument("--src", default=str(SRC_DB))
    ap.add_argument("--dst", default=str(DST_DB))
    ap.add_argument("--rebuild", action="store_true", help="先删表重建")
    a = ap.parse_args()

    if not Path(a.src).exists():
        print(f"[FATAL] 源库不存在：{a.src}")
        return 2
    Path(a.dst).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(a.dst, uri=True)
    con.execute("ATTACH DATABASE ? AS src", (f"file:{Path(a.src).as_posix()}?mode=ro",))
    cur = con.cursor()
    if a.rebuild:
        cur.executescript("DROP TABLE IF EXISTS quote_clean; DROP TABLE IF EXISTS adj_factor_daily;")
    cur.executescript(DDL)

    codes = [r[0] for r in cur.execute("SELECT DISTINCT code FROM src.daily_quote ORDER BY code")]
    # P6（TODO#48）2026-11-10：V2 自有行情源 daily_quote_src（ingest/fetch_quote.py 落）。
    # 旧仓 src.daily_quote 归档停在 20261106；本地表补其后所有新增交易日。
    # 口径：旧表已有的日期一律以旧表为准（权威），本地行只补 trade_date > 旧表最大日。
    has_local = cur.execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='daily_quote_src'"
    ).fetchone()[0]
    src_max = None
    if has_local:
        src_max = cur.execute("SELECT MAX(trade_date) FROM src.daily_quote").fetchone()[0]
        n_local_codes = cur.execute("SELECT COUNT(DISTINCT code) FROM daily_quote_src").fetchone()[0]
        codes = sorted(set(codes) | {r[0] for r in cur.execute(
            "SELECT DISTINCT code FROM daily_quote_src")})
        print(f"[quote] 本地源 daily_quote_src：{n_local_codes} 只（补 {src_max} 之后），"
              f"代码并集 {len(codes)} 只")
    total = len(codes)

    _Q_COLS = ("trade_date,code,name,pre_close,is_st,is_bj,open,high,low,close,pre_close,"
               "pct_chg,volume,amount,turnover_rate,circ_mv,data_source")
    _Q_LOCAL = (f"SELECT {_Q_COLS} FROM daily_quote_src WHERE code=?"
                + (f" AND trade_date>'{src_max}'" if src_max else ""))
    n_rows = n_fix = n_exdiv = n_sus = n_lu = n_ow = 0
    for i, code in enumerate(codes, 1):
        if has_local:
            days = cur.execute(
                f"SELECT * FROM (SELECT {_Q_COLS} FROM src.daily_quote WHERE code=? "
                f"UNION ALL SELECT {_Q_COLS} FROM daily_quote_src WHERE code=?"
                + (f" AND trade_date>'{src_max}'" if src_max else "")
                + ") ORDER BY trade_date", (code, code)).fetchall()
        else:
            days = cur.execute(
                f"SELECT {_Q_COLS} FROM src.daily_quote "
                f"WHERE code=? ORDER BY trade_date", (code,)).fetchall()
        if not days:
            continue
        # 组装成 clean() 需要的列序：td,code,name,board,is_st,is_bj,o,h,l,c,pre_raw,pct,vol,amt,tor,cmv,src
        rows_in = []
        for r in days:
            b = board_of(r[1], r[5])
            rows_in.append((r[0], r[1], r[2], b, r[4], r[5], r[6], r[7], r[8], r[9],
                            r[10], r[11], r[12], r[13], r[14], r[15], r[16]))
        out, adjs = clean(rows_in, a.since)
        if adjs:
            cur.executemany("INSERT OR REPLACE INTO adj_factor_daily VALUES (?,?,?,?)", adjs)
            n_exdiv += len(adjs)
        if out:
            cur.executemany(f"INSERT OR REPLACE INTO quote_clean ({COLS}) VALUES "
                            f"({','.join('?' * 35)})", out)
            n_rows += len(out)
            for r in out:
                if r[12] == "prev_close_fixed":
                    n_fix += 1
                if r[21]:
                    n_sus += 1
                n_lu += r[18]
                n_ow += r[20]
        if i % 800 == 0:
            con.commit()
            print(f"  {i}/{total} 只 …", flush=True)

    con.commit()
    meta = {
        "clean_quote.built_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "clean_quote.since": a.since,
        "quote_clean.adj_type": "qfq",   # 前复权（等比前复权，锚定最新交易日）
        "quote_clean.price_round": "exchange_half_up",  # 涨停价按交易所四舍五入到分
        "clean_quote.src": a.src,
        "clean_quote.rows": str(n_rows),
        "clean_quote.pre_close_fixed": str(n_fix),
        "clean_quote.exdiv_events": str(n_exdiv),
        "clean_quote.price_suspect": str(n_sus),
        "clean_quote.limit_up_rows": str(n_lu),
        "clean_quote.one_word_rows": str(n_ow),
    }
    cur.executemany("INSERT OR REPLACE INTO meta_v2 VALUES (?,?)", meta.items())
    con.commit()

    print(f"\n完成：{n_rows} 行 → {a.dst}")
    print(f"  pre_close 取错日修正 {n_fix} 行")
    print(f"  除权事件 {n_exdiv} 个（复权因子表）")
    print(f"  price_suspect {n_sus} 行")
    print(f"  涨停 {n_lu} 行 / 一字板 {n_ow} 行")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
