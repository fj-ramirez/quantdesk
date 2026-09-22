"""T101: the expiry dimension.

`gex.gex_by_strike` was `(snapshot_id, filter, strike, call_gex, put_gex, net_gex)`, and the
only expiry slicing anywhere in Postgres was the `ZERO_DTE` / `EX_ZERO_DTE` filter. So for any
question with a horizon -- "what happens this week" -- the desk could not say how much of a
wall expires Friday versus October versus January. On 2026-09-21 the QQQ 740 wall was +662mn
and nothing stored said what fraction survived that Friday, which is the first thing anyone
asks about a wall.

Per-contract rows live only in Parquet by design (invariant 5), so this is a rollup written at
capture time, not a schema violation. Two shapes, chosen by measurement rather than taste:

**Four horizon columns on `gex_by_strike`, not a `(strike, expiry)` table.** The cross product
is the shape that answers everything, and it does not fit. Measured against the stored
2026-09-21 session -- 144 snapshots, 1,743,058 contracts -- one row per (strike, expiry, filter)
is ~2.61 million rows *for that single day*, against the 266,050 rows `gex_by_strike` holds for
the project's entire history to date. That is 18.6x the per-strike rollup, every day, and it
would make this the largest object in the database within a week. Four columns on the rows that
already exist cost no new rows at all and answer the question that was actually asked.

**`gex_by_expiry` for the exact-expiry half.** One row per expiry per filter -- ~5.2k rows a
day across the whole universe, because it does not multiply by strike. `engine.by_expiry` has
computed this since T08 and the read API has returned it for a live snapshot since T11; it was
simply never persisted, so no historical term-structure question could be answered.

The horizon columns are nullable: rows written before this migration have no split and cannot
get one without reopening their Parquet file. Null means "not computed for this row", never
zero -- the distinction T100 exists to protect.

T76's `ALTER DEFAULT PRIVILEGES` covers the `gex` schema, so `quantdesk_ro` can read the new
table the moment it exists and no grant belongs in this file.

Revision ID: f1a2b3c4d5e6
Revises: d3c8a1f57b90
Create Date: 2026-09-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.core.schemas import SCHEMA_GEX

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "d3c8a1f57b90"
branch_labels: str | None = None
depends_on: str | None = None

_HORIZONS = ("net_gex_0dte", "net_gex_this_week", "net_gex_next_30d", "net_gex_beyond_30d")


def upgrade() -> None:
    for column in _HORIZONS:
        op.add_column(
            "gex_by_strike",
            sa.Column(column, sa.Float(), nullable=True),
            schema=SCHEMA_GEX,
        )

    op.create_table(
        "gex_by_expiry",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("filter", sa.String(length=32), nullable=False),
        sa.Column("expiry", sa.Date(), nullable=False),
        sa.Column("dte", sa.Integer(), nullable=False),
        sa.Column("call_gex", sa.Float(), nullable=False),
        sa.Column("put_gex", sa.Float(), nullable=False),
        sa.Column("net_gex", sa.Float(), nullable=False),
        sa.Column("abs_gex", sa.Float(), nullable=False),
        sa.Column("contracts", sa.Integer(), nullable=False),
        sa.Column("open_interest", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], [f"{SCHEMA_GEX}.snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "filter", "expiry", name="uq_gex_by_expiry_snapshot_filter_expiry"
        ),
        schema=SCHEMA_GEX,
    )
    op.create_index(
        "ix_gex_by_expiry_snapshot_filter",
        "gex_by_expiry",
        ["snapshot_id", "filter"],
        schema=SCHEMA_GEX,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_gex_by_expiry_snapshot_filter", table_name="gex_by_expiry", schema=SCHEMA_GEX
    )
    op.drop_table("gex_by_expiry", schema=SCHEMA_GEX)
    for column in reversed(_HORIZONS):
        op.drop_column("gex_by_strike", column, schema=SCHEMA_GEX)
