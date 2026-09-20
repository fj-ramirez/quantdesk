"""The quantdesk MCP server: the whole desk, read-only, over stdio (T82).

Three stores became one database in T75–T80, and one database is something a connector can be
pointed at. This is that connector.

**Domain tools first, `query_sql` as the escape hatch.** A server exposing only raw SQL makes
the model guess at a schema it has never seen, and the failure mode is a confidently wrong
query rather than an error. The eight tools below encode the questions actually worth asking;
`query_sql` covers everything else and is deliberately the least convenient option.

**Every result carries its caveats, structurally.** `research_leaderboard` returns the noise
ceiling in the same payload as the rows, the terminal tools state the `as_of` they actually used
and every row's `as_of_basis`, and GEX results carry `captured_at`. This is not belt-and-braces:
a model summarising a leaderboard without the noise ceiling *will* report luck as an edge, and
making that impossible is cheaper and more reliable than prompting against it. It matters more,
not less, with a smaller local model on the other end.

**Stdout is the transport.** Nothing in this process may print. `app/mcp/__main__.py` routes
logging to stderr before importing anything; a stray `print` corrupts the protocol and the
failure looks like the client's fault.
"""

from __future__ import annotations

import datetime as dt

import psycopg
from mcp.server.mcpserver import MCPServer

from app.core.schemas import SCHEMA_GEX, SCHEMA_RESEARCH, SCHEMA_TERMINAL
from app.mcp.db import (
    DEFAULT_ROW_LIMIT,
    MAX_ROW_LIMIT,
    STATEMENT_TIMEOUT_MS,
    run_query,
)
from app.mcp.format import render_result
from app.mcp.sqlguard import SqlRejected, check_sql

__all__ = ["build_server", "server"]

server = MCPServer(
    name="quantdesk",
    # Reported in the initialize handshake. Clients show it when listing servers, and an
    # empty string there looks like a broken build.
    version="1.0.0",
    instructions=(
        "Read-only access to the quantdesk database: GEX option positioning, EdgeLab research "
        "trials, and the xactx cross-asset terminal.\n\n"
        "Three things to know before summarising anything from here:\n"
        "1. A research leaderboard row means nothing without the noise ceiling returned "
        "beside it. Rows below their ceiling are indistinguishable from luck.\n"
        "2. Terminal data is point-in-time. Every terminal tool reports the `as_of` it used; "
        "if a question is about a past moment, pass that moment rather than reading the "
        "latest-known answer.\n"
        "3. A null is not a zero anywhere in this database. Missing values render as `·`."
    ),
)


def _parse_date(value: str | None, *, field: str) -> dt.date | None:
    if value is None or value == "":
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def _parse_as_of(value: str | None) -> tuple[dt.datetime, bool]:
    """Parse an `as_of`, defaulting to now. Returns (instant, was_explicit).

    **Naive input is given UTC explicitly rather than left to the server's zone.** Invariant 4
    applies to the connector too, and a bare date reaching a `TIMESTAMPTZ` comparison would
    otherwise resolve against wherever this process happens to run.
    """
    if not value:
        return dt.datetime.now(dt.UTC), False
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        parsed_date = _parse_date(value, field="as_of")
        if parsed_date is None:
            return dt.datetime.now(dt.UTC), False
        parsed = dt.datetime.combine(parsed_date, dt.time.max)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed, True


def _as_of_note(as_of: dt.datetime, explicit: bool) -> str:
    if explicit:
        return (
            f"Point-in-time read **as of {as_of.isoformat(timespec='seconds')}**. Revisions "
            f"published after this instant are excluded."
        )
    return (
        f"No `as_of` was given, so this is **latest-known** (resolved to "
        f"{as_of.isoformat(timespec='seconds')}). For a question about a past moment, pass "
        f"`as_of` — otherwise the answer reflects later revisions that were not knowable then."
    )


# --- GEX -------------------------------------------------------------------------------------


