"""The connector's database access, and the one place read-only is enforced (T82).

**Postgres enforces read-only, not this module.** The connector authenticates as
`quantdesk_ro` (T76), a role with `SELECT` and nothing else. That is the safety boundary, and
it is the only one worth having: inspecting SQL strings for `DROP` is security theatre, because
`WITH x AS (DELETE ... RETURNING *) SELECT * FROM x` starts with `WITH` and a parser that
reasons about prefixes will pass it. A role with no write grant cannot write however the query
is phrased.

Everything else here — the read-only transaction, the statement timeout, the row cap, the
single-statement rule — exists to stop a *runaway* query, not a malicious one. They are
ergonomics and cost control. The role is the security.

**Fail closed.** There is deliberately no fallback to `DATABASE_URL`. If `DATABASE_URL_RO` is
unset the server refuses to start, because the failure mode of a fallback is the worst kind:
everything works, every tool answers, and nothing is read-only. `assert_read_only` goes further
and verifies at startup that the role holds no write privilege on any quantdesk schema — a
configuration that points `DATABASE_URL_RO` at the application user is a plausible mistake and
would otherwise be undetectable from inside.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

import psycopg

from app.core.config import settings
from app.core.schemas import SCHEMAS

log = logging.getLogger("app.mcp.db")

__all__ = [
    "DEFAULT_ROW_LIMIT",
    "MAX_ROW_LIMIT",
    "STATEMENT_TIMEOUT_MS",
    "QueryResult",
    "ReadOnlyViolation",
    "assert_read_only",
    "run_query",
]

#: Sized for a context window, not for a database. `research.trials` alone holds 134,377 rows,
#: and an uncapped `SELECT *` would blow the window and teach the model nothing.
DEFAULT_ROW_LIMIT = 100
MAX_ROW_LIMIT = 1000

#: Kills a runaway query rather than hanging the client's session. Ten seconds is far more than
#: any indexed read here needs and far less than a user will wait before assuming a crash.
STATEMENT_TIMEOUT_MS = 10_000


class ReadOnlyViolation(RuntimeError):
    """Raised at startup when the configured role can write. Never caught."""


class QueryResult:
    """Rows plus the facts a model needs to read them honestly."""

    def __init__(self, columns: list[str], rows: list[tuple], *, truncated: bool, limit: int):
        self.columns = columns
        self.rows = rows
        #: Whether rows were cut off. Reported in the rendered output, because a model shown
        #: 100 of 134,377 rows without being told will summarise them as if they were all.
        self.truncated = truncated
        self.limit = limit


def _dsn() -> str:
    """The read-only DSN. Absent is a hard failure, never a fallback.

    A fallback to `DATABASE_URL` would mean a missing setting produced a fully working connector
    with no read-only guarantee at all — the exact class of bug that is invisible until it
    matters.
    """
    url = settings.DATABASE_URL_RO
    if not url:
        raise ReadOnlyViolation(
            "DATABASE_URL_RO is not set. The MCP connector must authenticate as `quantdesk_ro` "
            "and deliberately has no fallback to DATABASE_URL: falling back would give a "
            "connector that works perfectly and is not read-only. Set it to "
            "postgresql://quantdesk_ro:<QUANTDESK_RO_PASSWORD>@<host>:5432/<db>."
        )
    return url.replace("postgresql+psycopg://", "postgresql://")


@contextmanager
def _connection():
    conn = psycopg.connect(_dsn(), autocommit=False)
    try:
        with conn.cursor() as cur:
            # `SET` takes no bind parameters in Postgres ("syntax error at or near $1"), so
            # the value is interpolated. Safe because it is this module's own integer constant
            # and is never caller-supplied -- and it is coerced to `int` anyway so the intent
            # survives someone later making the constant configurable.
            cur.execute(f"SET statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
            # Belt to the role's braces: even a correctly-granted role should not be able to
            # start a write transaction by accident inside this process.
            cur.execute("SET TRANSACTION READ ONLY")
        yield conn
    finally:
        conn.rollback()
        conn.close()


def assert_read_only() -> str:
    """Prove at startup that the role we connected as cannot write. Returns the role name.

    The mistake this catches is pointing `DATABASE_URL_RO` at the application user, which no
    amount of reading the setting can detect and which silently removes the connector's entire
    safety model.

    **This asks Postgres's own privilege functions rather than attempting a write, and the first
    version of this function got that wrong.** It tried `CREATE TEMPORARY TABLE` and treated
    success as proof of write access — but Postgres grants `TEMP` on a database to `PUBLIC` by
    default, so `quantdesk_ro` creates temporary tables happily and the check refused to start a
    perfectly correct connector. A temp table cannot touch any of this data; it is not evidence
    of anything. `has_schema_privilege` and `has_table_privilege` answer the question that
    actually matters -- can this role write **our** tables -- and answer it authoritatively,
    without writing anything.
    """
    dsn = _dsn()
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            role = cur.fetchone()[0]

            cur.execute(
                """
                SELECT
                    coalesce(bool_or(has_schema_privilege(current_user, n.nspname, 'CREATE')),
                             false) AS can_create,
                    coalesce(bool_or(
                        has_table_privilege(current_user, c.oid, 'INSERT')
                        OR has_table_privilege(current_user, c.oid, 'UPDATE')
                        OR has_table_privilege(current_user, c.oid, 'DELETE')
                        OR has_table_privilege(current_user, c.oid, 'TRUNCATE')
                    ), false) AS can_write
                FROM pg_namespace n
                LEFT JOIN pg_class c
                       ON c.relnamespace = n.oid AND c.relkind IN ('r', 'p')
                WHERE n.nspname = ANY(%s)
                """,
                (list(SCHEMAS),),
            )
            can_create, can_write = cur.fetchone()

            if can_create or can_write:
                raise ReadOnlyViolation(
                    f"the MCP connector connected as {role!r}, which can "
                    f"{'create objects in' if can_create else 'write to'} the quantdesk schemas. "
                    "DATABASE_URL_RO must point at `quantdesk_ro` (or another role with SELECT "
                    "and nothing else). Refusing to start: a connector that can write is not the "
                    "thing this server is."
                )

            log.info("connected as %s; no write privilege on any quantdesk schema", role)
            return role
    finally:
        conn.close()


def run_query(sql: str, params: Any = None, *, limit: int = DEFAULT_ROW_LIMIT) -> QueryResult:
    """Execute one read-only statement and return at most `limit` rows.

    `limit + 1` rows are fetched so truncation can be reported honestly rather than guessed at
    from a full page.
    """
    limit = max(1, min(limit, MAX_ROW_LIMIT))
    with _connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return QueryResult([], [], truncated=False, limit=limit)
        columns = [d.name for d in cur.description]
        rows = cur.fetchmany(limit + 1)
        truncated = len(rows) > limit
        return QueryResult(columns, list(rows[:limit]), truncated=truncated, limit=limit)
