"""History loader: EOD snapshots for past sessions from ThetaData (T26).

Run as::

    uv run python -m app.modules.gex.gex.history --symbols SPX,SPY --start 2024-09-01 --end 2026-09-24

Needs a reachable Theta Terminal (``THETADATA_URL``). On the homeserver that is the
``theta-terminal`` service; run this inside a worker container there, e.g.
``docker compose -f compose.yaml -f compose.prod.yaml exec gex-capture python -m app.modules.gex.gex.history ...``.

Each session goes through :func:`app.modules.gex.jobs.capture.capture_snapshot` with a
one-shot :class:`~app.modules.gex.providers.thetadata.ThetaDataEodProvider` bound to it, so a
historical snapshot is written, indexed and has its levels computed by exactly the code a live
capture uses. There is no second write path to drift.

The rules it enforces (plans/thetadata/README.md):

* **The live record stays authoritative.** A session that already has an EOD snapshot for the
  symbol -- from Cboe or from an earlier run of this loader -- is skipped, never replaced.
  That also makes the loader resumable: re-running the same range picks up where it stopped.
* **It stays out of the capture window.** On a trading day it refuses to start between 15:45
  and 17:00 ET unless ``--force``: nothing may risk the 16:20 capture.
* **It never touches ``gex.decisions``.** History produces snapshots and levels only; replaying
  decisions over them is T127 and writes elsewhere.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.gex.gex.store import get_session_factory
from app.modules.gex.jobs.calendar import is_trading_day
from app.modules.gex.jobs.capture import CaptureResult, capture_snapshot
from app.modules.gex.models.chain import Underlying
from app.modules.gex.models.db import Snapshot
from app.modules.gex.providers.thetadata import ThetaDataClient, ThetaDataEodProvider

__all__ = ["capture_window_open", "load_history", "main", "pending_sessions"]

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")
_WINDOW = (dt.time(15, 45), dt.time(17, 0))


def capture_window_open(now: dt.datetime) -> bool:
    """True while the live EOD capture may be running (15:45-17:00 ET on a trading day)."""
    local = now.astimezone(_NY)
    return is_trading_day(local.date()) and _WINDOW[0] <= local.time() < _WINDOW[1]


def pending_sessions(
    session: Session, underlying: Underlying, start: dt.date, end: dt.date
) -> list[dt.date]:
    """Trading days in ``[start, end]`` with no EOD snapshot for ``underlying`` yet."""
    have = set(
        session.execute(
            select(Snapshot.session_date).where(
                Snapshot.underlying == underlying.value,
                Snapshot.is_eod.is_(True),
                Snapshot.session_date.is_not(None),
            )
        ).scalars()
    )
    days: list[dt.date] = []
    day = start
    while day <= end:
        if is_trading_day(day) and day not in have:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


async def load_history(
    symbols: Sequence[Underlying],
    start: dt.date,
    end: dt.date,
    *,
    client: ThetaDataClient | None = None,
    session_factory: sessionmaker[Session] | None = None,
    data_dir: str | Path | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Load every pending session for every symbol, oldest first. Returns counts."""
    factory = session_factory if session_factory is not None else get_session_factory()
    owns_client = client is None
    active = client if client is not None else ThetaDataClient()
    counts = {"pending": 0, "loaded": 0, "failed": 0, "duplicate": 0}
    try:
        for underlying in symbols:
            with factory() as session:
                days = pending_sessions(session, underlying, start, end)
            counts["pending"] += len(days)
            logger.info("history: %s has %d pending sessions in %s..%s", underlying.value, len(days), start, end)
            if dry_run:
                continue
            for day in days:
                result: CaptureResult = await capture_snapshot(
                    underlying.value,
                    is_eod=True,
                    provider=ThetaDataEodProvider(active, day),
                    session_factory=factory,
                    data_dir=data_dir,
                )
                if not result.ok:
                    counts["failed"] += 1
                    logger.error("history: %s %s failed: %s", underlying.value, day, result.error)
                elif result.skipped_duplicate:
                    counts["duplicate"] += 1
                else:
                    counts["loaded"] += 1
    finally:
        if owns_client:
            await active.close()
    return counts


def _date(raw: str) -> dt.date:
    return dt.date.fromisoformat(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="SPX,SPY,QQQ,GLD,DIA", help="comma-separated underlyings")
    parser.add_argument("--start", type=_date, required=True, help="first session, YYYY-MM-DD")
    parser.add_argument("--end", type=_date, required=True, help="last session, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="count pending sessions, load nothing")
    parser.add_argument("--force", action="store_true", help="run even inside the 15:45-17:00 ET capture window")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        symbols = [Underlying(s.strip().upper()) for s in args.symbols.split(",") if s.strip()]
    except ValueError as exc:
        parser.error(str(exc))
    if args.start > args.end:
        parser.error("--start is after --end")
    if not args.dry_run and not args.force and capture_window_open(dt.datetime.now(dt.UTC)):
        print("history: refusing to run inside the 15:45-17:00 ET capture window (use --force)")
        return 2

    counts = asyncio.run(load_history(symbols, args.start, args.end, dry_run=args.dry_run))
    print(
        f"history: pending={counts['pending']} loaded={counts['loaded']} "
        f"duplicate={counts['duplicate']} failed={counts['failed']}"
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
