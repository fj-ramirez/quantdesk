"""The quantdesk MCP server: the whole desk, read-only, over stdio (T82).

Three stores became one database in T75–T80, and one database is something a connector can be
pointed at. This is that connector.

**Domain tools first, `query_sql` as the escape hatch.** A server exposing only raw SQL makes
the model guess at a schema it has never seen, and the failure mode is a confidently wrong
query rather than an error. The ten tools below encode the questions actually worth asking;
`query_sql` covers everything else and is deliberately the least convenient option.

**A domain tool that is awkward for the common case costs more than the calls it wastes**
(T105/T106). `gex_levels` used to take one symbol and return every stored snapshot of it, so
the everyday question -- the board, now -- meant either 28 calls or a drop to `query_sql`,
which is what the `market-research` skill ended up teaching. Bypassing the domain tool also
bypasses its caveats, so ergonomics here is an honesty property, not a convenience one. The
same reasoning added `desk_status` and `gex_track_record`: both were hand-written SQL in a
prompt file, and the track record was additionally *hardcoded as prose* that then rotted.

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
import json

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
        "GEX levels: flip point, call/put walls and net gamma, from the stored snapshot index. "
        "**One row per (symbol, filter) for each symbol's most recent snapshot** on or before "
        "`date`. `symbol` accepts a comma-separated list, or omit it for the whole universe — "
        "the board in one call. Set `history=true` for every stored snapshot of one symbol "
        "over time instead."
    )
)
def gex_levels(
    symbol: str | None = None,
    date: str | None = None,
    history: bool = False,
    limit: int = DEFAULT_ROW_LIMIT,
) -> str:
    """Flip point, walls and regime -- latest per symbol, or one symbol's history.

    **Latest-per-symbol is the default because it is the question** (T105). This tool used to
    order every stored snapshot by `captured_at DESC` and cut at `limit`, which meant a single
    `gex_levels("QQQ")` returned ~33 snapshots across 3 filters -- roughly 9,000 tokens to
    answer something whose answer is the first three rows, with a truncation warning attached.
    Its description already claimed it returned "the most recent snapshot"; now it does.
    """
    on = _parse_date(date, field="date")
    symbols = [part.strip().upper() for part in symbol.split(",") if part.strip()] if symbol else None

    if history:
        if not symbols or len(symbols) != 1:
            return (
                "`history=true` describes one symbol over time, so it needs exactly one "
                "`symbol`. Omit `history` to compare symbols at their latest capture."
            )
        sql = f"""
            SELECT s.underlying, s.captured_at, s.session_date, s.spot, s.is_eod, l.filter,
                   l.net_gex, l.flip_point, l.call_wall, l.call_wall_gex,
                   l.put_wall, l.put_wall_gex, l.max_abs_strike
            FROM {SCHEMA_GEX}.gex_levels l
            JOIN {SCHEMA_GEX}.snapshots s ON s.id = l.snapshot_id
            WHERE s.underlying = %(symbol)s
              {"AND s.captured_at::date <= %(on)s" if on else ""}
            ORDER BY s.captured_at DESC, l.filter
        """
        params: dict[str, object] = {"symbol": symbols[0]}
        if on:
            params["on"] = on
        scope_note = f"**History for {symbols[0]}**, newest capture first."
    else:
        sql = f"""
            WITH latest AS (
                SELECT DISTINCT ON (s.underlying)
                       s.id, s.underlying, s.captured_at, s.session_date, s.spot, s.is_eod
                FROM {SCHEMA_GEX}.snapshots s
                WHERE (%(symbols)s::text[] IS NULL OR s.underlying = ANY(%(symbols)s))
                  {"AND s.captured_at::date <= %(on)s" if on else ""}
                ORDER BY s.underlying, s.captured_at DESC
            )
            SELECT t.underlying, t.captured_at, t.session_date, t.spot, t.is_eod, l.filter,
                   l.net_gex, l.flip_point, l.call_wall, l.call_wall_gex,
                   l.put_wall, l.put_wall_gex, l.max_abs_strike
            FROM latest t
            JOIN {SCHEMA_GEX}.gex_levels l ON l.snapshot_id = t.id
            ORDER BY t.underlying, l.filter
        """
        params = {"symbols": symbols}
        if on:
            params["on"] = on
        scope_note = (
            "Each symbol's **most recent** capture"
            + (f" on or before {on.isoformat()}" if on else "")
            + ". Pass `history=true` with one symbol for its history instead."
        )

    result = run_query(sql, params, limit=limit)
    note = (
        f"{scope_note}\n\n"
        "`captured_at` is when the chain was captured, in UTC; `session_date` is the trading "
        "session its *contents* belong to, which differs on any weekend or pre-open capture — "
        "group by `session_date`, not by `captured_at`. A null level is a real answer: the "
        "filter admitted no contracts, the profile never changed sign, or no strike carried "
        "enough net gamma to be a wall. It is never a zero."
    )
    return render_result(result, note=note)


@server.tool(
    description=(
        "The decision log: opportunities the engine emitted, with the levels as suggested and "
        "the outcome columns filled in from later bars. Filter by symbol, decision `key` "
        "(e.g. FADE_CALL_WALL, GAMMA_PIN) and/or a start date. Set `thesis=true` to get each "
        "row's full reasoning rather than a second query for it."
    )
)
def gex_decisions(
    symbol: str | None = None,
    key: str | None = None,
    since: str | None = None,
    thesis: bool = False,
    limit: int = DEFAULT_ROW_LIMIT,
) -> str:
    """Emitted opportunities, the levels they named, and how they resolved."""
    start = _parse_date(since, field="since")
    clauses = []
    params: dict[str, object] = {}
    if symbol:
        clauses.append("underlying = %(symbol)s")
        params["symbol"] = symbol.upper()
    if key:
        clauses.append("key = %(key)s")
        params["key"] = key.upper()
    if start:
        clauses.append("decided_on >= %(start)s")
        params["start"] = start
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, underlying, decided_on, filter, key, side, status, grade,
               spot, entry, stop, target, snapshot_id,
               outcome, fill, result_r, mfe_r, mae_r, mark_r
        FROM {SCHEMA_GEX}.decisions
        {where}
        ORDER BY decided_on DESC, underlying
    """
    result = run_query(sql, params, limit=limit)
    note = (
        "A recorded level is a commitment the track record scores: rows are inserted when first "
        "seen and **never rewritten**, wrong ones included.\n\n"
        "**`outcome` is never null.** It is one of `pending` (no resolution yet), `untriggered` "
        "(the entry was never reached -- not a loss; most limit-style decisions end here), "
        "`stop`, or `target`. The test for "
        "*unresolved* is **`result_r IS NULL`**, not `outcome IS NULL`: far more rows carry an "
        "`outcome` than carry a scored `result_r`, so counting the wrong one inflates every "
        "track-record denominator. Use `gex_track_record` rather than averaging these by hand."
    )
    rendered = render_result(result, note=note)

    if thesis and result.rows:
        ids = [row[0] for row in result.rows]
        if len(ids) > 10:
            return rendered + (
                f"\n\n*(`thesis` omitted: {len(ids)} rows. Narrow with `key`, `symbol` or "
                "`since` to 10 or fewer and ask again — full reasoning is long, and truncating "
                "it to fit a table is how a caveat gets lost.)*"
            )
        detail = run_query(
            f"""
            SELECT id, underlying, key,
                   payload::jsonb->>'thesis' AS thesis,
                   payload::jsonb->>'invalidation' AS invalidation,
                   payload::jsonb->>'structure' AS structure
            FROM {SCHEMA_GEX}.decisions WHERE id = ANY(%(ids)s) ORDER BY id
            """,
            {"ids": ids},
            limit=len(ids),
        )
        parts = ["", "---", "", "## Reasoning, in full"]
        for row in detail.rows:
            d_id, und, d_key, th, inval, struct = row
            parts.append(f"\n**#{d_id} {und} {d_key}**")
            for label, blob in (("Thesis", th), ("Invalidation", inval)):
                if not blob:
                    continue
                try:
                    items = json.loads(blob)
                except (TypeError, ValueError):
                    items = [blob]
                parts.append(f"\n*{label}:*")
                parts.extend(f"- {item}" for item in (items if isinstance(items, list) else [items]))
            if struct:
                parts.append(f"\n*Structure:* {struct}")
        rendered += "\n".join(parts)

    return rendered