@server.tool(
    description=(
        "GEX levels for one underlying: flip point, call/put walls and net gamma, from the "
        "stored snapshot index. Returns the most recent snapshot on or before `date`, or the "
        "latest overall when `date` is omitted."
    )
)
def gex_levels(symbol: str, date: str | None = None, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Flip point, walls and regime for a symbol."""
    on = _parse_date(date, field="date")
    sql = f"""
        SELECT s.underlying, s.captured_at, s.spot, s.is_eod, l.filter,
               l.net_gex, l.flip_point, l.call_wall, l.call_wall_gex,
               l.put_wall, l.put_wall_gex, l.max_abs_strike
        FROM {SCHEMA_GEX}.gex_levels l
        JOIN {SCHEMA_GEX}.snapshots s ON s.id = l.snapshot_id
        WHERE s.underlying = %(symbol)s
          {"AND s.captured_at::date <= %(on)s" if on else ""}
        ORDER BY s.captured_at DESC, l.filter
    """
    params: dict[str, object] = {"symbol": symbol.upper()}
    if on:
        params["on"] = on
    result = run_query(sql, params, limit=limit)
    note = (
        "`captured_at` is when the chain was captured, in UTC. A null level is a real answer — "
        "it means that filter admitted no contracts, or the profile never changed sign — and is "
        "never a zero."
    )
    return render_result(result, note=note)


@server.tool(
    description=(
        "The decision log: opportunities the engine emitted, with the levels as suggested and "
        "the outcome columns filled in from later bars. Filter by symbol and/or a start date."
    )
)
def gex_decisions(
    symbol: str | None = None, since: str | None = None, limit: int = DEFAULT_ROW_LIMIT
) -> str:
    """Emitted opportunities and how they resolved."""
    start = _parse_date(since, field="since")
    clauses = []
    params: dict[str, object] = {}
    if symbol:
        clauses.append("underlying = %(symbol)s")
        params["symbol"] = symbol.upper()
    if start:
        clauses.append("decided_on >= %(start)s")
        params["start"] = start
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT underlying, decided_on, filter, key, outcome, fill,
               result_r, mfe_r, mae_r, mark_r
        FROM {SCHEMA_GEX}.decisions
        {where}
        ORDER BY decided_on DESC, underlying
    """
    note = (
        "A recorded level is a commitment the track record scores: rows are inserted when first "
        "seen and never rewritten. `outcome` null means still pending, which is different from a "
        "loss."
    )
    return render_result(run_query(sql, params, limit=limit), note=note)


# --- research --------------------------------------------------------------------------------


@server.tool(
    description=(
        "EdgeLab's leaderboard: backtested strategy/parameter combinations ranked by "
        "out-of-sample Sharpe. **Always returns the noise ceiling alongside the rows** — a row "
        "below its ceiling is indistinguishable from luck and must not be reported as an edge."
    )
)
def research_leaderboard(
    market: str | None = None, top_n: int = 20, min_trades_oos: int = 20
) -> str:
    """Survivors, with the noise ceiling that gives them meaning."""
    top_n = max(1, min(top_n, MAX_ROW_LIMIT))
    total = run_query(f"SELECT count(*) FROM {SCHEMA_RESEARCH}.trials", limit=1)
    total_trials = total.rows[0][0] if total.rows else 0

    params: dict[str, object] = {"min_fills": min_trades_oos}
    market_clause = ""
    if market:
        market_clause = "AND market = %(market)s"
        params["market"] = market

    sql = f"""
        SELECT hash, market, strategy, symbol, timeframe, params,
               is_sharpe, oos_sharpe, oos_cagr, oos_max_dd, oos_fills,
               oos_exposure, oos_years, run_date,
               sqrt(2 * ln(greatest({max(total_trials, 2)}, 2))) / sqrt(nullif(oos_years, 0))
                   AS row_noise_ceiling,
               oos_sharpe > sqrt(2 * ln(greatest({max(total_trials, 2)}, 2)))
                   / sqrt(nullif(oos_years, 0)) AS above_ceiling
        FROM {SCHEMA_RESEARCH}.trials
        WHERE oos_fills >= %(min_fills)s AND oos_exposure >= 0.02
          AND oos_sharpe > 0 AND is_sharpe > 0
          {market_clause}
        ORDER BY oos_sharpe DESC
    """
    result = run_query(sql, params, limit=top_n)
    above = sum(1 for row in result.rows if row[-1])
    note = (
        f"**Noise ceiling.** {total_trials:,} trials have been searched. Across that many "
        f"attempts, pure luck alone is expected to produce a best out-of-sample Sharpe of about "
        f"sqrt(2·ln N)/sqrt(years) — `row_noise_ceiling` is that figure for each row's own OOS "
        f"span, and `above_ceiling` says whether the row clears it. "
        f"**{above} of the {len(result.rows)} rows below clear their own ceiling.**\n\n"
        f"Do not describe a row with `above_ceiling = no` as an edge; on this evidence it is "
        f"indistinguishable from luck.\n\n"
        f"Also note the out-of-sample split has been reused across thousands of search cycles, "
        f"so it is no longer truly unseen. The paper watchlist (`research_paper`) is the only "
        f"genuinely out-of-sample evidence here."
    )
    return render_result(result, note=note)


@server.tool(description="One research trial in full: its parameters and both sides of the split.")
def research_trial(hash: str) -> str:
    """A single trial by hash."""
    sql = f"""
        SELECT hash, market, strategy, symbol, timeframe, params, run_date,
               is_sharpe, is_cagr, is_max_dd, is_fills,
               oos_sharpe, oos_cagr, oos_max_dd, oos_fills, oos_exposure, oos_bars, oos_years
        FROM {SCHEMA_RESEARCH}.trials WHERE hash = %(hash)s
    """
    result = run_query(sql, {"hash": hash}, limit=1)
    if not result.rows:
        return f"No trial with hash {hash!r}."
    note = (
        "The in-sample figures are what the search fitted. A large gap between `is_sharpe` and "
        "`oos_sharpe` is the signature of a parameter set tuned to its own history."
    )
    return render_result(result, note=note)


@server.tool(
    description=(
        "The paper watchlist: trials promoted to forward tracking. Performance after "
        "`promoted_at` is the only evidence in EdgeLab that was never fitted."
    )
)
def research_paper(limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Promoted candidates, oldest first."""
    sql = f"""
        SELECT hash, promoted_at, market, strategy, symbol, timeframe, params,
               promoted_oos_sharpe, sharpe_2x, neighbor_med, wf_pos, wf_active, wf_med, corr_max
        FROM {SCHEMA_RESEARCH}.paper_candidates
        ORDER BY promoted_at
    """
    note = (
        "Ordered by promotion date, deliberately not by performance. Each row cleared a share of "
        "its noise ceiling, stayed profitable at **doubled** costs (`sharpe_2x`), survived a "
        "parameter-neighbourhood check (`neighbor_med`), passed a walk-forward gate "
        "(`wf_pos`/`wf_active`) and was not too correlated with the existing list (`corr_max`)."
    )
    return render_result(run_query(sql, limit=limit), note=note)


# --- terminal --------------------------------------------------------------------------------


@server.tool(
    description=(
        "The cross-asset change board at a point in time: every series' latest observation and "
        "how many standard deviations its move is against its own trailing history. Pass `as_of` "
        "(ISO 8601) to see the world as it was known at that instant."
    )
)
def terminal_board(as_of: str | None = None, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """The board, point-in-time."""
    instant, explicit = _parse_as_of(as_of)
    sql = f"""
        SELECT DISTINCT ON (o.series_id)
               o.series_id, m.display_name, m.asset_class, o.value_date, o.value,
               o.as_of, o.as_of_basis, m.unit, m.frequency
        FROM {SCHEMA_TERMINAL}.observations o
        JOIN {SCHEMA_TERMINAL}.series_metadata m ON m.series_id = o.series_id
        WHERE o.as_of <= %(as_of)s
        ORDER BY o.series_id, o.value_date DESC, o.as_of DESC
    """
    note = (
        _as_of_note(instant, explicit)
        + "\n\n`as_of_basis` says how each row's vintage was established: `source_vintage` is "
        "the publisher's own date, `derived_lag` is the value date plus a publication "
        "convention, `archive_floor` means the value was known *by* then but the true "
        "publication date is unrecoverable. A series absent from this table had published "
        "nothing by that instant — that is a fact, not a gap to fill."
    )
    return render_result(run_query(sql, {"as_of": instant}, limit=limit), note=note)


@server.tool(
    description=(
        "One terminal series as it was known at a moment, with every stored vintage of its "
        "latest value. This is the traceability escape hatch: what did we believe, and when did "
        "we change our mind."
    )
)
def terminal_series(
    series_id: str, as_of: str | None = None, limit: int = DEFAULT_ROW_LIMIT
) -> str:
    """A series, point-in-time, plus its revision history."""
    instant, explicit = _parse_as_of(as_of)
    sql = f"""
        SELECT DISTINCT ON (value_date)
               value_date, value, as_of, as_of_basis, source_batch
        FROM {SCHEMA_TERMINAL}.observations
        WHERE series_id = %(series_id)s AND as_of <= %(as_of)s
        ORDER BY value_date DESC, as_of DESC
    """
    result = run_query(sql, {"series_id": series_id, "as_of": instant}, limit=limit)
    if not result.rows:
        return (
            f"No observations for {series_id!r} at or before "
            f"{instant.isoformat(timespec='seconds')}. Either the series id is wrong, or nothing "
            f"had been published by that instant."
        )
    note = (
        _as_of_note(instant, explicit)
        + "\n\nOne row per `value_date`: the newest vintage not later than the cutoff. A "
        "revision adds a row here and never overwrites one, so an earlier `as_of` genuinely "
        "returns the earlier number."
    )
    return render_result(result, note=note)


@server.tool(
    description=(
        "Transmission graph edges: what theory expects a relationship to do, and what the data "
        "measures. Set `conflicts_only` to see just the edges currently running opposite to "
        "theory — the highest-signal output here."
    )
)
def terminal_edges(conflicts_only: bool = False, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Edges with their betas, correlation percentiles and sign conflicts."""
    where = "WHERE s.sign_conflict" if conflicts_only else ""
    sql = f"""
        SELECT d.from_series, d.to_series, d.expected_sign, d.typical_lag_days, d.chain,
               s.as_of, s.value_date, s.beta, s.beta_window, s.beta_t_stat, s.r_squared,
               s.corr, s.corr_percentile, s.sign_conflict, s.significant, s.n_obs
        FROM {SCHEMA_TERMINAL}.edge_definitions d
        LEFT JOIN LATERAL (
            SELECT * FROM {SCHEMA_TERMINAL}.edge_stats e
            WHERE e.from_series = d.from_series AND e.to_series = d.to_series
            ORDER BY e.as_of DESC LIMIT 1
        ) s ON TRUE
        {where}
        ORDER BY d.from_series, d.to_series
    """
    note = (
        "`expected_sign` of **0 is a real value**, not a missing one: it means the sign is "
        "genuinely regime-dependent and asserting one would mislead exactly when it matters. "
        "`corr_percentile` says where the current correlation sits in its own history — a "
        "correlation of 0.4 means nothing until you know whether that is the 5th or the 95th "
        "percentile for this pair. `beta_window` is reported because a fixed-window estimate is "
        "an average over regimes, not a fact about today."
    )
    return render_result(run_query(sql, limit=limit), note=note)


# --- the escape hatch --------------------------------------------------------------------------


@server.tool(
    description=(
        "Run a read-only SQL query against the quantdesk database. Prefer the domain tools "
        "above — they encode the caveats each dataset needs. Read the `quantdesk://schema` "
        "resource before writing a query so it is aimed rather than guessed. Single statement, "
        "SELECT/WITH only, row-capped."
    )
)
def query_sql(sql: str, limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Arbitrary read-only SQL."""
    try:
        statement = check_sql(sql)
    except SqlRejected as exc:
        return f"Query rejected: {exc}"

    try:
        result = run_query(statement, limit=limit)
    except psycopg.errors.ReadOnlySqlTransaction:
        # The interesting case, and proof the layering is right: a write phrased to slip past
        # the prefix check -- `WITH x AS (DELETE ... RETURNING *) SELECT * FROM x` begins with
        # `WITH` -- reaches the database and is refused *there*. Reported as text rather than
        # raised so the model reads an explanation instead of a transport error.
        return (
            "Refused by the database: this connection is read-only and the statement attempts "
            "to write. Note that it passed the SELECT/WITH check, which is exactly why that "
            "check is not the safety boundary -- the role has no write grant."
        )
    except psycopg.errors.InsufficientPrivilege as exc:
        return f"Refused by the database: {exc}".strip()
    except psycopg.errors.QueryCanceled:
        return (
            f"Query cancelled: it exceeded the {STATEMENT_TIMEOUT_MS // 1000}s statement "
            "timeout. Narrow it -- an unbounded scan of `research.trials` or "
            "`terminal.observations` will not finish."
        )
    except psycopg.Error as exc:
        # Syntax errors, unknown columns: the model's next attempt is better with the real
        # message than with a generic failure.
        return f"Query failed: {exc}".strip()

    return render_result(result)


# --- the schema resource ------------------------------------------------------------------------


@server.resource(
    "quantdesk://schema",
    name="quantdesk schema",
    description="Tables and columns across all three modules, with what each one means.",
    mime_type="text/markdown",
)
def schema_resource() -> str:
    """The schema, with meanings — so `query_sql` is aimed rather than guessed.

    Column *names* are read live from the database so this can never drift from reality; the
    meanings that matter are written here, because "what does `as_of_basis` mean" is not
    answerable from a column name and is exactly what a model needs to avoid a wrong query.
    """
    sql = """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = ANY(%(schemas)s)
        ORDER BY table_schema, table_name, ordinal_position
    """
    result = run_query(
        sql, {"schemas": [SCHEMA_GEX, SCHEMA_RESEARCH, SCHEMA_TERMINAL]}, limit=MAX_ROW_LIMIT
    )

    lines = [
        "# quantdesk schema",
        "",
        "Three modules, one database, one schema each. All access is read-only.",
        "",
        "## What matters before you query",
        "",
        (
            f"- **`{SCHEMA_GEX}`** — option gamma positioning. `snapshots` indexes captured chains "
            "(per-contract rows live in Parquet on disk, not here); `gex_levels` holds the computed "
            "flip point and walls per `(snapshot_id, filter)`. A null level is a real answer, not a "
            "zero. `captured_at` is tz-aware UTC."
        ),
        (
            f"- **`{SCHEMA_RESEARCH}`** — EdgeLab. `trials` holds **every** combination ever "
            "backtested, losers included, because the count is the denominator of the noise "
            "ceiling. Never summarise a trial's Sharpe without comparing it to "
            "sqrt(2·ln N)/sqrt(oos_years) where N is the total trial count."
        ),
        (
            f"- **`{SCHEMA_TERMINAL}`** — xactx cross-asset. `observations` is **point-in-time**: "
            "its primary key is `(series_id, value_date, as_of)`, a revision adds a row and never "
            "overwrites one. Any query without an `as_of <= ...` filter silently returns "
            "latest-known, which is a look-ahead answer to a historical question."
        ),
        "",
        "## Columns",
        "",
    ]

    current: tuple[str, str] | None = None
    for schema, table, column, data_type in result.rows:
        if (schema, table) != current:
            current = (schema, table)
            lines.append("")
            lines.append(f"### `{schema}.{table}`")
        lines.append(f"- `{column}` — {data_type}")

    return "\n".join(lines)


def build_server() -> MCPServer:
    """The configured server. A function so tests can import it without starting a transport."""
    return server
