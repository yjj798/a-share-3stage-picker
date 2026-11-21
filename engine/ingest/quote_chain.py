# -*- coding: utf-8 -*-
"""quote_chain.py — V2 行情生产链编排（P6 / TODO#48，2026-11-10）。

顺序即契约，不可调换：
    1. ingest/fetch_quote.py       同花顺日线（last.js）→ daily_quote_src（原始暂存）
    2. ingest/clean_quote.py       涨停价/复权/清洗重算 → quote_clean（全量幂等，~90s）
    3. ingest/turnover_fix.py      turnover_est 全历史反推（观察池入池规则依赖，~25s）
    4. ingest/limit_up_day_sync.py 近 30 交易日涨停表重算 → limit_up_day_v2

auto.py 的 pm 里程碑（15:05）在本链之后才跑 universe/preselect/tags/score，
保证下游拿到的行情永远先落库。任一步失败即返回非零，auto 记录到状态文件。
"""
from __future__ import annotations

import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                        # noqa: BLE001
    pass

ROOT = __file__.rsplit("\\ingest", 1)[0].rsplit("/ingest", 1)[0]

STEPS = [
    ("行情抓取", "ingest/fetch_quote.py"),
    ("行情清洗", "ingest/clean_quote.py"),
    ("换手反推", "ingest/turnover_fix.py"),        # turnover_est 全历史重算（池规则依赖）
    ("涨停表", "ingest/limit_up_day_sync.py"),
]


def main() -> int:
    rc = 0
    for label, script in STEPS:
        t0 = time.time()
        r = subprocess.run([sys.executable, script], cwd=ROOT,
                           capture_output=True, text=True, timeout=1800)
        out = "\n".join((r.stdout or "").strip().splitlines()[-3:])
        print(f"[{label}] rc={r.returncode} {time.time() - t0:.0f}s\n{out}",
              flush=True)
        if r.returncode != 0:
            print((r.stderr or "")[-500:], flush=True)
            return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
