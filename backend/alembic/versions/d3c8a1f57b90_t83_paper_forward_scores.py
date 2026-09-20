"""T83: persist the paper watchlist's forward scores.

`paper.forward_stats` has computed forward performance correctly since EdgeLab was ported --
signals warmed up on full history, performance sliced to bars strictly after `promoted_at` --
but the result was rendered into `report.html` and discarded. The database kept promotion-time
gates only, so `/api/research/paper` and the MCP connector could say what a candidate looked
like the day it was promoted and nothing about what happened afterwards.

Invariant 9 calls the watchlist the only genuinely out-of-sample evidence in the module. A
watchlist that is promoted and then never scored is the authoritative-looking artifact that
invariant exists to prevent, so this table is the missing half of it.

**Append-only, `(hash, scored_at)`.** One row per candidate per scoring run, never updated --
the rule `terminal.observations` follows for vintages (invariant 10) and the GEX decision log
follows for recorded levels. The trajectory *is* the evidence: a candidate promoted at OOS
Sharpe 2.8 reading 1.1 two months later is a decaying edge, and an UPDATE keeping only the
newest number would erase exactly that, silently.

No foreign key to `paper_candidates`, on purpose. That table is written under `ON CONFLICT DO
NOTHING` by whichever of the worker and the scheduled task gets there first; a constraint here
would let a scoring run fail on a candidate row that is simply not committed yet. The join is
by hash regardless.

No backfill. Forward performance is a function of when it was measured, and inventing a score
dated today for bars that arrived over the past three months would fabricate the one thing
this table exists to record honestly. The first real row lands on the next cycle.

T76's `ALTER DEFAULT PRIVILEGES` covers the `research` schema, so `quantdesk_ro` can read this
table the moment it exists and no grant belongs in this file.

Revision ID: d3c8a1f57b90
Revises: b095e1c94027
Create Date: 2026-09-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.modules.gex.models.db
from alembic import op

revision: str = "d3c8a1f57b90"
down_revision: str | Sequence[str] | None = "b095e1c94027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Matches `app.core.schemas.SCHEMA_RESEARCH`, spelled literally for the reason every revision
#: spells things literally: this file records what was applied to a real database and must not
#: change meaning when a constant is renamed later.
SCHEMA = "research"


def upgrade() -> None:
    op.create_table(
        "paper_scores",
        sa.Column("hash", sa.String(length=24), nullable=False),
        # Not the last bar covered -- the moment the measurement was taken. Two scores of one
        # candidate differ by the bars that arrived between them, so this is the axis the
        # trajectory is read along. `UTCDateTime` for invariant 4, same as `promoted_at`.
        sa.Column(
            "scored_at",
            app.modules.gex.models.db.UTCDateTime(timezone=True),
            nullable=False,
        ),
        # Real counts, so 0 here means zero and is honest. Read these before any float below:
        # a forward Sharpe over eleven bars is noise wearing a number's clothes.
        sa.Column("fwd_days", sa.Integer(), nullable=True),
        sa.Column("fwd_bars", sa.Integer(), nullable=True),
        # Nullable, and null whenever there were fewer than two forward bars. `None` is
        # unknown; `0.0` would read as flat. Invariant 3's rule, and what the MCP connector
        # promises when it says a null is never a zero anywhere in this database.
        sa.Column("fwd_sharpe", sa.Float(), nullable=True),
        sa.Column("fwd_return", sa.Float(), nullable=True),
        sa.Column("fwd_max_dd", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("hash", "scored_at"),
        schema=SCHEMA,
    )
    # Serves both queries that exist: the latest score per candidate (`DISTINCT ON (hash) ORDER
    # BY hash, scored_at DESC`) and one candidate's trajectory. Descending `scored_at` matches
    # the order both read in.
    op.create_index(
        "ix_paper_scores_hash_scored_at",
        "paper_scores",
        ["hash", sa.text("scored_at DESC")],
        unique=False,
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_paper_scores_hash_scored_at", table_name="paper_scores", schema=SCHEMA)
    op.drop_table("paper_scores", schema=SCHEMA)