@server.tool(
    description=(
        "Is the desk fresh? One call: the newest capture, terminal observation, research trial "
        "and decision, with the rules that decide whether 'recent' actually means 'current'. "
        "**Run this before quoting any number from this database.**"
    )
)
def desk_status() -> str:
    """Freshness across all three modules, with the rules that qualify it.

    This exists because the alternative was a hand-written `query_sql` at the top of every
    session (T106). The `market-research` skill opens by calling a freshness check
    "non-negotiable, and first, every time" and then supplies the SQL to run -- which is a
    tool-surface gap wearing a prompt's clothes. A tool can also carry the rules *beside* the
    numbers they qualify, which prose in a separate file cannot.
    """
    gex = run_query(
        f"""
        SELECT max(captured_at) AS last_capture,
               max(session_date) AS last_session,
               count(*) FILTER (WHERE session_date = (SELECT max(session_date) FROM {SCHEMA_GEX}.snapshots))
                   AS snaps_in_last_session,
               count(DISTINCT underlying) FILTER (
                   WHERE session_date = (SELECT max(session_date) FROM {SCHEMA_GEX}.snapshots)
               ) AS symbols_in_last_session
        FROM {SCHEMA_GEX}.snapshots
        """,
        limit=1,
    )
    decisions = run_query(
        f"""
        SELECT max(decided_on) AS last_decision,
               count(*) FILTER (WHERE result_r IS NULL) AS unresolved,
               count(result_r) AS scored
        FROM {SCHEMA_GEX}.decisions
        """,
        limit=1,
    )
    terminal = run_query(
        f"""
        SELECT max(as_of) AS last_as_of, count(DISTINCT series_id) AS series
        FROM {SCHEMA_TERMINAL}.observations
        """,
        limit=1,
    )
    research = run_query(
        f"""
        SELECT max(run_date) AS last_run, count(*) AS trials
        FROM {SCHEMA_RESEARCH}.trials
        """,
        limit=1,
    )

    def cell(result, idx):
        return result.rows[0][idx] if result.rows else None

    from app.mcp.format import render_rows, render_value

    rows = [
        ("gex — last capture", render_value(cell(gex, 0))),
        ("gex — last session covered", render_value(cell(gex, 1))),
        ("gex — snapshots in that session", render_value(cell(gex, 2))),
        ("gex — symbols in that session", render_value(cell(gex, 3))),
        ("gex — last decision emitted", render_value(cell(decisions, 0))),
        ("gex — decisions scored / unresolved",
         f"{render_value(cell(decisions, 2))} / {render_value(cell(decisions, 1))}"),
        ("terminal — newest as_of", render_value(cell(terminal, 0))),
        ("terminal — series count", render_value(cell(terminal, 1))),
        ("research — last run_date", render_value(cell(research, 0))),
        ("research — trials searched", render_value(cell(research, 1))),
    ]
    table = render_rows(["fact", "value"], [(a, b) for a, b in rows])

    note = (
        "**Three rules decide whether these numbers mean the market is current.** Each has "
        "already caused a wrong read on this desk.\n\n"
        "1. **A weekend or holiday capture holds the previous session's book.** Cboe serves the "
        "last session, so a Sunday capture is Friday's chain -- useful, and not stale, but it "
        "is Friday's. `session_date` is the session the contents belong to; `captured_at` is "
        "the wall clock. Group and compare by `session_date`.\n"
        "2. **A passed opex voids a gamma profile rather than ageing it.** The third Friday "
        "(quarterly in Mar/Jun/Sep/Dec) expires the near walls. Levels captured before an opex "
        "that has since passed must not be quoted at all -- not even with a caveat.\n"
        "3. **A multi-day gap with sessions inside it is an outage**, and `catchup_skipped … "
        "\"not a trading day\"` in the worker log is correct behaviour rather than one. Compare "
        "`last session covered` against the sessions that have actually traded since.\n\n"
        "`decisions scored / unresolved` uses `result_r IS NOT NULL` as the test for scored, "
        "which is the only correct one -- see `gex_track_record`."
    )
    return f"{note}\n\n{table}"


