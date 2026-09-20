"""Backfill CLI: compute GEX levels for every snapshot that doesn't have them yet.

Run as::

    uv run python -m app.modules.gex.gex.backfill

Idempotent by the same construction as `app.modules.gex.gex.store.compute_and_store`: a snapshot counts
as "lacking levels" only when it does not already carry a `gex_levels` row for every filter in
`app.modules.gex.gex.store.DEFAULT_FILTERS`, so a second run against an unchanged database finds nothing
pending and writes nothing at all -- no rows deleted, none re-inserted, counts unchanged. This
is what makes the T09 acceptance run's "run backfill twice, counts don't move" demonstration
possible without special-casing anything here.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.gex.gex.store import DEFAULT_FILTERS, compute_and_store, get_session_factory
from app.modules.gex.models.db import GexLevel, Snapshot

__all__ = ["backfill", "main"]

logger = logging.getLogger("app.modules.gex.gex.backfill")


def _snapshots_lacking_levels(session: Session, *, expected_filters: int) -> list[int]:
    """IDs of every `Snapshot` without a complete `gex_levels` row set.

    Counts *distinct filters per snapshot* rather than a plain "has any row" check.
    `compute_and_store` writes one row per filter inside a single transaction, so in practice
    a snapshot has either all of `DEFAULT_FILTERS` or none of them -- but counting also
    self-heals a snapshot left partially written by an interrupted process, at no extra cost
    over a simpler check.
    """
    counts = select(GexLevel.snapshot_id, func.count(func.distinct(GexLevel.filter))).group_by(
        GexLevel.snapshot_id
    )
    complete = {sid for sid, n in session.execute(counts) if n >= expected_filters}
    all_ids = session.execute(select(Snapshot.id)).scalars().all()
    return [sid for sid in all_ids if sid not in complete]


def backfill(
    *,
    session_factory: sessionmaker[Session] | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, int]:
    """Compute and store levels for every snapshot lacking them. Never raises.

    One snapshot's failure (a missing or corrupt Parquet file, a bad row) is logged and
    skipped rather than aborting the whole pass -- the same priority order as the T09 capture
    hook: get through everything recomputable rather than lose the whole run over one bad
    snapshot.

    Returns:
        `{"total": n, "processed": n, "failed": n, "skipped_up_to_date": n}`.
    """
    factory = session_factory or get_session_factory()
    with factory() as session:
        pending = _snapshots_lacking_levels(session, expected_filters=len(DEFAULT_FILTERS))
        total = session.execute(select(func.count()).select_from(Snapshot)).scalar_one()

    processed = 0
    failed = 0
    for snapshot_id in pending:
        try:
            compute_and_store(snapshot_id, session_factory=session_factory, data_dir=data_dir)
        except Exception:
            failed += 1
            logger.exception("backfill: failed to compute levels for snapshot_id=%d", snapshot_id)
        else:
            processed += 1

    summary = {
        "total": total,
        "processed": processed,
        "failed": failed,
        "skipped_up_to_date": total - len(pending),
    }
    logger.info("backfill: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    summary = backfill()
    print(
        f"backfill: total={summary['total']} processed={summary['processed']} "
        f"failed={summary['failed']} skipped_up_to_date={summary['skipped_up_to_date']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
