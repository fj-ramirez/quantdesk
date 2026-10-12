"""T142/T153: gex.level_events and gex.level_event_scores -- the gamma-level forward record.

Every intraday flip cross (`docs/edges/flip-cross.md`) and wall bounce
(`docs/edges/wall-bounce.md`) under their frozen rules, and, once the session has ended, what
price did afterwards: fixed-horizon returns on the desk symbol and the excursion path (MAE/MFE
swings) on the traded CFD.

Both tables are insert-only. No backfill: a forward record dated before the logger was live
would be a backtest wearing a forward test's name, and the sessions before 2026-10-09 are the
window the flip-cross leaning was found in.

T76's `ALTER DEFAULT PRIVILEGES` covers the `gex` schema, so `quantdesk_ro` can read both tables
the moment they exist and no grant belongs in this file.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-10-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.modules.gex.models.db
from alembic import op

revision: str = "b9c0d1e2f3a4"
down_revision: str | Sequence[str] | None = "a8b9c0d1e2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Matches `app.core.schemas.SCHEMA_GEX`, spelled literally: a revision records what was applied
#: to a real database and must not change meaning when a constant is renamed later.
SCHEMA = "gex"


def upgrade() -> None:
    utc = app.modules.gex.models.db.UTCDateTime(timezone=True)
    op.create_table(
        "level_events",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("bar_ts", utc, nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("entry_at", utc, nullable=False),
        sa.Column("spot", sa.Float(), nullable=False),
        sa.Column("level", sa.Float(), nullable=False),
        sa.Column("level_kind", sa.String(length=16), nullable=False),
        sa.Column("touch_ts", utc, nullable=True),
        sa.Column("net_gex", sa.Float(), nullable=True),
        sa.Column("capture_at", utc, nullable=False),
        sa.Column("spx_flip", sa.Float(), nullable=True),
        sa.Column("spx_side", sa.Integer(), nullable=True),
        sa.Column("in_window", sa.Boolean(), nullable=False),
        sa.Column("alerted", sa.Boolean(), nullable=False),
        sa.Column("recorded_at", utc, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "bar_ts", "key"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_level_events_session_date", "level_events", ["session_date"], unique=False, schema=SCHEMA
    )
    op.create_table(
        "level_event_scores",
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("bar_ts", utc, nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("ret_15m_bps", sa.Float(), nullable=True),
        sa.Column("ret_30m_bps", sa.Float(), nullable=True),
        sa.Column("ret_60m_bps", sa.Float(), nullable=True),
        sa.Column("ret_close_bps", sa.Float(), nullable=True),
        sa.Column("cfd_symbol", sa.String(length=32), nullable=True),
        sa.Column("path_entry", sa.Float(), nullable=True),
        sa.Column("path_unit", sa.Float(), nullable=True),
        sa.Column("path_swings", sa.Text(), nullable=True),
        sa.Column("path_mae", sa.Float(), nullable=True),
        sa.Column("path_mfe", sa.Float(), nullable=True),
        sa.Column("path_close", sa.Float(), nullable=True),
        sa.Column("path_end_at", utc, nullable=True),
        sa.Column("path_note", sa.String(length=128), nullable=True),
        sa.Column("scored_at", utc, nullable=False),
        sa.PrimaryKeyConstraint("symbol", "bar_ts", "key"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("level_event_scores", schema=SCHEMA)
    op.drop_index("ix_level_events_session_date", table_name="level_events", schema=SCHEMA)
    op.drop_table("level_events", schema=SCHEMA)
