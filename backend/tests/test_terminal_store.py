"""T79: the DuckDB -> Postgres facade, and the point-in-time rule it has to preserve.

Two halves, deliberately split by what they need:

`translate_sql` is **new code written for this port** and is tested offline, thoroughly. It is
the single point through which every SQL statement in a 6,600-line module now passes, so a bug
in it is a bug in all of them — and its failure mode is not an exception but a query that runs
and means something slightly different.

The point-in-time tests need a real Postgres, because that is the whole thing under test: DuckDB
and Postgres differ exactly where this module's correctness lives (timestamp handling, NULL
parameter typing, conflict clauses). Running them against SQLite would test nothing worth
knowing. They are skipped when no database is reachable rather than deleted, so they run on the
dev host and in any environment that provides one.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from app.modules.terminal.store.db import translate_sql

# --- translate_sql: offline, and the riskiest new code in the port --------------------------


def test_placeholders_become_psycopg_style():
    assert translate_sql("SELECT * FROM t WHERE a = ?", True) == "SELECT * FROM t WHERE a = %s"


def test_a_parameterless_query_is_untouched():
    """psycopg does no `%` processing without parameters, so escaping would corrupt a LIKE."""
    sql = "SELECT * FROM t WHERE id LIKE 'policy.ff.meeting_%'"
    assert translate_sql(sql, False) == sql


def test_a_literal_percent_is_escaped_when_parameters_are_passed():
    """The real query from `brief.py`, which is why this matters.

    `WHERE series_id LIKE 'policy.ff.meeting_%'` alongside a bound parameter: psycopg scans the
    whole string for `%`, so an unescaped one is either an error or a silently wrong pattern.
    """
    out = translate_sql("SELECT 1 WHERE id LIKE 'policy.ff.meeting_%' AND x = ?", True)
    assert "'policy.ff.meeting_%%'" in out
    assert out.endswith("x = %s")


def test_a_question_mark_inside_a_string_literal_is_data():
    """A `?` in a literal is not a placeholder; translating it would corrupt the value."""
    out = translate_sql("SELECT * FROM t WHERE note = 'why?' AND a = ?", True)
    assert "'why?'" in out
    assert out.count("%s") == 1


def test_an_apostrophe_in_a_line_comment_does_not_break_translation():
    """The bug this port actually hit, in `query.py`'s point-in-time SELECT.

    A `--` comment containing an ordinary English possessive ("the parameter's type") was read
    as opening a string literal, so every placeholder after it was left untranslated and
    psycopg reported "the query has 3 placeholders but 5 parameters were passed". The comment
    is the kind of thing anyone would write, which is what makes this worth pinning.
    """
    sql = """
    SELECT * FROM t
      -- the parameter's type cannot be inferred here
      WHERE a = ? AND b = ?
    """
    assert translate_sql(sql, True).count("%s") == 2


def test_an_apostrophe_in_a_block_comment_does_not_break_translation():
    sql = "SELECT * FROM t /* it's fine */ WHERE a = ?"
    assert translate_sql(sql, True).count("%s") == 1


def test_a_percent_inside_a_comment_is_still_escaped():
    """psycopg scans the whole string; a comment is not a hiding place."""
    out = translate_sql("SELECT 1 -- 50% of rows\nWHERE a = ?", True)
    assert "50%%" in out


def test_the_real_point_in_time_query_translates_to_the_right_arity():
    """Five parameters in, five placeholders out — the shape `query.get` actually passes."""
    from app.modules.terminal.store.query import _GET_SQL

    assert translate_sql(_GET_SQL, True).count("%s") == 5


# --- point-in-time: needs Postgres ----------------------------------------------------------

_DSN = os.environ.get("DATABASE_URL", "")
_needs_pg = pytest.mark.skipif(
    not _DSN.startswith("postgresql"),
    reason="point-in-time semantics are what differ between engines; testing them off Postgres "
    "would prove nothing. Set DATABASE_URL to run these.",
)


@pytest.fixture
def conn():
    from app.modules.terminal.store.db import connect

    c = connect()
    yield c
    c.close()


@_needs_pg
def test_a_revision_adds_a_vintage_and_destroys_none(conn):
    """The module's invariant, against the real migrated data.

    January 2024 nonfarm payrolls first printed 157700 and is now 157032. Both numbers — and
    the three between them — must still be there. If a migration or an upsert ever collapsed
    these to one row, every historical board would silently start showing today's revised
    world, and nothing would report an error.
    """
    from app.modules.terminal.store import query

    vintages = query.vintages(conn, "macro.payrolls", dt.date(2024, 1, 1))
    values = list(vintages["value"])
    assert len(values) >= 5
    assert values[0] == 157700.0
    assert values[-1] == 157032.0


@_needs_pg
def test_as_of_returns_the_world_as_it_looked_then(conn):
    from app.modules.terminal.store import query

    d = dt.date(2024, 1, 1)
    latest = query.get(conn, "macro.payrolls", d, d)
    early = query.get(
        conn, "macro.payrolls", d, d, as_of=dt.datetime(2024, 2, 15, tzinfo=dt.UTC)
    )

    assert latest["value"].iloc[0] == 157032.0
    assert early["value"].iloc[0] == 157700.0


@_needs_pg
def test_a_value_not_yet_published_is_absent_not_approximated(conn):
    """"Rows the world had not yet produced are absent, not approximated" — query.py's own words."""
    from app.modules.terminal.store import query

    d = dt.date(2024, 1, 1)
    before_first_print = query.get(
        conn,
        "macro.payrolls",
        d,
        d,
        as_of=dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
        warn_empty=False,
    )
    assert before_first_print.empty


@_needs_pg
def test_a_naive_as_of_is_refused(conn):
    """A naive timestamp cannot be compared against vintages recorded in other zones."""
    from app.modules.terminal.errors import DataIntegrityError
    from app.modules.terminal.store import query

    with pytest.raises(DataIntegrityError):
        query.get(
            conn, "macro.payrolls", dt.date(2024, 1, 1), dt.date(2024, 1, 1),
            as_of=dt.datetime(2024, 2, 15),  # noqa: DTZ001 - the point of the test
        )


@_needs_pg
def test_lookahead_is_caught_on_an_assembled_frame(conn):
    """The independent guard: `get` enforces the cutoff at read time, this catches a frame
    assembled some other way."""
    from app.modules.terminal.errors import LookaheadError
    from app.modules.terminal.store import query

    d = dt.date(2024, 1, 1)
    frame = query.get(conn, "macro.payrolls", d, d)
    query.assert_no_lookahead(frame, dt.datetime(2026, 9, 20, tzinfo=dt.UTC))

    with pytest.raises(LookaheadError):
        query.assert_no_lookahead(frame, dt.datetime(2024, 1, 1, tzinfo=dt.UTC))


@_needs_pg
def test_the_unqualified_sql_resolves_through_the_search_path(conn):
    """Every query in the module names `observations` bare, as it did against a schemaless file.

    `connect()` sets `search_path` to the terminal schema per connection, which is what let the
    port leave 25 SQL strings untouched. T76 pinned the *application* engine's path to `public`,
    so the two do not collide.
    """
    row = conn.execute("SELECT count(*) FROM observations").fetchone()
    assert row[0] > 0
