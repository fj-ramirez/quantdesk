"""Backfill CLI: compute GEX levels for every snapshot that doesn't have them yet.

Run as::

    uv run python -m app.modules.gex.gex.backfill

Idempotent by the same construction as `app.modules.gex.gex.store.compute_and_store`: a snapshot counts
as "lacking levels" only when it does not already carry a `gex_levels` row for every filter in
`app.modules.gex.gex.store.DEFAULT_FILTERS`, so a second run against an unchanged database finds nothing
pending and writes nothing at all -- no rows deleted, none re-inserted, counts unchanged. This
is what makes the T09 acceptance run's "run backfill twice, counts don't move" demonstration
possible without special-casing anything here.

**`--recompute` exists because that selection rule cannot see a change in the engine** (T101).
"Lacking levels" answers "was this snapshot ever processed", not "was it processed by the
current code", so after an engine change every stored snapshot looks up to date and the pass
writes nothing -- which is exactly wrong when the point is to propagate the change. Two such
changes landed on 2026-09-21: T99 put a sign and magnitude test on the walls, so stored rows
still name walls the engine would now refuse, and T101 added the expiry dimension, which no
existing row has at all.

`--recompute` is safe rather than merely permitted: `compute_and_store` deletes the existing
`(snapshot_id, filter)` slice before reinserting, so reprocessing replaces rather than
accumulates. It is **not** the default, because it reopens every Parquet file on disk -- run it
outside capture hours, since nothing may risk the 16:20 capture.

**`--atm-iv` fills T103's stored ATM vol on snapshots captured before T103** (T124). Readers
that need a symbol's IV (`/trend`, `/regime`, `/decisions`) fall back to reopening the Parquet
file when the column is null -- on 2026-09-23 that was 23 of the 28 latest snapshots, ~1.8s of
every request. This pass computes the value once and stores it, touching nothing else. A chain
with no usable IV stays null and is simply retried on the next run.

It rewrites derived rows only. `gex.decisions` is append-only and is never touched here: the
track record depends on a recorded level being the level that was actually suggested at the
time, wrong ones included.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.gex.gex.engine import to_frame
from app.modules.gex.gex.report import iv_regime
from app.modules.gex.gex.store import (
    DEFAULT_FILTERS,
    apply_atm_iv,
    compute_and_store,
    get_session_factory,
)
from app.modules.gex.models.db import GexLevel, Snapshot
from app.modules.gex.storage.parquet import read_snapshot, resolve_snapshot_path

__all__ = ["backfill", "backfill_atm_iv", "main"]

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
    recompute: bool = False,
) -> dict[str, int]:
    """Compute and store levels for every snapshot lacking them. Never raises.

    With `recompute=True`, processes **every** snapshot rather than only those lacking levels
    -- the way to propagate an engine change into stored rows. See the module docstring.

    One snapshot's failure (a missing or corrupt Parquet file, a bad row) is logged and
    skipped rather than aborting the whole pass -- the same priority order as the T09 capture
    hook: get through everything recomputable rather than lose the whole run over one bad
    snapshot.

    Returns:
        `{"total": n, "processed": n, "failed": n, "skipped_up_to_date": n}`.
    """
    factory = session_factory or get_session_factory()
    with factory() as session:
        if recompute:
            pending = list(
                session.execute(select(Snapshot.id).order_by(Snapshot.id)).scalars().all()
            )
        else:
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


def backfill_atm_iv(
    *,
    session_factory: sessionmaker[Session] | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, int]:
    """Store the ATM vol on every snapshot whose `atm_iv` is null (T124). Never raises.

    Only the six `atm_iv*` columns are written -- levels, strikes and `gex.decisions` are left
    exactly as they are. One snapshot's failure is logged and skipped, as in `backfill`.

    Returns:
        `{"pending": n, "filled": n, "still_null": n, "failed": n}` -- `still_null` counts
        chains that were read fine but carry no usable ATM quote.
    """
    factory = session_factory or get_session_factory()
    with factory() as session:
        pending = list(
            session.execute(
                select(Snapshot.id).where(Snapshot.atm_iv.is_(None)).order_by(Snapshot.id)
            )
            .scalars()
            .all()
        )

    filled = still_null = failed = 0
    for snapshot_id in pending:
        try:
            with factory() as session:
                row = session.get(Snapshot, snapshot_id)
                snapshot = read_snapshot(resolve_snapshot_path(row, data_dir))  # invariant 5
                iv = iv_regime(to_frame(snapshot), snapshot.spot)
                apply_atm_iv(row, iv)
                session.commit()
        except Exception:
            failed += 1
            logger.exception("backfill --atm-iv: failed for snapshot_id=%d", snapshot_id)
            continue
        if iv.atm_iv is None:
            still_null += 1
        else:
            filled += 1

    summary = {
        "pending": len(pending),
        "filled": filled,
        "still_null": still_null,
        "failed": failed,
    }
    logger.info("backfill --atm-iv: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--recompute",
        action="store_true",
        help=(
            "Reprocess every snapshot, not just those lacking levels. Use after an engine "
            "change; reopens every Parquet file, so run it outside capture hours."
        ),
    )
    parser.add_argument(
        "--atm-iv",
        action="store_true",
        help=(
            "Store the ATM implied vol on snapshots captured before T103 (atm_iv is null) "
            "instead of computing levels. Reopens those Parquet files only."
        ),
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.atm_iv:
        iv_summary = backfill_atm_iv()
        print(
            f"backfill --atm-iv: pending={iv_summary['pending']} filled={iv_summary['filled']} "
            f"still_null={iv_summary['still_null']} failed={iv_summary['failed']}"
        )
        return 1 if iv_summary["failed"] else 0

    summary = backfill(recompute=args.recompute)
    print(
        f"backfill: total={summary['total']} processed={summary['processed']} "
        f"failed={summary['failed']} skipped_up_to_date={summary['skipped_up_to_date']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
