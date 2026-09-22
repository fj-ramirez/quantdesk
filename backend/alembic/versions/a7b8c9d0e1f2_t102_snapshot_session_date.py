"""T102: the trading session a chain's contents belong to.

`gex.snapshots` recorded `captured_at` -- the wall-clock instant of the capture -- and nothing
about which *session* the chain it holds came from. Those differ routinely, not exceptionally:
Cboe serves the last session, so every weekend and pre-open capture holds the previous
session's book. Snapshot 178 is `2026-09-20 15:10:08Z`, a **Sunday**, `is_eod = true`, 10,436
contracts of Friday's post-opex chain. A consumer filtering `WHERE is_eod` and grouping by
`captured_at::date` gets a phantom Sunday session that never traded, which is exactly what the
first pass at a freshness query in the 2026-09-21 review produced.

Storing the session makes that rule enforceable in SQL instead of documented in prose and
re-derived, differently, by every consumer.

`is_eod` is deliberately unchanged. It is defensible as "an end-of-session book" and other code
reads it; the two columns answer different questions and a consumer usually wants both
(`WHERE is_eod GROUP BY session_date`).

**This migration backfills.** Unlike T101's horizon columns, `session_date` is a pure function
of `captured_at` and the trading calendar -- no Parquet file has to be reopened -- so every one
of the 346 existing rows can be filled here rather than left null pending a separate pass.

It imports `app.modules.gex.jobs.calendar.session_date` to do it. That is a deliberate
exception to keeping revision scripts self-contained: the alternative is transcribing the NYSE
holiday table into this file, where it would immediately begin to rot, and a wrong holiday
silently mislabels a session. The cost is that re-running this migration on a fresh database
uses whatever rule the calendar module holds at that time -- acceptable for a derived column,
and arguably the correct behaviour.

Nullable rather than NOT NULL: a row inserted by something bypassing `SnapshotRepository` is a
real possibility, and a null that says "nobody derived this" beats a NOT NULL constraint that
turns it into a failed capture. Nothing may risk the 16:20 capture.

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-21
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa

from alembic import context, op
from app.core.schemas import SCHEMA_GEX
from app.modules.gex.jobs.calendar import session_date

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "snapshots",
        sa.Column("session_date", sa.Date(), nullable=True),
        schema=SCHEMA_GEX,
    )
    op.create_index(
        "ix_snapshots_session_date", "snapshots", ["session_date"], schema=SCHEMA_GEX
    )

    if context.is_offline_mode():
        # `--sql` renders static SQL and cannot read the rows it would need to derive a session
        # from. Skipping keeps `alembic upgrade --sql` working as a review tool; an offline
        # deploy would have to run the backfill separately, and this repo deploys online.
        op.execute("-- T102 backfill skipped in offline mode: it reads captured_at")
        return

    # Backfill. Row-by-row rather than a single UPDATE ... FROM: the trading calendar lives in
    # Python, and 346 rows is nothing. Batched into one transaction by Alembic as usual.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(f"SELECT id, captured_at FROM {SCHEMA_GEX}.snapshots ORDER BY id")
    ).fetchall()
    for row_id, captured_at in rows:
        if captured_at is None:
            continue
        # `UTCDateTime` guarantees tz-aware UTC on the way out of the ORM, but this is a raw
        # driver read, so a naive value here would be UTC by storage convention. Say so
        # explicitly rather than letting `astimezone` assume the server's zone (invariant 4).
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=dt.UTC)
        connection.execute(
            sa.text(
                f"UPDATE {SCHEMA_GEX}.snapshots SET session_date = :session WHERE id = :id"
            ),
            {"session": session_date(captured_at), "id": row_id},
        )


def downgrade() -> None:
    op.drop_index("ix_snapshots_session_date", table_name="snapshots", schema=SCHEMA_GEX)
    op.drop_column("snapshots", "session_date", schema=SCHEMA_GEX)
