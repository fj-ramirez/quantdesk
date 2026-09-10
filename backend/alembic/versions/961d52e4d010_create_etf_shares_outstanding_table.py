"""create etf_shares_outstanding table

Revision ID: 961d52e4d010
Revises: 6d74a6583548
Create Date: 2026-09-09 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "961d52e4d010"
down_revision: str | Sequence[str] | None = "6d74a6583548"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "etf_shares_outstanding",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        # BigInteger for the same reason as daily_bars.volume -- see
        # app.models.db.EtfSharesOutstanding's docstring.
        sa.Column("shares", sa.BigInteger(), nullable=False),
        sa.Column("nav", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "date", name="uq_etf_shares_outstanding_symbol_date"),
    )
    op.create_index("ix_etf_shares_outstanding_symbol", "etf_shares_outstanding", ["symbol"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_etf_shares_outstanding_symbol", table_name="etf_shares_outstanding")
    op.drop_table("etf_shares_outstanding")
