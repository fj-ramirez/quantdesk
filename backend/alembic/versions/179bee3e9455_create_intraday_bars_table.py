"""create intraday_bars table

Revision ID: 179bee3e9455
Revises: c7a1e93b5d02
Create Date: 2026-09-11 15:20:00.000000

TASKS.md T74 / plans/continuous-feed/05-intraday-bars.md. Purely additive: a new table, no
change to `daily_bars`, which deliberately keeps holding settled sessions only.

`ts` is `DateTime(timezone=True)` to match `app.models.db.UTCDateTime`'s Postgres impl --
invariant 4, tz-aware UTC everywhere. The unique key is what lets a still-forming bucket
converge across polls instead of accumulating a near-duplicate row every five minutes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "179bee3e9455"
down_revision: str | Sequence[str] | None = "c7a1e93b5d02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "intraday_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        # BigInteger for the same reason as daily_bars.volume: index volume overflows int4.
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "symbol", "interval", "ts", name="uq_intraday_bars_symbol_interval_ts"
        ),
    )
    op.create_index(
        "ix_intraday_bars_symbol_interval_ts",
        "intraday_bars",
        ["symbol", "interval", "ts"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_intraday_bars_symbol_interval_ts", table_name="intraday_bars")
    op.drop_table("intraday_bars")
