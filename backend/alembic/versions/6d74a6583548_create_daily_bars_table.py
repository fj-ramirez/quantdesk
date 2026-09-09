"""create daily_bars table

Revision ID: 6d74a6583548
Revises: e5e7495e6791
Create Date: 2026-09-09 10:47:35.218544

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6d74a6583548"
down_revision: str | Sequence[str] | None = "e5e7495e6791"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "daily_bars",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        # None means "vendor did not report volume"; 0 means "reported, genuinely zero"
        # (^VIX) -- see app.models.db.DailyBar's docstring. Never collapse the two.
        # BigInteger: SPX/^GSPC volume (~4.97e9) overflows Postgres int4. See that same
        # docstring -- SQLite accepts it, Postgres does not.
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date", name="uq_daily_bars_symbol_date"),
    )
    op.create_index("ix_daily_bars_symbol", "daily_bars", ["symbol"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_daily_bars_symbol", table_name="daily_bars")
    op.drop_table("daily_bars")
