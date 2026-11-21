"""换手率全历史反推（P3-0，TODO #14 的前置修复）。

问题：源库 `daily_quote.turnover_rate` 全库只有 27,832/1,565,682 = 1.78% 有值，
      集中在 2026-10-13~10-26 那几天；`circ_mv` 同样只有那几天有值。
      → 七项先验分里的 `turn` 项（±10）没数据，等于白送 0 分。

解法（不引外部数据，全部自洽推导）：
  ① 流通股本：`shares = circ_mv / close`（同一日同价，无口径问题）
  ② 股本随时间变（送转/增发）→ 用前复权因子校正：`shares × adj_cum = const`
     （adj_cum 锚定最新交易日=1，除权当日 pre_close 被交易所调整，故股本与 adj_cum 成反比）
  ③ 任一日换手率：`turnover = (amount / close) / shares_t × 100`
                = `(amount / close) × adj_cum_t / shares_ref × 100`

精度闸：用 27,832 行真值做回归。实测 P50 = 1.17%、P90 = 3.63%、超 10% 仅 0.28%
      —— 误差来源是平台 `circ_mv` 本身取整/口径近似（非本算法引入）。
      `turn` 项只在 3/6/10/15/20/30 六个边界上分桶，1% 相对误差不会改变分桶结论，
      故闸门定为「中位 ≤2% 且 P90 ≤5% 且 超 10% ≤0.5%」，达标才落库。
落 `quote_clean.turnover_est` + `turnover_src`，原始 `turnover_rate` 不动（只加不删）。
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ingest import taglib  # noqa: E402

SRC = taglib.SRC


def add_columns(con) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(quote_clean)")}
    for name, decl in (("turnover_est", "REAL"), ("turnover_src", "TEXT")):
        if name not in cols:
            con.execute(f"ALTER TABLE quote_clean ADD COLUMN {name} {decl}")
    con.commit()


def build_shares(con) -> dict[str, tuple[float, float, str]]:
    """{code: (shares_ref, adj_cum_ref, ref_date)}，取每票最近一次 circ_mv>0。"""
    out: dict[str, tuple[float, float, str]] = {}
    cur = con.execute(
        f"""SELECT q.code, q.trade_date, q.close, q.circ_mv, COALESCE(q.adj_cum,1.0)
            FROM quote_clean q
            JOIN (SELECT code, MAX(trade_date) d FROM quote_clean
                  WHERE circ_mv > 0 AND close > 0 GROUP BY code) m
              ON m.code = q.code AND m.d = q.trade_date
            WHERE q.circ_mv > 0 AND q.close > 0""")
    for code, d, close, cmv, ac in cur:
        out[code] = (cmv / close, ac, d)
    return out


def main() -> int:
    con = taglib.connect()
    add_columns(con)
    shares = build_shares(con)
    print(f"[turnover] 反推股本 {len(shares)} 只（circ_mv>0 的全部代码）")

    # ---- 精度校验：真值行 vs 反推行
    errs: list[float] = []
    bad: list[tuple] = []
    for code, d, close, amt, tr, ac in con.execute(
            """SELECT code, trade_date, close, amount, turnover_rate, COALESCE(adj_cum,1.0)
               FROM quote_clean WHERE turnover_rate > 0 AND close > 0 AND amount > 0"""):
        s = shares.get(code)
        if not s:
            continue
        est = (amt / close) * ac / s[0] * 100
        if tr <= 0:
            continue
        e = abs(est - tr) / tr
        errs.append(e)
        if e > 0.05 and len(bad) < 8:
            bad.append((code, d, tr, round(est, 4), round(e, 3), s[2]))
    if errs:
        errs.sort()
        med = statistics.median(errs)
        p90 = errs[int(len(errs) * 0.90)]
        p99 = errs[int(len(errs) * 0.99)]
        over10 = sum(1 for e in errs if e > 0.10) / len(errs)
        print(f"[turnover] 校验 {len(errs)} 行：相对误差 中位 {med:.4%} / P90 {p90:.3%} / "
              f"P99 {p99:.3%} / 超 10% {over10:.3%}")
        for b in bad:
            print("   偏差样例", b)
        if med > 0.02 or p90 > 0.05 or over10 > 0.005:
            print("[turnover] 精度不达标，拒绝落库")
            return 2

    # ---- 全量回填
    rows = []
    for code, d, close, amt, ac, tr in con.execute(
            """SELECT code, trade_date, close, amount, COALESCE(adj_cum,1.0), turnover_rate
               FROM quote_clean WHERE close > 0 AND amount > 0"""):
        s = shares.get(code)
        if not s:
            continue
        if tr and tr > 0:
            rows.append((tr, "given", code, d))         # 原始有值 → 直接沿用真值（不要写 NULL）
        else:
            rows.append(((amt / close) * ac / s[0] * 100, "est_ref", code, d))
    con.executemany(
        "UPDATE quote_clean SET turnover_est=?, turnover_src=? WHERE code=? AND trade_date=?",
        rows)
    con.commit()

    n_all = con.execute("SELECT COUNT(*) FROM quote_clean").fetchone()[0]
    n_est = con.execute("SELECT COUNT(*) FROM quote_clean WHERE turnover_est IS NOT NULL").fetchone()[0]
    n_given = con.execute("SELECT COUNT(*) FROM quote_clean WHERE turnover_src='given'").fetchone()[0]
    print(f"[turnover] 落库 {n_est}/{n_all} = {n_est / n_all:.2%}"
          f"（真值 {n_given} / 反推 {n_est - n_given}）")
    d = taglib.trade_days(con)[-1]
    print(f"[turnover] {d} 当日覆盖 "
          f"{con.execute('SELECT SUM(turnover_est IS NOT NULL) FROM quote_clean WHERE trade_date=?', (d,)).fetchone()[0]}"
          f"/{con.execute('SELECT COUNT(*) FROM quote_clean WHERE trade_date=?', (d,)).fetchone()[0]}")
    taglib.meta_set(con, {
        "turnover.rows": str(n_est),
        "turnover.given": str(n_given),
        "turnover.err_median": f"{statistics.median(errs):.6f}" if errs else "",
        "turnover.err_p90": f"{errs[int(len(errs) * 0.90)]:.6f}" if errs else "",
        "turnover.built_at": taglib.now(),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
