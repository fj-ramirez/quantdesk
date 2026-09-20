"""The single writer.

Adapters never write (spec 1.2); they yield Observation instances and this class
persists them. Keeping one writer is what makes source_batch a reliable audit
trail and what lets the conflict check below live in exactly one place.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from ..errors import DataIntegrityError, UnknownSeriesError
from ..logging import get_logger
from ..models import Observation, SeriesMeta
from .db import Store

log = get_logger("store.loader")


@dataclass(frozen=True)
class LoadResult:
    """What one persist() call actually did. Returned rather than logged only,
    so a caller (the CLI, a test) can assert on it."""

    source_batch: str
    inserted: int
    duplicates: int
    series_touched: tuple[str, ...]

    @property
    def total(self) -> int:
        return self.inserted + self.duplicates


def new_batch_id() -> str:
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}"


class Loader:
    def __init__(self, store: Store) -> None:
        if store.read_only:
            raise DataIntegrityError("Loader requires a writable Store")
        self.store = store
        self.conn = store.conn

    # -- metadata -------------------------------------------------------------

    def register_series(self, meta: SeriesMeta) -> None:
        """Insert or update one series_metadata row.

        Metadata is descriptive and safe to correct in place; observations are
        not, and are never updated (see persist).
        """
        self.conn.execute(
            """
            INSERT INTO series_metadata (
                series_id, display_name, source, source_code, asset_class,
                category, unit, frequency, default_transform, revisable,
                vintage_source, snapshot_tz, snapshot_local_time, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (series_id) DO UPDATE SET
                display_name = excluded.display_name,
                source = excluded.source,
                source_code = excluded.source_code,
                asset_class = excluded.asset_class,
                category = excluded.category,
                unit = excluded.unit,
                frequency = excluded.frequency,
                default_transform = excluded.default_transform,
                revisable = excluded.revisable,
                vintage_source = excluded.vintage_source,
                snapshot_tz = excluded.snapshot_tz,
                snapshot_local_time = excluded.snapshot_local_time,
                notes = excluded.notes
            """,
            [
                meta.series_id, meta.display_name, meta.source, meta.source_code,
                meta.asset_class, meta.category, meta.unit, meta.frequency,
                meta.default_transform, meta.revisable, meta.vintage_source,
                meta.snapshot_tz, meta.snapshot_local_time, meta.notes,
            ],
        )

    def registered_series(self) -> set[str]:
        rows = self.conn.execute("SELECT series_id FROM series_metadata").fetchall()
        return {r[0] for r in rows}

    # -- batches --------------------------------------------------------------

    def start_batch(self, adapter: str, args: str = "") -> str:
        batch = new_batch_id()
        self.conn.execute(
            """
            INSERT INTO ingest_batches (source_batch, started_at, adapter, args, status)
            VALUES (?, ?, ?, ?, 'running')
            """,
            [batch, datetime.now(UTC), adapter, args],
        )
        log.info("batch %s started (adapter=%s %s)", batch, adapter, args)
        return batch

    def finish_batch(self, batch: str, status: str, note: str = "") -> None:
        self.conn.execute(
            "UPDATE ingest_batches SET finished_at = ?, status = ?, note = ? WHERE source_batch = ?",
            [datetime.now(UTC), status, note, batch],
        )
        log.info("batch %s finished: %s %s", batch, status, note)

    # -- observations ---------------------------------------------------------

    def persist(self, observations: Iterable[Observation]) -> LoadResult:
        """Write observations. Never updates an existing row.

        A re-fetch that produces a byte-identical row is a duplicate and is
        skipped. A re-fetch that produces a *different* value under the same
        (series_id, value_date, as_of) is not a duplicate: it means the source
        changed a value without advancing its vintage, or our as_of derivation
        is wrong. Either way it raises, because silently keeping one of the two
        would make the stored history untrue and undetectable.
        """
        obs = list(observations)
        if not obs:
            log.warning("persist called with no observations")
            return LoadResult("", 0, 0, ())

        batches = {o.source_batch for o in obs}
        if len(batches) != 1:
            raise DataIntegrityError(
                f"persist expects one source_batch per call, got {sorted(batches)}"
            )
        batch = batches.pop()

        known = self.registered_series()
        unknown = sorted({o.series_id for o in obs} - known)
        if unknown:
            raise UnknownSeriesError(
                f"not registered in series_metadata: {unknown}. "
                "Register the series before loading its observations."
            )

        inserted = duplicates = 0
        for o in obs:
            existing = self.conn.execute(
                """
                SELECT value, source_batch FROM observations
                WHERE series_id = ? AND value_date = ? AND as_of = ?
                """,
                [o.series_id, o.value_date, o.as_of],
            ).fetchone()

            if existing is not None:
                prior_value, prior_batch = existing
                if prior_value != o.value:
                    raise DataIntegrityError(
                        f"{o.series_id} {o.value_date} as_of={o.as_of.isoformat()}: "
                        f"stored {prior_value} (batch {prior_batch}) but batch {batch} "
                        f"supplies {o.value}. The same vintage cannot hold two values."
                    )
                duplicates += 1
                continue

            self.conn.execute(
                """
                INSERT INTO observations
                    (series_id, value_date, as_of, value, as_of_basis, source_batch)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [o.series_id, o.value_date, o.as_of, o.value, o.as_of_basis,
                 o.source_batch],
            )
            inserted += 1

        touched = tuple(sorted({o.series_id for o in obs}))
        log.info(
            "batch %s: %d inserted, %d already present, %d series",
            batch, inserted, duplicates, len(touched),
        )
        return LoadResult(batch, inserted, duplicates, touched)
