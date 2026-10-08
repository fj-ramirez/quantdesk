"""T139: terminal.releases becomes a point-in-time calendar.

T79 created `releases` keyed on `release_id` alone, with `series_id NOT NULL`, and left it empty
on purpose. T139 fills it, which needs three things that shape could not hold:

* **`as_of` in the primary key**, so a reschedule or a revised forecast adds a vintage instead
  of overwriting one (invariant 10's rule, applied to the calendar);
* **title, country, impact, status, starts_on and the raw text** of forecast and prior,
  because speeches and auctions are not series and a compound value must not be guessed;
* **a nullable `series_id`**, for the same reason.

A primary-key change is not additive, so the table is recreated rather than altered. **That is
only safe because it is empty**, and the migration checks instead of assuming: if any row
exists it stops and says so rather than dropping data. The same guard runs on the way down.

The recreated table is readable by `quantdesk_ro` without a grant here: T76's `ALTER DEFAULT
PRIVILEGES` covers every table later created in the `terminal` schema.

Revision ID: e6f7a8b9c0d1
Revises: d2e3f4a5b6c7
Create Date: 2026-10-08

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.modules.gex.models.db
from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "terminal"


def _tz() -> sa.types.TypeEngine:
    return app.modules.gex.models.db.UTCDateTime(timezone=True)


def _refuse_if_populated(direction: str) -> None:
    count = op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {SCHEMA}.releases")).scalar()
    if count:
        raise RuntimeError(
            f"T139 {direction}: {SCHEMA}.releases holds {count} row(s). This migration recreates "
            "the table and is only safe on an empty one; it will not drop data. Export the rows, "
            "or write a migration that carries them across."
        )


def upgrade() -> None:
    _refuse_if_populated("upgrade")
    op.drop_table("releases", schema=SCHEMA)
    op.create_table(
        "releases",
        sa.Column("release_id", sa.Text(), nullable=False),
        sa.Column("as_of", _tz(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("scheduled_at", _tz(), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("series_id", sa.Text(), nullable=True),
        sa.Column("consensus", sa.Float(), nullable=True),
        sa.Column("consensus_raw", sa.Text(), nullable=True),
        sa.Column("consensus_as_of", _tz(), nullable=True),
        sa.Column("prior", sa.Float(), nullable=True),
        sa.Column("prior_raw", sa.Text(), nullable=True),
        sa.Column("actual", sa.Float(), nullable=True),
        sa.Column("actual_as_of", _tz(), nullable=True),
        sa.Column("source_batch", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("release_id", "as_of"),
        schema=SCHEMA,
    )
    op.create_index("releases_scheduled_at", "releases", ["scheduled_at"], schema=SCHEMA)
    op.create_index("releases_source_asof", "releases", ["source", "as_of"], schema=SCHEMA)


def downgrade() -> None:
    _refuse_if_populated("downgrade")
    op.drop_index("releases_source_asof", table_name="releases", schema=SCHEMA)
    op.drop_index("releases_scheduled_at", table_name="releases", schema=SCHEMA)
    op.drop_table("releases", schema=SCHEMA)
    op.create_table(
        "releases",
        sa.Column("release_id", sa.Text(), nullable=False),
        sa.Column("series_id", sa.Text(), nullable=False),
        sa.Column("scheduled_at", _tz(), nullable=False),
        sa.Column("consensus", sa.Float(), nullable=True),
        sa.Column("consensus_as_of", _tz(), nullable=True),
        sa.Column("prior", sa.Float(), nullable=True),
        sa.Column("actual", sa.Float(), nullable=True),
        sa.Column("actual_as_of", _tz(), nullable=True),
        sa.PrimaryKeyConstraint("release_id"),
        schema=SCHEMA,
    )
