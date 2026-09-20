"""Guards on the raw-SQL escape hatch (T82).

**None of this is the safety boundary.** `quantdesk_ro` is. Read `app/mcp/db.py` first: a role
with no write grant cannot write however a query is phrased, and that is the whole control. The
checks here exist for two lesser reasons:

1. **A clearer error.** "only SELECT and WITH are accepted" is a better thing for a model to
   read than Postgres's permission error, and it steers the next attempt.
2. **Multi-statement input must be refused, not partially run.** `psycopg` will happily execute
   `SELECT 1; DELETE FROM x` as one call. The DELETE fails on the role — so nothing is
   destroyed — but the client would see the SELECT's result and no error, which is a confusing
   and misleading outcome. Refusing the whole input is the honest answer.

The prefix check is explicitly *not* trusted as security, and the docstring of `check_sql` says
so, because the obvious next step for someone hardening this would be to add more keywords to a
denylist — which is effort spent on the wrong layer. `WITH x AS (DELETE ... RETURNING *) SELECT
* FROM x` begins with `WITH` and passes every prefix check ever written; it fails here because
the role cannot delete.
"""

from __future__ import annotations

import re

__all__ = ["SqlRejected", "check_sql", "strip_sql_comments"]


class SqlRejected(ValueError):
    """The statement was refused before reaching the database."""


_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")

_ALLOWED_PREFIXES = ("select", "with", "table", "explain", "values")


def strip_sql_comments(sql: str) -> str:
    """Remove comments and string literals, so structural checks see structure only.

    String literals are blanked rather than removed so a `;` inside one cannot be mistaken for a
    statement separator — `SELECT ';'` is a single statement and must be accepted.
    """
    without_blocks = _BLOCK_COMMENT.sub(" ", sql)
    without_lines = _LINE_COMMENT.sub(" ", without_blocks)
    return _STRING_LITERAL.sub("''", without_lines)


def check_sql(sql: str) -> str:
    """Validate and return the statement, or raise `SqlRejected`.

    **This is not a security boundary** — see the module docstring. It produces good errors and
    refuses multi-statement input; the database refuses everything that matters.
    """
    if not sql or not sql.strip():
        raise SqlRejected("empty statement")

    skeleton = strip_sql_comments(sql).strip()
    if not skeleton:
        raise SqlRejected("the statement is only comments")

    # A single trailing semicolon is ordinary; anything after one is a second statement.
    body = skeleton.rstrip().rstrip(";").rstrip()
    if ";" in body:
        raise SqlRejected(
            "multiple statements are not accepted. Send one statement per call — the whole "
            "input is refused rather than running the first part of it."
        )

    first = body.split(None, 1)[0].lower() if body.split() else ""
    if first not in _ALLOWED_PREFIXES:
        raise SqlRejected(
            f"only read statements are accepted here ({', '.join(_ALLOWED_PREFIXES).upper()}); "
            f"got {first.upper() or 'nothing'}. Note that this check is a convenience: the "
            f"connector authenticates as a role that has no write permission at all, so a write "
            f"phrased to slip past it still fails at the database."
        )
    return sql.strip().rstrip(";")
