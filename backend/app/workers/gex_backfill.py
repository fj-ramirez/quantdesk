"""The GEX intraday backfill (T166, `plans/gex-intraday-backfill/README.md`). One container,
behind the `backfill` compose profile, so a plain `up` never starts it.

It runs `app.modules.gex.gex.intraday_history` over the plan's range and exits when every
session has its files. It sleeps through weekday capture hours (09:15-17:00 ET), when the live
work owns the ThetaData terminal. It resumes per file, so `restart: on-failure` after a crash or
an OOM kill retries only what is missing; a clean finish exits 0 and stays down.

Environment (defaults are the plan's):

| Variable | Default |
|---|---|
| `GEX_BACKFILL_SYMBOLS` | `SPX,SPY,QQQ,GLD,IWM,DIA` (load order: the index family first) |
| `GEX_BACKFILL_START` / `_END` | `2023-01-03` / `2026-09-18` (the holdout starts 2026-09-21) |
| `GEX_BACKFILL_OUT` | `/data/research/gex_intraday` |
| `GEX_BACKFILL_NOT_BEFORE` | unset; an ISO datetime with offset delays the first request |
"""

from __future__ import annotations

import os
import sys

from app.modules.gex.gex.intraday_history import main


def _argv() -> list[str]:
    env = os.environ.get
    argv = [
        "--symbols", env("GEX_BACKFILL_SYMBOLS", "SPX,SPY,QQQ,GLD,IWM,DIA"),
        "--start", env("GEX_BACKFILL_START", "2023-01-03"),
        "--end", env("GEX_BACKFILL_END", "2026-09-18"),
        "--out", env("GEX_BACKFILL_OUT", "/data/research/gex_intraday"),
    ]
    if not_before := env("GEX_BACKFILL_NOT_BEFORE", "").strip():
        argv += ["--not-before", not_before]
    return argv + sys.argv[1:]


if __name__ == "__main__":
    main(_argv())
