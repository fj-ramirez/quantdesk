"""One research cycle: update data -> random search -> promote -> report (T77).

Formerly `scripts/nightly.py` in the standalone repo. It makes no LLM or API calls of any kind.

**`run_cycle` is the single definition of a cycle.** The `research-search` worker
(`app/workers/research_search.py`) calls it and so does this module's CLI; neither reimplements
the other, which is what keeps a hand-run cycle and a scheduled one from drifting apart.

Run it by hand exactly as before:

    uv run python -m app.modules.research.nightly                 # one full cycle
    uv run python -m app.modules.research.nightly --trials 50     # small smoke test
    uv run python -m app.modules.research.nightly --no-update     # reuse cached data
    uv run python -m app.modules.research.nightly --report-only   # regenerate the report
    uv run python -m app.modules.research.nightly --loop 60       # continuous, hourly

`--loop` survives for hand use on a machine with no Docker. **It must never be run inside the
worker container**, which registers a scheduled job and nothing else -- the two together would
double every cycle. See the worker's own docstring.

Unlike the original, this needs `DATABASE_URL` to point at a reachable Postgres. There is no
SQLite fallback anywhere in the module (see `registry.py` for why that is the whole point), so
a laptop that is off the network fails loudly here rather than quietly starting a second,
divergent trial history.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.modules.research.config import LOGS_DIR, ensure_dirs, load_config
from app.modules.research.paper import forward_stats, portfolio_stats, promote
from app.modules.research.registry import Registry
from app.modules.research.report import write_candidates, write_report
from app.modules.research.search import run_search

log = logging.getLogger(__name__)

__all__ = ["main", "run_cycle"]


def run_cycle(
    cfg: dict,
    *,
    trials: int | None = None,
    no_update: bool = False,
    report_only: bool = False,
    registry: Registry | None = None,
) -> dict:
    """Run one full cycle and return the report summary.

    `registry` is injectable for tests; production passes none and gets one bound to
    `settings.DATABASE_URL`. A registry passed in is **not** closed here -- whoever opened it
    owns it.
    """
    owned = registry is None
    registry = registry or Registry()
    try:
        if not report_only:
            if not no_update:
                from app.modules.research.data import update_all

                log.info("updating OHLCV caches...")
                update_all(cfg)
            log.info("running search...")
            run_search(cfg, registry, trials=trials)
            n = promote(cfg, registry)
            if n:
                log.info("promoted %d new paper candidates", n)
        paper_rows = forward_stats(cfg, registry)
        summary = write_report(cfg, registry, paper_rows, portfolio_stats(cfg, registry))
        write_candidates(cfg, paper_rows)
        log.info("report written: %s", summary)
        return summary
    finally:
        if owned:
            registry.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run one EdgeLab research cycle.")
    ap.add_argument("--config", default=None)
    ap.add_argument("--trials", type=int, default=None, help="override trials_per_cycle")
    ap.add_argument("--no-update", action="store_true", help="skip the data download")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument(
        "--loop",
        type=int,
        metavar="MINUTES",
        default=None,
        help="run continuously with this many minutes between cycles (hand use only -- the "
        "worker container schedules cycles itself and must not also run this)",
    )
    args = ap.parse_args(argv)

    ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            # Kept from the original: Task Scheduler swallows stderr, so a failure that only
            # ever reached the console would be invisible on the host that runs this nightly.
            # Market time, not UTC and not the host's idea of local: the log a human opens
            # after a 02:00 run should be named for the night they ran it, and `settings.TZ` is
            # what every other schedule in this repo is expressed in.
            logging.FileHandler(
                LOGS_DIR / f"research_{dt.datetime.now(ZoneInfo(settings.TZ)).date()}.log",
                encoding="utf-8",
            ),
        ],
    )
    cfg = load_config(args.config)
    kwargs = {
        "trials": args.trials,
        "no_update": args.no_update,
        "report_only": args.report_only,
    }

    if args.loop is None:
        try:
            summary = run_cycle(cfg, **kwargs)
        except Exception:
            log.exception("cycle failed")
            return 1
        print(
            f"\nDone. Trials in registry: {summary['total_trials']}, "
            f"noise ceiling: {summary['noise_ceiling']}, "
            f"candidates above ceiling: {summary['candidates_above_ceiling']}\n"
            f"Report: {summary['report']}"
        )
        return 0

    log.info("continuous mode: one cycle every %d minutes", args.loop)
    while True:
        try:
            run_cycle(cfg, **kwargs)
        except Exception:
            log.exception("cycle failed; retrying next interval")
        time.sleep(args.loop * 60)


if __name__ == "__main__":
    raise SystemExit(main())
