"""T82: the MCP connector.

Split by what each half is actually testing:

The **guards and the formatter** are pure logic and run offline, in full. They are where a model
is protected from misreading a result — truncation notices, null markers, the multi-statement
refusal — and none of them need a database.

The **read-only boundary** needs Postgres, because that is the entire claim: `quantdesk_ro`
cannot write, and no amount of SQLite proves it. These are skipped without a database rather
than deleted, and they include the case that matters most — a write phrased to slip past the
prefix check, refused by the role.
"""

from __future__ import annotations

import os

import pytest

from app.mcp.db import QueryResult
from app.mcp.format import NULL_MARKER, render_result, render_rows, render_value
from app.mcp.sqlguard import SqlRejected, check_sql, strip_sql_comments

# --- the SQL guard: offline ------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select * from gex.snapshots",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "EXPLAIN SELECT 1",
        "  SELECT 1  ",
        "SELECT 1;",
    ],
)
def test_read_statements_are_accepted(sql):
    assert check_sql(sql)


@pytest.mark.parametrize(
    "sql",
    ["INSERT INTO t VALUES (1)", "UPDATE t SET x = 1", "DELETE FROM t", "DROP TABLE t", "TRUNCATE t"],
)
def test_obvious_writes_are_refused_with_a_useful_message(sql):
    """Not the safety boundary -- a better error than Postgres's, and a steer for the retry."""
    with pytest.raises(SqlRejected, match="only read statements"):
        check_sql(sql)


def test_the_refusal_says_the_role_is_the_real_control():
    """So nobody hardens the wrong layer.

    The obvious next move for someone tightening this would be to add keywords to a denylist,
    which is effort spent where it cannot help. The message says where the boundary is.
    """
    with pytest.raises(SqlRejected, match="no write permission"):
        check_sql("DELETE FROM t")


def test_multiple_statements_are_refused_whole():
    """psycopg would run both. The DELETE fails on the role, so nothing is destroyed -- but the
    client would see the SELECT's result and no error, which is worse than a refusal."""
    with pytest.raises(SqlRejected, match="multiple statements"):
        check_sql("SELECT 1; DELETE FROM research.trials")


def test_a_semicolon_inside_a_string_is_not_a_statement_separator():
    """`SELECT ';'` is one statement and must be accepted."""
    assert check_sql("SELECT ';' AS x")


def test_a_semicolon_inside_a_comment_is_not_a_statement_separator():
    assert check_sql("SELECT 1 -- and then; something\n")


def test_comments_cannot_smuggle_a_leading_keyword():
    """`/* SELECT */ DELETE ...` must be judged on the DELETE."""
    with pytest.raises(SqlRejected, match="only read statements"):
        check_sql("/* SELECT */ DELETE FROM t")


def test_an_empty_or_comment_only_statement_is_refused():
    with pytest.raises(SqlRejected):
        check_sql("")
    with pytest.raises(SqlRejected, match="only comments"):
        check_sql("-- nothing here")


def test_strip_sql_comments_blanks_literals_without_removing_them():
    """Literals are blanked rather than deleted so structure (and arity) survives."""
    assert ";" not in strip_sql_comments("SELECT ';'")


def test_the_with_delete_form_passes_the_guard_on_purpose():
    """The documented hole, asserted so nobody "fixes" it in the wrong place.

    `WITH x AS (DELETE ... RETURNING *) SELECT * FROM x` begins with `WITH` and no prefix check
    will ever catch it. It is refused by the role at the database, and there is a Postgres-backed
    test below that proves it.
    """
    assert check_sql("WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x")


# --- the formatter: offline -------------------------------------------------------------------


def test_null_renders_as_a_marker_never_as_blank_or_zero():
    """Across this desk a null and a zero are different facts, and a blank cell in a markdown
    table is indistinguishable from an empty string."""
    assert render_value(None) == NULL_MARKER
    assert render_value(0) == "0"
    assert render_value(0.0) == "0"


def test_truncation_is_stated_above_the_table():
    """A model shown 100 of 134,377 rows with nothing saying so will summarise them as all."""
    result = QueryResult(["a"], [(1,)], truncated=True, limit=1)
    out = render_result(result)
    assert "Truncated to 1 rows" in out
    # Above the table, where something reading top-down cannot miss it.
    assert out.index("Truncated") < out.index("| a |")


def test_an_untruncated_result_reports_its_row_count():
    out = render_result(QueryResult(["a"], [(1,), (2,)], truncated=False, limit=100))
    assert "(2 rows)" in out
    assert "Truncated" not in out


def test_pipes_in_values_cannot_break_the_table():
    assert render_value("a|b") == "a\\|b"


def test_long_values_are_truncated_so_one_cell_cannot_eat_the_budget():
    rendered = render_value("x" * 500)
    assert len(rendered) < 200
    assert rendered.endswith("…")


