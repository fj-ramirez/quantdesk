"""Connection handling. **The only module allowed to know the database engine** (T79).

The original said exactly that, and promised that "swapping in Postgres later means adding a
sibling implementation here, not touching the loader, the query layer or the adapters". This
file is that promise being collected: the engine is Postgres now, and `loader.py`, `query.py`,
`board.py`, `brief.py`, `graph.py` and the rest kept their SQL character for character.

What makes that possible is `_Connection` below -- a small DuckDB-shaped facade over a psycopg
connection. Three differences had to be absorbed, and absorbing them once here is the whole
reason 25 call sites did not need editing:

**Placeholders.** DuckDB takes `?`, psycopg takes `%s`. Translating in one place means the SQL
stays diffable against the standalone repo, which is how "did the point-in-time query change?"
stays answerable by `diff` rather than by reading.

**Literal `%`.** psycopg treats `%` as its own escape *when parameters are passed*, so
`WHERE series_id LIKE 'policy.ff.meeting_%'` (brief.py, real and load-bearing) would raise or
silently mangle. `_translate` escapes it -- and only when there are parameters, because psycopg
does no interpolation at all without them.

**`fetch_df()`.** DuckDB hands back a DataFrame directly; psycopg does not. `_Cursor.fetch_df`
builds one from `cursor.description`, so the analytics keep receiving frames with the column
names they index by.

The trade this makes is deliberate: a facade is a layer to understand, but the alternative was
editing every SQL string in the module, which would have made the port unreviewable and put the
point-in-time semantics -- this module's entire correctness -- at risk in the same commit.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

import pandas as pd
import psycopg

from app.core.config import settings
from app.core.schemas import SCHEMA_TERMINAL

from ..logging import get_logger

log = get_logger("store.db")

__all__ = ["Connection", "Store", "connect", "translate_sql"]


def translate_sql(sql: str, has_params: bool) -> str:
    """DuckDB's `?` paramstyle -> psycopg's `%s`, escaping literal `%` on the way.

    Quote-aware *and* comment-aware, both learned the hard way.

    A `?` inside a string literal is data, not a placeholder. And an apostrophe inside a `--`
    comment -- "the parameter's type", which is exactly the kind of thing a comment says -- is
    not a string delimiter. Without the comment rule that apostrophe flipped the quote state
    for the remainder of the statement, so the real placeholders after it were never
    translated and psycopg raised "the query has 3 placeholders but 5 parameters were passed".
    Observed while porting `query.py`'s point-in-time SELECT, not theorised.

    `%` is escaped only when parameters are being passed. psycopg performs no `%` processing
    on a parameterless query, and escaping anyway would turn a literal `%` into `%%` in, for
    instance, a `LIKE` pattern.
    """
    if not has_params:
        return sql

    out: list[str] = []
    in_string = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""

        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            out.append("%%" if ch == "%" else ch)
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                out.append("*/")
                i += 2
                continue
            out.append("%%" if ch == "%" else ch)
            i += 1
            continue

        if not in_string and ch == "-" and nxt == "-":
            in_line_comment = True
            out.append("--")
            i += 2
            continue
        if not in_string and ch == "/" and nxt == "*":
            in_block_comment = True
            out.append("/*")
            i += 2
            continue

        if ch == "'":
            in_string = not in_string
            out.append(ch)
        elif ch == "%":
            # Escaped everywhere: psycopg scans the whole string, comments included.
            out.append("%%")
        elif ch == "?" and not in_string:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class _Cursor:
    """What `conn.execute(...)` returns, shaped like DuckDB's result object."""

    def __init__(self, cursor: psycopg.Cursor) -> None:
        self._cursor = cursor

    def fetchone(self) -> tuple | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[tuple]:
        return self._cursor.fetchall()

    def fetch_df(self) -> pd.DataFrame:
        """A DataFrame with the query's column names.

        `description` is None for a statement that returns no rows (INSERT/UPDATE); an empty
        frame is the right answer there rather than an exception, matching what DuckDB does.
        """
        if self._cursor.description is None:
            return pd.DataFrame()
        columns = [d.name for d in self._cursor.description]
        return pd.DataFrame(self._cursor.fetchall(), columns=columns)

    # DuckDB's object is also iterable.
    def __iter__(self):
        return iter(self._cursor)


class _Connection:
    """A psycopg connection wearing DuckDB's `execute` signature."""

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def execute(self, sql: str, params: list | tuple | None = None) -> _Cursor:
        cursor = self._conn.cursor()
        cursor.execute(translate_sql(sql, params is not None), params)
        return _Cursor(cursor)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    @property
    def raw(self) -> psycopg.Connection:
        """The psycopg connection, for the one caller that genuinely needs it (the migration
        script's `COPY`). Nothing in the module's own code should reach for this."""
        return self._conn


def connect(*, read_only: bool = False) -> _Connection:
    """Open a connection with `search_path` set to the terminal schema.

    **The `search_path` is what keeps the SQL unqualified.** Every query in this module names
    `observations`, `series_metadata` and so on bare, exactly as it did against a DuckDB file
    with no schemas at all. Setting the path per connection means none of them had to learn
    that they now live in `terminal.` -- and T76's decision to pin the *application* engine's
    path to `public` is what makes this safe to do differently here without the two colliding.

    `read_only` no longer means what it did. DuckDB allowed one writer process, so an
    interactive session had to open the file read-only or ingestion could not run. Postgres has
    no such constraint; the flag is kept so the CLI's signatures are unchanged, and it now sets
    a genuinely read-only transaction, which is a stronger guarantee than the original had.
    """
    conn = psycopg.connect(_psycopg_dsn(), autocommit=True)
    conn.execute(f"SET search_path TO {SCHEMA_TERMINAL}")
    if read_only:
        conn.execute("SET default_transaction_read_only = on")
    return _Connection(conn)


def _psycopg_dsn() -> str:
    """`settings.DATABASE_URL` is a SQLAlchemy URL; psycopg wants a plain one.

    One `+psycopg` to strip. Done here rather than by adding a second setting, so the module
    cannot end up pointed at a different database from the rest of the app.
    """
    return settings.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


class Store:
    """Thin owner of a connection. Use as a context manager.

    The `db_path` argument is gone: there is no file any more, and leaving a path-shaped way in
    would invite exactly the second-store fork that `modules/research`'s registry refuses for
    the same reason.
    """

    def __init__(self, *, read_only: bool = False) -> None:
        self.read_only = read_only
        self.conn = connect(read_only=read_only)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()


#: The type every analytics/brief/graph signature annotates its connection with. It was
#: `duckdb.DuckDBPyConnection`; a module-level alias means the six files that mention it needed
#: one import line changed each rather than a per-signature edit, and the next engine swap is
#: one line here.
Connection = _Connection
