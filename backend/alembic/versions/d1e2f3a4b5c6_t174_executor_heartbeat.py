"""T174: broker.executor_heartbeat, the executor's liveness row.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-10-15

The executor overwrites one row per tick; `capture-watch` alerts when it goes stale while a leg
is due. Read-only access for the MCP role follows from the schema's default privileges
(f7a8b9c0d1e2).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.modules.gex.models.db
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | Sequence[str] | None = "c0d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "broker"


def _tz() -> sa.types.TypeEngine:
    return app.modules.gex.models.db.UTCDateTime(timezone=True)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.create_table(
        "executor_heartbeat",
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("beat_at", _tz(), nullable=False),
        sa.Column("started_at", _tz(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("strategies", sa.String(256), nullable=False),
        sa.PrimaryKeyConstraint("service"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.drop_table("executor_heartbeat", schema=SCHEMA)
