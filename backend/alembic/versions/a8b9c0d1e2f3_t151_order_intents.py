"""T151: the executor's tables, broker.order_intents and broker.strategy_state.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-10-09

The executor writes an intent before it sends anything, and records the fill on the same row,
so the table is both the audit trail and the trade journal. Read-only access for the MCP role
follows from the schema's default privileges (f7a8b9c0d1e2).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.modules.gex.models.db
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "broker"


def _tz() -> sa.types.TypeEngine:
    return app.modules.gex.models.db.UTCDateTime(timezone=True)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_table(
        "order_intents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("key", sa.String(96), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("volume", sa.Float(), nullable=False),
        sa.Column("due_at", _tz(), nullable=False),
        sa.Column("expires_at", _tz(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", _tz(), nullable=False),
        sa.Column("sent_at", _tz(), nullable=True),
        sa.Column("sl", sa.Float(), nullable=True),
        sa.Column("ticket", sa.BigInteger(), nullable=True),
        sa.Column("fill_price", sa.Float(), nullable=True),
        sa.Column("quote_bid", sa.Float(), nullable=True),
        sa.Column("quote_ask", sa.Float(), nullable=True),
        sa.Column("pnl_pct", sa.Float(), nullable=True),
        sa.Column("account_login", sa.BigInteger(), nullable=True),
        sa.Column("account_mode", sa.String(8), nullable=True),
        sa.Column("note", sa.String(512), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
        schema=SCHEMA,
    )
    op.create_table(
        "strategy_state",
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(256), nullable=True),
        sa.Column("updated_at", _tz(), nullable=False),
        sa.PrimaryKeyConstraint("strategy"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    count = op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {SCHEMA}.order_intents")).scalar()
    if count:
        raise RuntimeError(
            f"downgrade: {SCHEMA}.order_intents holds {count} row(s), the executor's journal. "
            "Export it before dropping."
        )
    op.drop_table("strategy_state", schema=SCHEMA)
    op.drop_table("order_intents", schema=SCHEMA)
