"""T76: three module schemas, the gex tables moved into theirs, and the read-only role.

One database, three namespaces -- `gex`, `research`, `terminal` -- plus `quantdesk_ro`, the
role the MCP connector (T82) will authenticate as. See `plans/quantdesk/01-postgres-schemas.md`
for why schemas rather than `module__table` prefixes or three databases.

**GEX moves too, and that is the point.** Leaving it in `public` while the newcomers got
schemas would be the cheaper migration and the worse design: the read-only role would need a
different grant shape for one module than for the other two, and every cross-module query
would carry exactly one unqualified name.

**`alembic_version` does not move.** It is Alembic's bookkeeping, not a module's table, and
relocating the very table this migration is mid-way through writing to is a trap with no safe
ordering -- see the long note in `alembic/env.py`. `public` therefore ends up holding exactly
one table, owned by the migration tool rather than by any module.

Postgres-only, by an explicit dialect guard. `ALTER TABLE ... SET SCHEMA`, `CREATE ROLE` and
`ALTER DEFAULT PRIVILEGES` have no SQLite equivalent, and `tests/test_alembic_upgrade.py`
walks this chain on SQLite (it stops earlier today, but a guard here is cheaper than the
puzzle it would otherwise set for whoever extends that test).

Revision ID: a3f1c7d92b64
Revises: 179bee3e9455
Create Date: 2026-09-19

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core.schemas import SCHEMA_GEX, SCHEMAS

revision: str = "a3f1c7d92b64"
down_revision: str | Sequence[str] | None = "179bee3e9455"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The gex tables as they stand at this revision, in dependency order. Spelled out rather than
#: read from `Base.metadata` on purpose: a migration records what was true when it ran, and a
#: model list that grows in T90 must not retroactively change what this revision did. It is
#: also the reason there is no bulk "move everything in public" loop -- that would sweep up
#: `alembic_version` and break the migration performing it.
GEX_TABLES: tuple[str, ...] = (
    "snapshots",
    "gex_levels",
    "gex_by_strike",
    "daily_bars",
    "intraday_bars",
    "etf_shares_outstanding",
    "decisions",
)

#: NOLOGIN here. This migration grants *privileges*, which are schema state and belong in the
#: chain; it does not set a password, which is a secret and does not. `app.core.ro_role` turns
#: the login on from `QUANTDESK_RO_PASSWORD` at container start, so rotating the password is
#: an env change and a restart rather than a new migration.
RO_ROLE = "quantdesk_ro"


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def upgrade() -> None:
    if not _is_postgres():
        return

    bind = op.get_bind()

    for schema in SCHEMAS:
        op.execute(sa.text(f"CREATE SCHEMA IF NOT EXISTS {_quote(schema)}"))

    # `IF EXISTS`-style guard per table rather than one blanket move: on a database built from
    # scratch by this chain every table is in `public` and all seven move, but a database that
    # has already been reorganised by hand (or a future revision that adds a table directly
    # into `gex`) must not make this fail.
    for table in GEX_TABLES:
        present = bind.scalar(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        )
        if present:
            op.execute(
                sa.text(f"ALTER TABLE public.{_quote(table)} SET SCHEMA {_quote(SCHEMA_GEX)}")
            )

    # CREATE ROLE has no IF NOT EXISTS. The role is cluster-wide, not database-wide, so on a
    # host running both the lab and another quantdesk database it may already exist.
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RO_ROLE}') THEN
                    CREATE ROLE {_quote(RO_ROLE)} NOLOGIN;
                END IF;
            END
            $$
            """
        )
    )

    # `ALTER DEFAULT PRIVILEGES` applies per *granting* role: set for the wrong one it silently
    # governs nothing, and the failure surfaces months later when a module adds a table and the
    # connector cannot read it. The granting role is whoever runs migrations, which is this
    # connection's `current_user` -- read it rather than assuming `postgres` or `gex`.
    grantor = bind.scalar(sa.text("SELECT current_user"))

    for schema in SCHEMAS:
        q_schema = _quote(schema)
        op.execute(sa.text(f"GRANT USAGE ON SCHEMA {q_schema} TO {_quote(RO_ROLE)}"))
        op.execute(
            sa.text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {q_schema} TO {_quote(RO_ROLE)}")
        )
        # The half that makes this durable: every table created in these schemas *from now on*
        # is readable by the role with no new grant, which is what stops T77 and T79 from
        # having to remember.
        op.execute(
            sa.text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(grantor)} IN SCHEMA {q_schema} "
                f"GRANT SELECT ON TABLES TO {_quote(RO_ROLE)}"
            )
        )


def downgrade() -> None:
    if not _is_postgres():
        return

    bind = op.get_bind()
    grantor = bind.scalar(sa.text("SELECT current_user"))

    # Default privileges must be revoked with the same `FOR ROLE` they were granted under, and
    # before the role is dropped -- a lingering default-privilege entry makes DROP ROLE fail
    # with "cannot be dropped because some objects depend on it", naming nothing useful.
    for schema in SCHEMAS:
        q_schema = _quote(schema)
        op.execute(
            sa.text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {_quote(grantor)} IN SCHEMA {q_schema} "
                f"REVOKE SELECT ON TABLES FROM {_quote(RO_ROLE)}"
            )
        )
        op.execute(
            sa.text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {q_schema} FROM {_quote(RO_ROLE)}")
        )
        op.execute(sa.text(f"REVOKE ALL ON SCHEMA {q_schema} FROM {_quote(RO_ROLE)}"))

    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RO_ROLE}') THEN
                    DROP ROLE {_quote(RO_ROLE)};
                END IF;
            END
            $$
            """
        )
    )

    for table in GEX_TABLES:
        present = bind.scalar(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :s AND table_name = :t"
            ),
            {"s": SCHEMA_GEX, "t": table},
        )
        if present:
            op.execute(
                sa.text(f"ALTER TABLE {_quote(SCHEMA_GEX)}.{_quote(table)} SET SCHEMA public")
            )

    # Plain DROP, not CASCADE: after the moves above these are empty, and if they are not,
    # something this migration did not create is in them and silently destroying it would be
    # the wrong answer to a downgrade.
    for schema in SCHEMAS:
        op.execute(sa.text(f"DROP SCHEMA IF EXISTS {_quote(schema)} RESTRICT"))
