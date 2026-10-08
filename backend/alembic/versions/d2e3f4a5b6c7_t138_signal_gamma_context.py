"""T138: dealer-gamma context on research.signal_events.

Six nullable columns recording what the desk knew about dealer gamma at each signal's bar --
for the ETF it captures for that market (ES->SPY, NQ->QQQ, YM->DIA, RTY->IWM; an ETF signal
reads its own). Context, never a filter: the point is that the forward record can later be split
by gamma regime to see whether watching it would have helped.

No backfill. The rows recorded before this revision were alerted without the context, and
inventing it now from today's view of the snapshot table would fabricate what the alert said.

Revision ID: d2e3f4a5b6c7
Revises: c9d1e2f3a4b5
Create Date: 2026-10-07

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c9d1e2f3a4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "research"

#: (name, type) -- built fresh in each direction rather than sharing Column objects.
_COLUMNS = (
    ("gamma_proxy", sa.String(length=16)),
    ("gamma_session_date", sa.Date()),
    ("gamma_net_gex", sa.Float()),
    ("gamma_spot", sa.Float()),
    ("gamma_flip_point", sa.Float()),
    ("gamma_note", sa.String(length=128)),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("signal_events", sa.Column(name, type_, nullable=True), schema=SCHEMA)


def downgrade() -> None:
    for name, _type in reversed(_COLUMNS):
        op.drop_column("signal_events", name, schema=SCHEMA)
