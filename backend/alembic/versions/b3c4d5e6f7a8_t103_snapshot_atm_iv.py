"""T103: persist ATM implied vol per snapshot.

`gex.report.iv_regime` has computed a constant-maturity ~30-day ATM vol since T37 -- ATM
interpolated across strike to spot inside `ATM_MONEYNESS_WINDOW`, then interpolated between
the bracketing expiries in **total variance** (`sigma^2 * T` linear in `T`), as a decimal
fraction. It was never stored.

So every consumer that wanted it reopened the snapshot's Parquet file and recomputed, which is
why `api/scan._lookup_iv30` is documented as the dominant cost of the trend endpoint at roughly
3 seconds across the universe. The inputs are in memory at capture; this is free there.

The 2026-09-21 review read the decision engine's own words -- "no implied-versus-realized view
is available" -- as evidence that no IV existed anywhere on this desk. It did exist and was
being used: decision 50 carried IV/RV 1.27. That string was a fallback that fired whenever the
ratio sat *between* the rich and cheap thresholds, so it reported a neutral measurement as a
missing one. The prose is fixed alongside this migration; the columns are what stop the
number being thrown away and recomputed.

Nullable, and null means "not computable for this snapshot" -- an empty chain, or no usable IV
in the ATM window. Never zero, and explicitly never carried forward from an earlier capture: a
stale vol looks exactly like a fresh one, which is the failure this desk keeps finding in other
forms.

Existing rows get null and are filled by `gex/backfill.py --recompute`, which reopens the
Parquet files that are still on disk. Unlike a missed capture, this genuinely is recoverable.

Revision ID: b3c4d5e6f7a8
Revises: a7b8c9d0e1f2
Create Date: 2026-09-22
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from app.core.schemas import SCHEMA_GEX

revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | None = None
depends_on: str | None = None

_COLUMNS = (
    ("atm_iv", sa.Float()),
    ("atm_iv_target_dte", sa.Integer()),
    ("atm_iv_lower_dte", sa.Integer()),
    ("atm_iv_upper_dte", sa.Integer()),
    ("atm_iv_interpolated", sa.Boolean()),
    ("atm_iv_contracts", sa.Integer()),
)


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("snapshots", sa.Column(name, type_, nullable=True), schema=SCHEMA_GEX)


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("snapshots", name, schema=SCHEMA_GEX)
