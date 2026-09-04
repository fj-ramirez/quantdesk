"""create gex_levels and gex_by_strike tables

Revision ID: e5e7495e6791
Revises: feadd2b21401
Create Date: 2026-09-04 15:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

import app.models.db
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5e7495e6791"
down_revision: str | Sequence[str] | None = "feadd2b21401"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "gex_levels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("filter", sa.String(length=32), nullable=False),
        # Every numeric level below is nullable on purpose -- see GexLevel's docstring in
        # app/models/db.py. A ZERO_DTE filter on an EOD (16:20 ET) capture legitimately has
        # every one of these as NULL because every same-day contract has already expired.
        sa.Column("net_gex", sa.Float(), nullable=True),
        sa.Column("call_wall", sa.Float(), nullable=True),
        sa.Column("call_wall_gex", sa.Float(), nullable=True),
        sa.Column("put_wall", sa.Float(), nullable=True),
        sa.Column("put_wall_gex", sa.Float(), nullable=True),
        sa.Column("max_abs_strike", sa.Float(), nullable=True),
        sa.Column("max_call_gex_strike", sa.Float(), nullable=True),
        sa.Column("max_put_gex_strike", sa.Float(), nullable=True),
        sa.Column("flip_point", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=True),
        sa.Column("computed_at", app.models.db.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "filter", name="uq_gex_levels_snapshot_filter"),
    )
    op.create_index("ix_gex_levels_snapshot_id", "gex_levels", ["snapshot_id"])

    op.create_table(
        "gex_by_strike",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("filter", sa.String(length=32), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("call_gex", sa.Float(), nullable=False),
        sa.Column("put_gex", sa.Float(), nullable=False),
        sa.Column("net_gex", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "filter", "strike", name="uq_gex_by_strike_snapshot_filter_strike"
        ),
    )
    op.create_index("ix_gex_by_strike_snapshot_filter", "gex_by_strike", ["snapshot_id", "filter"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_gex_by_strike_snapshot_filter", table_name="gex_by_strike")
    op.drop_table("gex_by_strike")
    op.drop_index("ix_gex_levels_snapshot_id", table_name="gex_levels")
    op.drop_table("gex_levels")
