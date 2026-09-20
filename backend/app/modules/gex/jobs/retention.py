"""Retention for intraday per-strike GEX detail (TASKS.md T32, gating T18).

## Why this exists

`gex_by_strike` is the volume driver in this schema: ~800 rows per (snapshot, filter), which
at today's one capture a day is ~8,000 rows/day and at T18's 27 captures a session becomes
**~216,000 rows/day, ~54 M/year**. Nothing partitions or prunes it. T32 was filed on
2026-09-04 with the instruction to implement it *before* T18 ships, not after the table is
large, and that is why this module lands first.

## Why pruning is safe

**`gex_by_strike` is a cache, not a record.** Invariant 5 puts per-contract data in Parquet and
computed results in Postgres; every row in this table is recomputable from the Parquet file
that `snapshots.parquet_path` points at, by the same `app.modules.gex.gex.store.compute_and_store` that
wrote it -- which already does delete-then-insert, so a rebuild is idempotent -- or wholesale
via `uv run python -m app.modules.gex.gex.backfill`. Pruning a day therefore loses nothing permanently. It
costs a rebuild if that day is ever wanted again in strike detail.

This is the opposite of the Parquet files themselves, which are unbackfillable on a source that
serves only "now", and which this module never touches.

## The policy

| Data | Retention |
|---|---|
| Parquet files | forever -- unbackfillable, the archive of record |
| `snapshots` index rows | forever -- the index into that archive |
| `gex_levels` summary rows | forever -- a few hundred rows/day even at polling cadence, and what keeps a pruned day legible in history views and T20's timeline |
| `gex_by_strike` for `is_eod=True` | forever |
| `gex_by_strike` for `is_eod=False` | `settings.INTRADAY_STRIKE_RETENTION_DAYS` days |

The one user-visible consequence: scrubbing T20's intraday slider back past the cutoff finds
levels but no strike detail. That is a designed empty state, not a bug -- it is named in
`plans/continuous-feed/02-intraday-polling.md` as something T20 must render explicitly.

## Why a separate nightly job

Rather than a tail step on the capture path. Three reasons, in order of weight: a slow `DELETE`
must never sit in front of the next capture on a path whose whole premise is that a missed
capture is permanent; a distinct job id is visible in the scheduler's job list and can be run
by hand; and the deletion is then attributable to itself in the logs rather than buried in a
capture's output.

No new index is added. The predicate selects snapshot ids from `snapshots` (a table of tens of
thousands of rows even after a year of polling -- a trivial scan) and deletes from
`gex_by_strike` by `snapshot_id`, which is the leading column of the existing
`ix_gex_by_strike_snapshot_filter`. The expensive half is already indexed; the cheap half does
not warrant maintaining another index on every insert.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.modules.gex.models.db import GexByStrike, Snapshot

__all__ = ["PruneResult", "prune_intraday_strike_detail"]

logger = logging.getLogger("app.modules.gex.jobs.retention")

#: How many snapshots' strike rows to delete per transaction. Bounded so that the first run
#: after enabling T18 -- which may face months of accumulated intraday detail rather than one
#: day's -- commits incrementally and makes durable progress instead of building one enormous
#: transaction that a restart would roll back entirely.
_CHUNK_SIZE = 50


@dataclass(frozen=True)
class PruneResult:
    """What one prune run did. Also the shape of its structured log line."""

    cutoff: dt.datetime | None
    snapshots_pruned: int
    rows_deleted: int
    disabled: bool = False


def prune_intraday_strike_detail(
    *,
    session_factory: sessionmaker[Session],
    retention_days: int | None = None,
    now: dt.datetime | None = None,
) -> PruneResult:
    """Delete `gex_by_strike` rows belonging to non-EOD snapshots older than the cutoff.

    Args:
        session_factory: Bound sessionmaker. Tests pass a SQLite-backed one.
        retention_days: Defaults to `settings.INTRADAY_STRIKE_RETENTION_DAYS`. `0` (or
            negative) disables pruning and returns immediately, having done nothing.
        now: Injectable clock for tests; defaults to `datetime.now(UTC)`.

    Returns:
        A `PruneResult`. Never raises for an empty table or a disabled setting -- both are
        ordinary outcomes, not errors.
    """
    days = settings.INTRADAY_STRIKE_RETENTION_DAYS if retention_days is None else retention_days
    if days <= 0:
        logger.info(
            '{"event": "prune_intraday_strike_detail", "disabled": true, "retention_days": %d}',
            days,
        )
        return PruneResult(cutoff=None, snapshots_pruned=0, rows_deleted=0, disabled=True)

    moment = now or dt.datetime.now(dt.UTC)
    cutoff = moment - dt.timedelta(days=days)

    with session_factory() as session:
        # Only snapshots that still *have* strike rows, so a second run over an already-pruned
        # range is a genuine no-op rather than a pile of zero-row deletes.
        stale_ids = list(
            session.execute(
                select(Snapshot.id)
                .where(
                    Snapshot.is_eod.is_(False),
                    Snapshot.captured_at < cutoff,
                    Snapshot.id.in_(select(GexByStrike.snapshot_id).distinct()),
                )
                .order_by(Snapshot.id)
            )
            .scalars()
            .all()
        )

        rows_deleted = 0
        for start in range(0, len(stale_ids), _CHUNK_SIZE):
            chunk = stale_ids[start : start + _CHUNK_SIZE]
            result = session.execute(
                delete(GexByStrike).where(GexByStrike.snapshot_id.in_(chunk))
            )
            rows_deleted += result.rowcount or 0
            session.commit()

    outcome = PruneResult(
        cutoff=cutoff, snapshots_pruned=len(stale_ids), rows_deleted=rows_deleted
    )
    logger.info(
        '{"event": "prune_intraday_strike_detail", "cutoff": "%s", "snapshots_pruned": %d, '
        '"rows_deleted": %d}',
        cutoff.isoformat(),
        outcome.snapshots_pruned,
        outcome.rows_deleted,
    )
    return outcome
