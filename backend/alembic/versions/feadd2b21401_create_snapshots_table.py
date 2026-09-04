"""create snapshots table

Revision ID: feadd2b21401
Revises:
Create Date: 2026-09-04 14:22:39.847918

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.models.db
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "feadd2b21401"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("underlying", sa.String(length=16), nullable=False),
        sa.Column("captured_at", app.models.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("spot", sa.Float(), nullable=False),
        sa.Column("contract_count", sa.Integer(), nullable=False),
        sa.Column("parquet_path", sa.String(length=512), nullable=False),
        sa.Column("is_eod", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_snapshots_underlying_captured_at", "snapshots", ["underlying", "captured_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_snapshots_underlying_captured_at", table_name="snapshots")
    op.drop_table("snapshots")
