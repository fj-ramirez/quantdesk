"""create decisions table

Revision ID: b2d4f6a8c0e1
Revises: 961d52e4d010
Create Date: 2026-09-10 20:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d4f6a8c0e1"
down_revision: str | Sequence[str] | None = "961d52e4d010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "decisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("underlying", sa.String(length=16), nullable=False),
        sa.Column("filter", sa.String(length=32), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("snapshots.id"), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("decided_on", sa.Date(), nullable=False),
        # UTCDateTime is a TypeDecorator over DateTime(timezone=True); the migration declares
        # the underlying column type, same as the snapshots revision does.
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("setup", sa.String(length=16), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("grade", sa.String(length=1), nullable=False),
        sa.Column("entry", sa.Float(), nullable=False),
        sa.Column("stop", sa.Float(), nullable=False),
        sa.Column("target", sa.Float(), nullable=False),
        sa.Column("target_2", sa.Float(), nullable=True),
        sa.Column("spot", sa.Float(), nullable=False),
        sa.Column("atr14", sa.Float(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("fill", sa.Float(), nullable=True),
        sa.Column("triggered_on", sa.Date(), nullable=True),
        sa.Column("resolved_on", sa.Date(), nullable=True),
        sa.Column("bars_held", sa.Integer(), nullable=True),
        sa.Column("mfe_r", sa.Float(), nullable=True),
        sa.Column("mae_r", sa.Float(), nullable=True),
        sa.Column("result_r", sa.Float(), nullable=True),
        sa.Column("mark_r", sa.Float(), nullable=True),
        sa.Column("evaluated_through", sa.Date(), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "filter", "key", name="uq_decisions_snapshot_filter_key"),
    )
    op.create_index("ix_decisions_underlying_decided_on", "decisions", ["underlying", "decided_on"])
    op.create_index("ix_decisions_outcome", "decisions", ["outcome"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_decisions_outcome", table_name="decisions")
    op.drop_index("ix_decisions_underlying_decided_on", table_name="decisions")
    op.drop_table("decisions")
