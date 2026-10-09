"""charter-mt5 merge: the `broker` schema, its six tables, the `levels` view and the grants.

While `experiment/charter-mt5` was an experiment, the `broker-ingest` worker created all of this
itself with `create_all` (decision 10 of `plans/charter-mt5/README.md`), so that deploying the
branch never moved the shared database's `alembic_version`. This revision is that promise kept
at merge: the schema becomes part of the one chain, and the worker stops creating anything.

**The homeserver already has every object here**, created by the worker, with rows in them.
So every step is conditional rather than assumed: `CREATE SCHEMA IF NOT EXISTS`, a table is
created only if it is absent, the view is `CREATE OR REPLACE`, and the grants are idempotent.
On that database the upgrade changes nothing except the grants' grantor and `alembic_version`;
on a fresh one it builds the lot. The column definitions are the worker's `create_all` ones,
transcribed, so the two paths produce the same tables.

The grants repeat T76's three statements for one more schema. T76's `ALTER DEFAULT PRIVILEGES`
covered only the three schemas that existed then, so without them `quantdesk_ro` (the MCP
connector) could not read `broker.`.

`downgrade` refuses while `broker.bars` holds rows: years of broker M1 history cannot be
re-fetched quickly, and a downgrade is not the place to lose them. `DROP SCHEMA broker CASCADE`
by hand remains the deliberate way to undo the module.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-10-09

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import app.modules.gex.models.db
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | Sequence[str] | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "broker"
RO_ROLE = "quantdesk_ro"

#: Frozen copy of the view as T145 shipped it. A migration must not import it from app code,
#: which may change after this revision is applied.
LEVELS_VIEW_SQL = """
CREATE OR REPLACE VIEW broker.levels AS
SELECT s.id AS snapshot_id, s.underlying AS desk_symbol, s.captured_at, l.filter,
       b.cfd_symbol, b.method, b.proxy, b.kind AS basis_kind, b.at AS basis_at,
       s.captured_at - b.at AS basis_age, b."offset", b.ratio,
       l.spot AS desk_spot,
       CASE WHEN b.method = 'offset' THEN l.spot + b."offset" ELSE l.spot * b.ratio END AS spot,
       CASE WHEN b.method = 'offset' THEN l.call_wall + b."offset" ELSE l.call_wall * b.ratio END
           AS call_wall,
       CASE WHEN b.method = 'offset' THEN l.put_wall + b."offset" ELSE l.put_wall * b.ratio END
           AS put_wall,
       CASE WHEN b.method = 'offset' THEN l.flip_point + b."offset" ELSE l.flip_point * b.ratio END
           AS flip_point,
       CASE WHEN b.method = 'offset' THEN l.max_abs_strike + b."offset"
            ELSE l.max_abs_strike * b.ratio END AS max_abs_strike,
       l.net_gex, l.call_wall_gex, l.put_wall_gex,
       EXISTS (SELECT 1 FROM broker.rolls r WHERE r.cfd_symbol = b.cfd_symbol
               AND r.rolled_at > b.at AND r.rolled_at <= s.captured_at) AS roll_after_basis
