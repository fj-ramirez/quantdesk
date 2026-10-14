"""T158: delete COT observations stored with an as_of earlier than their publication.

Until T158, `CftcAdapter.release_as_of` gave every report the Friday of its week. CFTC
publishes later than that in three cases: when the Friday is a holiday or the day after
Thanksgiving (the 2025-11-25 report went out Monday 12-01), when the government closed by
order, and during the 2013, 2018-19 and 2025 shutdowns, when reports came out weeks late. Each
of those rows says the value was knowable before it was public.

**This is the one sanctioned exception to invariant 10**, decided by the user on 2026-10-13
(plans/cot/DECISIONS.md D4). The invariant forbids overwriting a vintage because a vintage
records what was known and when. These rows record neither: no one could have known the value
at that instant, so the row is a defect, not a revision. Keeping it beside the corrected row
would leave every point-in-time read in the gap returning a value that was not yet public,
which is the exact failure the invariant exists to prevent.

What is deleted, and only this: rows of `pos.cot.*` (raw and `.pctile`) whose `as_of` is
earlier than 15:30 ET on the corrected `release_date(value_date)`. A percentile row's as_of is
the latest input as_of across its window, and the corrected release dates never go backwards,
so the same test catches exactly the percentile rows built from an early input. Rows whose
as_of is correct or later are untouched. The next nightly ingest inserts the corrected raw
rows, and the derivation then rebuilds the percentiles from them.

It imports `release_date` from the adapter, the same deliberate exception T102 made for the
trading calendar: transcribing the catch-up schedules into this file would fork them. Re-run on
a fresh database it finds no rows to delete.

Downgrade does nothing. The deleted rows were wrong, and the old rule recreates them if the
adapter is ever reverted.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-10-13
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from alembic import op
from app.core.schemas import SCHEMA_TERMINAL
from app.modules.terminal.adapters.cftc import RELEASE_LOCAL_TIME, release_date

revision: str = "c0d1e2f3a4b5"
down_revision: str | None = "b9c0d1e2f3a4"
branch_labels: str | None = None
depends_on: str | None = None

NY = ZoneInfo("America/New_York")


def _published(value_date: dt.date) -> dt.datetime:
    return dt.datetime.combine(release_date(value_date), RELEASE_LOCAL_TIME, tzinfo=NY)


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT series_id, value_date, as_of FROM {SCHEMA_TERMINAL}.observations "
            "WHERE series_id LIKE 'pos.cot.%'"
        )
    ).all()
    early = [
        {"s": s, "v": v, "a": a} for s, v, a in rows if a < _published(v)
    ]
    if early:
        bind.execute(
            sa.text(
                f"DELETE FROM {SCHEMA_TERMINAL}.observations "
                "WHERE series_id = :s AND value_date = :v AND as_of = :a"
            ),
            early,
        )
    print(f"T158: deleted {len(early)} of {len(rows)} pos.cot.* rows with an early as_of")


def downgrade() -> None:
    pass