@server.tool(
    description=(
        "The decision engine's track record, per signal key, with the standard error beside "
        "every mean. Read it per key -- the aggregate hides everything useful. Optionally "
        "filter by `since` or a single `key`."
    )
)
def gex_track_record(key: str | None = None, since: str | None = None) -> str:
    """Per-key performance, with the sample size and standard error that qualify it.

    **Resolved means `result_r IS NOT NULL`.** Not `outcome IS NOT NULL`: every row carries an
    `outcome`, including `pending` and `untriggered`, so testing that instead inflates the
    denominator by more than a factor of two and deflates every mean with rows that never
    scored. That mistake is the single easiest way to misreport this table.

    **Keys are read from the data, never enumerated here** (T106). `T99` added `GAMMA_PIN`
    while this tool was being written; a hardcoded list would have dropped it from the record
    on the day it started emitting, which is exactly the class of silent wrongness the
    `desk-integrity` initiative exists to remove.
    """
    start = _parse_date(since, field="since")
    clauses = ["result_r IS NOT NULL"]
    params: dict[str, object] = {}
    if key:
        clauses.append("key = %(key)s")
        params["key"] = key.upper()
    if start:
        clauses.append("decided_on >= %(start)s")
        params["start"] = start
    where = " AND ".join(clauses)

    sql = f"""
        SELECT coalesce(key, 'ALL KEYS') AS key,
               count(*) AS n,
               count(*) FILTER (WHERE result_r > 0) AS wins,
               round(avg(result_r)::numeric, 3) AS avg_r,
               round((stddev_samp(result_r) / sqrt(count(*)))::numeric, 3) AS se,
               round(min(result_r)::numeric, 2) AS worst,
               round(max(result_r)::numeric, 2) AS best,
               min(decided_on) AS first_decision,
               max(decided_on) AS last_decision
        FROM {SCHEMA_GEX}.decisions
        WHERE {where}
        GROUP BY ROLLUP(key)
        ORDER BY key = 'ALL KEYS', avg_r DESC NULLS LAST
    """
    result = run_query(sql, params, limit=MAX_ROW_LIMIT)
    if not result.rows:
        return (
            "No scored decisions match. Rows are only scored once `result_r` is filled in from "
            "later bars; `pending` and `untriggered` rows carry an `outcome` but no `result_r`, "
            "and never will in the untriggered case."
        )

    note = (
        "**Resolved means `result_r IS NOT NULL`.** Rows whose `outcome` is `pending` or "
        "`untriggered` are excluded: they carry an outcome but were never scored, and counting "
        "them would inflate `n` while deflating `avg_r`. `untriggered` in particular is not a "
        "loss -- most limit-style decisions never fill, which is the system working.\n\n"
        "**Read `n` and `se` before `avg_r`.** A mean R over a handful of trades is noise with "
        "a decimal point, and the same discipline the research module's noise ceiling enforces "
        "applies here: at n below roughly 30, `avg_r ± 2·se` will usually straddle zero, which "
        "means the sign of the mean is not established. Quote the `n` every time, and describe "
        "small samples as leanings rather than results.\n\n"
        "`wins` counts `result_r > 0`, so a scratch at exactly 0 is neither a win nor counted "
        "as a loss in that column. Keys come from the data, so a signal added later appears "
        "here without anyone editing this tool."
    )
    return render_result(result, note=note)


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
        "The paper watchlist: trials promoted to forward tracking, each with its latest "
        "forward score (`fwd_sharpe`, `fwd_return`, `fwd_max_dd` on bars after `promoted_at`). "
        "That forward record is the only evidence in EdgeLab that was never fitted -- and "
        "`fwd_bars` says how much of it there is, which decides whether it means anything yet."
    )
)
def research_paper(limit: int = DEFAULT_ROW_LIMIT) -> str:
    """Promoted candidates, oldest first, each with its latest forward score."""
    # LEFT JOIN, not an inner one: a candidate promoted since the last cycle has no score row
    # yet and must still appear. `DISTINCT ON` takes the newest measurement of each.
    sql = f"""
        SELECT c.hash, c.promoted_at, c.market, c.strategy, c.symbol, c.timeframe, c.params,
               c.promoted_oos_sharpe, c.sharpe_2x, c.neighbor_med,
               c.wf_pos, c.wf_active, c.wf_med, c.corr_max,
               s.scored_at, s.fwd_days, s.fwd_bars, s.fwd_sharpe, s.fwd_return, s.fwd_max_dd
        FROM {SCHEMA_RESEARCH}.paper_candidates c
        LEFT JOIN (
            SELECT DISTINCT ON (hash) hash, scored_at, fwd_days, fwd_bars,
                   fwd_sharpe, fwd_return, fwd_max_dd
            FROM {SCHEMA_RESEARCH}.paper_scores
            ORDER BY hash, scored_at DESC
        ) s ON s.hash = c.hash
        ORDER BY c.promoted_at
    """
    note = (
        "Ordered by promotion date, deliberately not by performance. Each row cleared a share of "
        "its noise ceiling, stayed profitable at **doubled** costs (`sharpe_2x`), survived a "
        "parameter-neighbourhood check (`neighbor_med`), passed a walk-forward gate "
        "(`wf_pos`/`wf_active`) and was not too correlated with the existing list (`corr_max`)."
        "\n\nEverything named `promoted_*` or gate-shaped describes the candidate **on the day "
        "it was promoted**. The `fwd_*` columns are what has happened since, measured on bars no "
        "selection step has touched — the only evidence here that cannot have been mined. "
        "`scored_at` is when that measurement was taken; a null means this candidate has never "
        "been scored, not that it went nowhere."
        "\n\n**Read `fwd_bars` before `fwd_sharpe`.** A "
        "forward Sharpe over a few dozen bars is noise with a decimal point, and quoting it as a "
        "result is the same error the noise ceiling exists to prevent on the leaderboard. Only "
        "`research_trial` history shows whether a number is trending."
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