def test_booleans_render_as_words():
    """`yes`/`no` rather than `True`/`False`: less likely to be read back as a Python repr."""
    assert render_value(True) == "yes"
    assert render_value(False) == "no"


def test_an_empty_result_says_so_rather_than_rendering_an_empty_table():
    assert render_rows(["a"], []) == "(no rows)"


# --- the module itself: no database required ---------------------------------------------------


def test_the_server_module_imports():
    """A smoke test, and it earns its place.

    Every other test that touches `app.mcp.server` imports it *inside* the test body and is
    marked `_needs_pg`, so on a host without `DATABASE_URL_RO` -- which is the normal one, and
    CI -- the module is never imported by the suite at all. A syntax error or a bad import in
    the connector therefore ships green: it was introduced, the full suite passed, and only
    `ruff` noticed. Importing costs nothing and needs no database, so nothing is bought by
    leaving that hole open.

    It also asserts the tools are actually registered, because a decorator that silently stops
    matching is the other way this module breaks without failing.
    """
    from app.mcp import server

    for tool in ("query_sql", "research_paper", "research_leaderboard", "gex_levels",
                 "terminal_board", "terminal_series"):
        assert callable(getattr(server, tool, None)), f"{tool} is missing from the connector"


# --- the read-only boundary: needs Postgres ----------------------------------------------------

_needs_pg = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_RO", "").startswith("postgresql"),
    reason="the read-only boundary is a Postgres role; nothing else can prove it. "
    "Set DATABASE_URL_RO to run these.",
)


@_needs_pg
def test_the_connector_role_cannot_write_anything():
    """The startup assertion, which is the connector's whole safety model.

    Asks Postgres's own privilege functions rather than attempting a write -- the first version
    of this probed with `CREATE TEMPORARY TABLE` and refused to start, because Postgres grants
    TEMP to PUBLIC by default and `quantdesk_ro` creates temp tables happily. A temp table
    cannot touch this data; it proved nothing.
    """
    from app.mcp.db import assert_read_only

    assert assert_read_only() == "quantdesk_ro"


@_needs_pg
def test_a_write_that_slips_past_the_guard_is_refused_by_the_database():
    """**The test that matters.**

    This statement passes `check_sql` by design. If the role were ever mis-granted, this is the
    query that would destroy the research registry -- so the refusal has to come from Postgres,
    and this asserts that it does.
    """
    from app.mcp.server import query_sql

    out = query_sql("WITH x AS (DELETE FROM research.trials RETURNING *) SELECT * FROM x")
    assert "Refused by the database" in out
    assert "read-only" in out


@_needs_pg
def test_the_row_cap_holds_and_says_it_truncated():
    """`research.trials` has >134k rows; an uncapped SELECT * would blow the context window."""
    from app.mcp.server import query_sql

    out = query_sql("SELECT * FROM research.trials", limit=5)
    assert "Truncated to 5 rows" in out


@_needs_pg
def test_the_statement_timeout_kills_a_runaway_query():
    from app.mcp.server import query_sql

    out = query_sql("SELECT pg_sleep(30)")
    assert "timeout" in out.lower() or "cancel" in out.lower()


@_needs_pg
def test_the_leaderboard_always_carries_the_noise_ceiling():
    """The acceptance criterion, and the reason a model can be pointed at this safely."""
    from app.mcp.server import research_leaderboard

    out = research_leaderboard(top_n=3)
    assert "Noise ceiling" in out
    assert "trials have been searched" in out
    assert "indistinguishable from luck" in out
    assert "above_ceiling" in out


@_needs_pg
def test_terminal_tools_say_which_as_of_they_used():
    """A model asking a historical question must not silently get a look-ahead answer."""
    from app.mcp.server import terminal_series

    explicit = terminal_series("macro.payrolls", as_of="2024-02-15T00:00:00Z", limit=3)
    assert "as of 2024-02-15T00:00:00+00:00" in explicit

    implicit = terminal_series("macro.payrolls", limit=3)
    assert "latest-known" in implicit


@_needs_pg
def test_an_early_as_of_returns_the_original_print_not_the_revision():
    """The point-in-time guarantee, end to end through the connector.

    January 2024 payrolls first printed 157700 and is now 157032. Asking as of February 2024
    must give the number that was knowable then.
    """
    from app.mcp.server import terminal_series

    out = terminal_series("macro.payrolls", as_of="2024-02-15T00:00:00Z", limit=1)
    assert "1.577e+05" in out or "157700" in out


@_needs_pg
def test_the_schema_resource_names_the_point_in_time_trap():
    """The resource exists so `query_sql` is aimed rather than guessed, and the trap it most
    needs to warn about is a terminal query with no `as_of` filter."""
    from app.mcp.server import schema_resource

    out = schema_resource()
    assert "terminal" in out
    assert "as_of" in out
    assert "look-ahead" in out
