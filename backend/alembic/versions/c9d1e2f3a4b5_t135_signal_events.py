"""T135: research.signal_events -- the live price signals' forward record.

Every ENTER and EXIT that `app.modules.research.signals` reports for the demo forward test
(plans/signal-alerts/), alerted or not. Append-only under the key `(signal, symbol, bar_ts,
action)`: each run re-reads a window of bars so a missed run loses nothing, and the key turns
those repeats into no-ops instead of duplicates. Never updated, for the reason the decision log
and `terminal.observations` are not: the history is the evidence.

No backfill. A forward record dated before the rule was live would be a backtest wearing a
forward test's name.

T76's `ALTER DEFAULT PRIVILEGES` covers the `research` schema, so `quantdesk_ro` can read this
table the moment it exists and no grant belongs in this file.

Revision ID: c9d1e2f3a4b5
Revises: b3c4d5e6f7a8
Create Date: 2026-10-06

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import app.modules.gex.models.db
from alembic import op

revision: str = "c9d1e2f3a4b5"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Matches `app.core.schemas.SCHEMA_RESEARCH`, spelled literally: a revision records what was
#: applied to a real database and must not change meaning when a constant is renamed later.
SCHEMA = "research"


def upgrade() -> None:
    op.create_table(
        "signal_events",
        sa.Column("signal", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("bar_ts", app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(length=8), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("timeframe", sa.String(length=8), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=True),
        sa.Column("target", sa.Float(), nullable=True),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("late", sa.Boolean(), nullable=False),
        sa.Column("recorded_at", app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("signal", "symbol", "bar_ts", "action"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_signal_events_recorded_at", "signal_events", ["recorded_at"], unique=False, schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_index("ix_signal_events_recorded_at", table_name="signal_events", schema=SCHEMA)
    op.drop_table("signal_events", schema=SCHEMA)
