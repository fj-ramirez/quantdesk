"""snapshot content hash and (underlying, captured_at) uniqueness

Revision ID: c7a1e93b5d02
Revises: b2d4f6a8c0e1
Create Date: 2026-09-11 13:40:00.000000

TASKS.md T71, gating T18. Two changes, in a deliberate order:

1. Add `snapshots.content_hash` and an index on `(underlying, content_hash)`, so the capture
   path can recognise a chain it has already stored regardless of what the vendor's clock says
   (see `app.storage.fingerprint`).
2. Add a uniqueness constraint on `(underlying, captured_at)` -- but only after reporting and
   resolving any duplicates that already exist, because adding it to a table that violates it
   fails the migration.

The old non-unique `ix_snapshots_underlying_captured_at` is dropped: the unique constraint's
own backing index covers the identical columns in the identical order, so keeping both would
mean maintaining two indexes for one query shape on every insert.

**Duplicate resolution keeps the earliest row per pair and deletes the later ones, along with
their `gex_levels` / `gex_by_strike` children.** The Parquet files the deleted rows pointed at
are deliberately **left on disk**: unpicking which files are still referenced is the state
review's own orphaned-file item, deleting raw captures is irreversible on a source with no
history, and an orphaned file costs a megabyte while a wrongly deleted one costs a day of data
that cannot be re-fetched. Downgrade restores the old index and drops the column; it cannot
restore deleted duplicate rows, which is noted here rather than pretended otherwise.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a1e93b5d02"
down_revision: str | Sequence[str] | None = "b2d4f6a8c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _resolve_duplicates() -> None:
    """Report, then delete, all but the earliest row of each duplicated (underlying, captured_at).

    Written as explicit SELECT-then-DELETE rather than one clever statement so the log line
    below actually names what was removed -- a silent cleanup inside a migration is exactly the
    kind of thing that is impossible to reconstruct afterwards.
    """
    bind = op.get_bind()
    duplicates = bind.execute(
        sa.text(
            """
            SELECT underlying, captured_at, COUNT(*) AS n, MIN(id) AS keep_id
            FROM snapshots
            GROUP BY underlying, captured_at
            HAVING COUNT(*) > 1
            ORDER BY underlying, captured_at
            """
        )
    ).all()

    if not duplicates:
        print("T71: no duplicate (underlying, captured_at) rows found in snapshots")
        return

    total_removed = 0
    for underlying, captured_at, count, keep_id in duplicates:
        doomed = [
            row[0]
            for row in bind.execute(
                sa.text(
                    """
                    SELECT id FROM snapshots
                    WHERE underlying = :underlying
                      AND captured_at = :captured_at
                      AND id <> :keep_id
                    """
                ),
                {"underlying": underlying, "captured_at": captured_at, "keep_id": keep_id},
            ).all()
        ]
        print(
            f"T71: {underlying} at {captured_at} has {count} rows; "
            f"keeping id={keep_id}, deleting {doomed} "
            f"(their Parquet files are intentionally left on disk)"
        )
        for table in ("gex_by_strike", "gex_levels", "decisions"):
            bind.execute(
                sa.text(f"DELETE FROM {table} WHERE snapshot_id IN :ids").bindparams(
                    sa.bindparam("ids", value=doomed, expanding=True)
                )
            )
        bind.execute(
            sa.text("DELETE FROM snapshots WHERE id IN :ids").bindparams(
                sa.bindparam("ids", value=doomed, expanding=True)
            )
        )
        total_removed += len(doomed)

    print(f"T71: removed {total_removed} duplicate snapshot rows")


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("snapshots", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_snapshots_underlying_content_hash",
        "snapshots",
        ["underlying", "content_hash"],
    )

    _resolve_duplicates()

    op.drop_index("ix_snapshots_underlying_captured_at", table_name="snapshots")
    op.create_unique_constraint(
        "uq_snapshots_underlying_captured_at",
        "snapshots",
        ["underlying", "captured_at"],
    )


def downgrade() -> None:
    """Downgrade schema.

    Note: rows deleted by `_resolve_duplicates` are not restored -- they cannot be.
    """
    op.drop_constraint("uq_snapshots_underlying_captured_at", "snapshots", type_="unique")
    op.create_index(
        "ix_snapshots_underlying_captured_at",
        "snapshots",
        ["underlying", "captured_at"],
    )
    op.drop_index("ix_snapshots_underlying_content_hash", table_name="snapshots")
    op.drop_column("snapshots", "content_hash")