FROM gex.gex_levels l
JOIN gex.snapshots s ON s.id = l.snapshot_id
JOIN LATERAL (
    SELECT * FROM broker.basis b
    WHERE b.desk_symbol = s.underlying AND b.at <= s.captured_at
    ORDER BY b.at DESC LIMIT 1
) b ON true
"""


def _tz() -> sa.types.TypeEngine:
    return app.modules.gex.models.db.UTCDateTime(timezone=True)


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _tables() -> dict[str, list[sa.schema.SchemaItem]]:
    return {
        "bars": [
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("timeframe", sa.String(4), nullable=False),
            sa.Column("ts", _tz(), nullable=False),
            sa.Column("open", sa.Float(), nullable=False),
            sa.Column("high", sa.Float(), nullable=False),
            sa.Column("low", sa.Float(), nullable=False),
            sa.Column("close", sa.Float(), nullable=False),
            sa.Column("tick_volume", sa.BigInteger(), nullable=False),
            sa.Column("spread", sa.Integer(), nullable=False),
            sa.Column("real_volume", sa.BigInteger(), nullable=False),
            sa.Column("offset_s", sa.Integer(), nullable=False),
            sa.Column("ingested_at", _tz(), nullable=False),
            sa.PrimaryKeyConstraint("symbol", "timeframe", "ts"),
        ],
        "symbol_specs": [
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("observed_at", _tz(), nullable=False),
            sa.Column("spec", postgresql.JSONB(), nullable=False),
            sa.PrimaryKeyConstraint("symbol", "observed_at"),
        ],
        "tick_files": [
            sa.Column("symbol", sa.String(32), nullable=False),
            sa.Column("hour", _tz(), nullable=False),
            sa.Column("path", sa.String(256), nullable=True),
            sa.Column("rows", sa.Integer(), nullable=False),
            sa.Column("offset_s", sa.Integer(), nullable=False),
            sa.Column("written_at", _tz(), nullable=False),
            sa.PrimaryKeyConstraint("symbol", "hour"),
        ],
        "clock_checks": [
            sa.Column("checked_at", _tz(), nullable=False),
            sa.Column("measured_s", sa.Integer(), nullable=True),
            sa.Column("model_s", sa.Integer(), nullable=False),
            sa.Column("agrees", sa.Boolean(), nullable=True),
            sa.Column("symbol", sa.String(32), nullable=True),
            sa.PrimaryKeyConstraint("checked_at"),
        ],
        "basis": [
            sa.Column("desk_symbol", sa.String(16), nullable=False),
            sa.Column("kind", sa.String(4), nullable=False),
            sa.Column("at", _tz(), nullable=False),
            sa.Column("cfd_symbol", sa.String(32), nullable=False),
            sa.Column("method", sa.String(8), nullable=False),
            sa.Column("proxy", sa.Boolean(), nullable=False),
            sa.Column("desk_price", sa.Float(), nullable=False),
            sa.Column("cfd_price", sa.Float(), nullable=False),
            sa.Column("offset", sa.Float(), nullable=False),
            sa.Column("ratio", sa.Float(), nullable=False),
            sa.Column("segment_start", _tz(), nullable=True),
            sa.PrimaryKeyConstraint("desk_symbol", "kind", "at"),
        ],
        "rolls": [
            sa.Column("cfd_symbol", sa.String(32), nullable=False),
            sa.Column("rolled_at", _tz(), nullable=False),
            sa.Column("confirmed_at", _tz(), nullable=False),
            sa.Column("step", sa.Float(), nullable=False),
            sa.Column("desk_symbol", sa.String(16), nullable=False),
            sa.Column("resolution", sa.String(4), nullable=False),
            sa.PrimaryKeyConstraint("cfd_symbol", "rolled_at"),
        ],
    }


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
    inspector = sa.inspect(bind)
    for name, items in _tables().items():
        if not inspector.has_table(name, schema=SCHEMA):
            op.create_table(name, *items, schema=SCHEMA)
    op.execute(sa.text(LEVELS_VIEW_SQL))

    # Same three statements as T76 (a3f1c7d92b64), for the schema it could not know about.
    # `quantdesk_ro` exists by now: T76 creates it.
    grantor = bind.scalar(sa.text("SELECT current_user"))
    role = _quote(RO_ROLE)
    op.execute(sa.text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}"))
    op.execute(sa.text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {role}"))
    op.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(grantor)} IN SCHEMA {SCHEMA} "
            f"GRANT SELECT ON TABLES TO {role}"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {SCHEMA}.bars")).scalar()
    if count:
        raise RuntimeError(
            f"downgrade: {SCHEMA}.bars holds {count} row(s) of broker history. This migration "
            "will not drop it. Export it first, or drop the schema by hand if that is the intent."
        )
    grantor = bind.scalar(sa.text("SELECT current_user"))
    role = _quote(RO_ROLE)
    op.execute(
        sa.text(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(grantor)} IN SCHEMA {SCHEMA} "
            f"REVOKE SELECT ON TABLES FROM {role}"
        )
    )
    op.execute(sa.text(f"DROP SCHEMA {SCHEMA} CASCADE"))
