"""ETF shares-outstanding fetch CLI (T52, plans/continuation/05-etf-flows.md).

Run as::

    uv run python -m app.flows_fetch [--symbols XLK,IWM]

Mirrors `app.bars_backfill`'s shape (top-level module rather than nested under `app.jobs`,
argparse CLI wrapping the async job function, non-zero exit on any family failure) but has no
`--years`/`--force`/resumability options: unlike bars, this table has no backfill story at all
-- issuer pages only ever answer "the latest published value," so there is nothing to deepen.
Every run just calls `app.jobs.flows.update_flows_job` once.

This is also the acceptance-test entry point named in the T52 brief: running this twice in a
row with the same `--symbols` inserts a row the first time and (ordinarily) zero the second,
per `app.storage.flows_repository.insert_new_rows`'s `(symbol, date)`-keyed insert-once
contract -- a second run only inserts something new if the issuer's own as-of date actually
advanced between the two runs.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.jobs.flows import update_flows_job
from app.storage.flows_repository import get_session_factory

__all__ = ["main"]

logger = logging.getLogger("app.flows_fetch")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help=(
            "comma-separated symbols to fetch; default every symbol every supported family "
            "covers (app.providers.etf_flows.ALL_SUPPORTED_SYMBOLS)"
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    )

    # Forces the module-level cached session factory into existence up front (real Postgres,
    # or whatever DATABASE_URL points at) so a misconfigured URL fails fast with one clear
    # error rather than mid-run after a family's fetch has already happened -- same rationale
    # as app.bars_backfill.main's identical call.
    get_session_factory()

    results = asyncio.run(update_flows_job(symbols=symbols))

    failed = 0
    for result in results:
        if result.ok:
            print(
                f"flows_fetch: {result.family} inserted={result.inserted} "
                f"skipped={result.skipped} failed_symbols={result.failed_symbols}"
            )
        else:
            failed += 1
            print(f"flows_fetch: {result.family} FAILED: {result.error}")

    if not results:
        print("flows_fetch: no family matched the requested symbols; nothing fetched")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
