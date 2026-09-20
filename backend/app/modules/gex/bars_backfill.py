"""Daily-bars backfill CLI (T42, plans/continuation/00-foundation-daily-bars.md).

Run as::

    uv run python -m app.modules.gex.bars_backfill --years 5 [--symbols SPY,QQQ,SPX] [--sleep 0.5] [--force]

Mirrors `app.modules.gex.gex.backfill`'s shape (idempotent, summary dict, non-zero exit on any failure) but
is its own top-level module rather than living under `app.modules.gex.gex` -- CLAUDE.md invariant 1 keeps
`app/modules/gex/gex/*` reserved for the pure Greeks/engine code path, and bars have nothing to do with
option chains or GEX.

Resumable and rate-limited, per the plan's "likely first-contact failures" section: a symbol
whose `last_bar_date` already lands within 5 days of today is skipped with zero HTTP requests
rather than re-fetched every run (`--force` overrides that skip and re-fetches the full
`--years` window -- the only way to *deepen* stored history once the daily job has been keeping
every symbol recent), and `--sleep` puts a small pause between every request that
*is* made -- both matter once this is driving 45+ symbols against an unofficial, keyless,
unthrottled-by-contract endpoint (`app.modules.gex.providers.yahoo`) rather than a paid API with a
documented rate limit.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import sys

from app.core.config import settings
from app.modules.gex.providers.bars import BarProviderRegistry, ProviderError
from app.modules.gex.storage.bars_repository import get_session_factory, last_bar_date, upsert_bars

__all__ = ["backfill", "main"]

logger = logging.getLogger("app.modules.gex.bars_backfill")

_DEFAULT_SLEEP_SECONDS = 0.5

#: A symbol whose last stored bar is within this many days of today is treated as already
#: caught up and skipped outright -- matches the bars job's own `_OVERLAP_DAYS` re-fetch
#: window (`app.modules.gex.jobs.bars`), so the backfill CLI and the scheduled job agree on "up to date."
_UP_TO_DATE_WITHIN_DAYS = 5


async def backfill(
    *,
    symbols: list[str] | None = None,
    years: int = 5,
    sleep_seconds: float = _DEFAULT_SLEEP_SECONDS,
    registry: BarProviderRegistry | None = None,
    session_factory=None,
    today: dt.date | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Fetch and upsert `years` of history for every symbol (default `settings.scan_universe`).
    Never raises for a single symbol's failure -- see the module docstring.

    Args:
        today: Defaults to `dt.date.today()`. Tests inject a fixed value so "is this symbol
            already up to date" is deterministic regardless of the real calendar date the
            suite happens to run on -- same rationale as `YahooBarProvider`'s injectable
            `now_fn` (see that module's docstring).
        force: Ignore the resumability skip and re-fetch `years` of history for every symbol.
            The skip keys on "this symbol already has recent bars", which is permanently true
            once the 17:30 job has been running, so without this flag there is no way to
            *deepen* stored history. `--years 5 --force` over a store the daily job has filled
            with two years is the case this exists for. `upsert_bars` keys on `(symbol, date)`
            either way, so a forced run rewrites existing rows rather than duplicating them.

    Returns:
        `{"total", "fetched", "skipped_up_to_date", "failed", "inserted", "updated"}`.
    """
    active_symbols = symbols if symbols is not None else settings.scan_universe
    owns_registry = registry is None
    active_registry = registry if registry is not None else BarProviderRegistry()

    today = today if today is not None else dt.datetime.now(dt.UTC).date()
    default_start = today - dt.timedelta(days=365 * years)
    up_to_date_cutoff = today - dt.timedelta(days=_UP_TO_DATE_WITHIN_DAYS)

    fetched = 0
    skipped_up_to_date = 0
    failed = 0
    total_inserted = 0
    total_updated = 0

    try:
        for i, symbol in enumerate(active_symbols):
            last = last_bar_date(symbol, session_factory=session_factory)
            if not force and last is not None and last >= up_to_date_cutoff:
                skipped_up_to_date += 1
                logger.info("bars_backfill: %s already up to date (last=%s)", symbol, last)
                continue

            # A forced run always reaches back the full `years` window -- deepening history
            # is the point, and an incremental `last - 5 days` start could never do it.
            if force or last is None:
                start = default_start
            else:
                start = last - dt.timedelta(days=_UP_TO_DATE_WITHIN_DAYS)
            try:
                provider = active_registry.for_symbol(symbol)
                bars = await provider.fetch_daily_bars(symbol, start=start)
                result = upsert_bars(bars, session_factory=session_factory)
            except ProviderError as exc:
                failed += 1
                logger.error("bars_backfill: %s failed: %s", symbol, exc)
            except Exception:
                # Same rationale as app.modules.gex.jobs.bars.update_one_symbol: a provider bug leaking
                # something other than ProviderError must not abort the whole backfill run.
                failed += 1
                logger.exception("bars_backfill: %s raised a non-ProviderError", symbol)
            else:
                fetched += 1
                total_inserted += result.inserted
                total_updated += result.updated
                logger.info(
                    "bars_backfill: %s inserted=%d updated=%d",
                    symbol,
                    result.inserted,
                    result.updated,
                )

            if sleep_seconds and i < len(active_symbols) - 1:
                await asyncio.sleep(sleep_seconds)
    finally:
        if owns_registry:
            await active_registry.aclose()

    summary = {
        "total": len(active_symbols),
        "fetched": fetched,
        "skipped_up_to_date": skipped_up_to_date,
        "failed": failed,
        "inserted": total_inserted,
        "updated": total_updated,
    }
    logger.info("bars_backfill: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--years", type=int, default=5, help="years of history to backfill (default: 5)"
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="comma-separated symbols to backfill; default settings.SCAN_UNIVERSE",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=_DEFAULT_SLEEP_SECONDS,
        help="seconds to sleep between requests (default: 0.5)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "re-fetch the full --years window even for symbols that already have recent "
            "bars; use this to deepen history rather than resume an interrupted run"
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    )

    # Forces the module-level cached session factory into existence up front (real Postgres,
    # or whatever DATABASE_URL points at) so a misconfigured URL fails fast with one clear
    # error rather than mid-loop after several symbols have already been fetched.
    get_session_factory()

    summary = asyncio.run(
        backfill(
            symbols=symbols, years=args.years, sleep_seconds=args.sleep, force=args.force
        )
    )
    print(
        f"bars_backfill: total={summary['total']} fetched={summary['fetched']} "
        f"skipped_up_to_date={summary['skipped_up_to_date']} failed={summary['failed']} "
        f"inserted={summary['inserted']} updated={summary['updated']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
