"""T79: the terminal module's six tables, in the `terminal` schema.

xactx's point-in-time store, ported from DuckDB. T76 created the empty `terminal` namespace and
the `quantdesk_ro` grants covering it, so no grant statement belongs here.

Transcribed from `store/schema.sql`, whose header promised the DDL was "kept close to ANSI so
the same tables can be created in Postgres later without a redesign". **That promise held**:
the only type change the port needed was `DOUBLE` -> `DOUBLE PRECISION`. `TEXT`, `BOOLEAN`,
`INTEGER`, `DATE` and `TIMESTAMPTZ` came across untouched, and no DuckDB-specific construct
(`ASOF JOIN`, `QUALIFY`, `PIVOT`, `arg_max`, `EXCLUDE (...)`) appears anywhere in the module.

**`observations`' three-column primary key is the module's invariant**, not an implementation
detail: `(series_id, value_date, as_of)` is what lets one (series, date) hold many vintages
where none overwrites another, which is what lets any screen re-render the world as it looked
at a past moment. Anything that made `as_of` updatable would destroy it.

The `import app.modules.gex.models.db` below is **not** dead: autogenerate emits the
fully-qualified `UTCDateTime` on every timestamp column but does not add its import, so the
generated file raised `NameError` on the first `as_of` column. Same trap as T77's revision --
which is why generated migrations in this repo are read line by line before they are kept.

Data does not move here. The 28 MB DuckDB file is imported afterwards by
`scripts/migrate_xactx.py`, deliberately outside the chain: it is a one-shot import from a file
that exists on one laptop, and it must be independently re-runnable and verifiable.

Revision ID: b095e1c94027
Revises: 27cf03abcbc1
Create Date: 2026-09-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.modules.gex.models.db
from alembic import op

revision: str = "b095e1c94027"
down_revision: str | Sequence[str] | None = "27cf03abcbc1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('edge_definitions',
    sa.Column('from_series', sa.Text(), nullable=False),
    sa.Column('to_series', sa.Text(), nullable=False),
    sa.Column('expected_sign', sa.Integer(), nullable=False),
    sa.Column('typical_lag_days', sa.Integer(), nullable=False),
    sa.Column('chain', sa.Text(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('from_series', 'to_series'),
    schema='terminal'
    )
    op.create_table('edge_stats',
    sa.Column('from_series', sa.Text(), nullable=False),
    sa.Column('to_series', sa.Text(), nullable=False),
    sa.Column('as_of', app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('value_date', sa.Date(), nullable=False),
    sa.Column('beta', sa.Float(), nullable=True),
    sa.Column('beta_window', sa.Integer(), nullable=False),
    sa.Column('beta_t_stat', sa.Float(), nullable=True),
    sa.Column('r_squared', sa.Float(), nullable=True),
    sa.Column('corr', sa.Float(), nullable=True),
    sa.Column('corr_percentile', sa.Float(), nullable=True),
    sa.Column('corr_history_n', sa.Integer(), nullable=True),
    sa.Column('sign_conflict', sa.Boolean(), nullable=True),
    sa.Column('significant', sa.Boolean(), nullable=True),
    sa.Column('n_obs', sa.Integer(), nullable=False),
    sa.Column('source_batch', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('from_series', 'to_series', 'as_of'),
    schema='terminal'
    )
    op.create_table('ingest_batches',
    sa.Column('source_batch', sa.Text(), nullable=False),
    sa.Column('started_at', app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('finished_at', app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('adapter', sa.Text(), nullable=True),
    sa.Column('args', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('source_batch'),
    schema='terminal'
    )
    op.create_table('observations',
    sa.Column('series_id', sa.Text(), nullable=False),
    sa.Column('value_date', sa.Date(), nullable=False),
    sa.Column('as_of', app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('value', sa.Float(), nullable=False),
    sa.Column('as_of_basis', sa.Text(), nullable=False),
    sa.Column('source_batch', sa.Text(), nullable=False),
    sa.PrimaryKeyConstraint('series_id', 'value_date', 'as_of'),
    schema='terminal'
    )
    op.create_index('observations_series_asof', 'observations', ['series_id', 'as_of'], unique=False, schema='terminal')
    op.create_table('releases',
    sa.Column('release_id', sa.Text(), nullable=False),
    sa.Column('series_id', sa.Text(), nullable=False),
    sa.Column('scheduled_at', app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=False),
    sa.Column('consensus', sa.Float(), nullable=True),
    sa.Column('consensus_as_of', app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=True),
    sa.Column('prior', sa.Float(), nullable=True),
    sa.Column('actual', sa.Float(), nullable=True),
    sa.Column('actual_as_of', app.modules.gex.models.db.UTCDateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('release_id'),
    schema='terminal'
    )
    op.create_table('series_metadata',
    sa.Column('series_id', sa.Text(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_code', sa.Text(), nullable=False),
    sa.Column('asset_class', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=False),
    sa.Column('unit', sa.Text(), nullable=False),
    sa.Column('frequency', sa.Text(), nullable=False),
    sa.Column('default_transform', sa.Text(), nullable=False),
    sa.Column('revisable', sa.Boolean(), nullable=False),
    sa.Column('vintage_source', sa.Text(), nullable=False),
    sa.Column('snapshot_tz', sa.Text(), nullable=True),
    sa.Column('snapshot_local_time', sa.Text(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('series_id'),
    schema='terminal'
    )


def downgrade() -> None:
    op.drop_table('series_metadata', schema='terminal')
    op.drop_table('releases', schema='terminal')
    op.drop_index('observations_series_asof', table_name='observations', schema='terminal')
    op.drop_table('observations', schema='terminal')
    op.drop_table('ingest_batches', schema='terminal')
    op.drop_table('edge_stats', schema='terminal')
    op.drop_table('edge_definitions', schema='terminal')
