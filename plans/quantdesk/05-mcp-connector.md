# 05 · MCP connector — T82

## Goal

Point a model at everything the desk knows, without paying for API credits. An MCP server
exposing the three schemas read-only, driven from a local client — the Claude CLI on the
existing subscription first, a locally hosted model just as well — rather than through the
Anthropic API.

This is the task the whole merge was arguably for: three stores became one database, and one
database is something a connector can be pointed at.

## What the user sees

In a session on this machine — a `claude` one, or any MCP client pointed at the server:

> *"What did GEX say about SPY on the three days the terminal flagged a regime change last
> month, and did any research survivor trade SPY on those days?"*

and the model answers it by querying, rather than by being told.

## Design decisions

**Domain tools first, raw SQL as the escape hatch.** A server that exposes only `query_sql`
makes the model guess at a schema it has never seen, and the failure mode is a confidently
wrong query. The tool surface is a handful of sharp tools that encode the questions actually
worth asking, plus `query_sql` for everything else:

| tool | what it answers |
|---|---|
| `gex_levels(symbol, date?)` | flip point, walls, regime for a symbol on a date |
| `gex_decisions(symbol?, since?)` | the decision log and its resolved outcomes |
| `research_leaderboard(market?, top_n?)` | survivors, with the noise ceiling alongside |
| `research_trial(hash)` | one trial in full, params and both splits |
| `terminal_board(as_of?)` | the change board, point-in-time |
| `terminal_series(series_id, as_of?)` | one series as it was known at a moment |
| `terminal_edges(conflicts_only?)` | transmission graph edges and sign conflicts |
| `query_sql(sql, limit?)` | anything else, read-only, row-capped |

Plus a schema resource the model can read: table and column names with the one-line meaning
of each, so `query_sql` is aimed rather than guessed.

**Read-only is enforced by Postgres, not by the server.** The connector authenticates as
`quantdesk_ro` (created in T76). String-inspecting SQL for `DROP` is security theatre; a role
that has no write grant cannot write however the query is phrased. The server additionally
sets a statement timeout and wraps every query in a read-only transaction, but those are for
runaway queries, not for safety.

**Every result carries its caveats.** `research_leaderboard` returns the noise ceiling and the
OOS-reuse warning in its payload, not only in the docs. `terminal_*` returns the `as_of` it
actually used and the `as_of_basis` of the rows. GEX results carry `captured_at`. A model
summarizing a leaderboard without the noise ceiling will report luck as an edge, and it is
cheaper to make that structurally impossible than to prompt against it.

**Row caps, always.** Default 100 rows, hard maximum 1000, and the response says when it
truncated. `research.trials` has 134,377 rows and an uncapped `SELECT *` would blow the
context window and teach the model nothing.

**stdio transport, any local client.** The server runs as a local process the client
launches. Nothing is exposed on the network, nothing needs authentication in front of it, and
the connector has no HTTP surface to get wrong. The user's call, 2026-09-19: no remote/HTTP
variant is designed, built or left half-specified here.

**The client is not part of the contract.** The Claude CLI is the first one, registered in a
project-level `.mcp.json` at the repo root, but that file is *an instance* of the
registration, not the interface — the interface is the command that starts the server. A local
model driving an MCP-capable client (LM Studio, Goose, Cline) is a supported second client,
and the docs give the generic stdio invocation so pointing one at the desk is a config
exercise rather than a port. The limit there is the model, not the transport: a 7B–14B on this
host handles the domain tools, and will mangle `query_sql` and drop bare `as_of` arguments.
Which is a second argument for sharp tools first — the escape hatch is the part a small model
cannot use.

**Billing.** Driving the connector from the Claude CLI on a subscription plan
consumes that plan's usage, not API credits — which is the constraint that motivated this
whole approach. Confirm against current plan terms before relying on it for heavy use; the
design does not depend on the answer, only the cost does. A local model costs nothing to run,
but since the CLI is already off the credit meter it buys offline operation and data locality
rather than savings.

## Tasks

### T82 · Opus · T76

An MCP server over the three schemas, plus its registration and docs.

- `backend/app/mcp/` — server, tool definitions, the schema resource, and a formatter that
  renders result sets compactly (a markdown table beats JSON for a model reading rows).
- Connect as `quantdesk_ro` via its own `DATABASE_URL_RO`; the server must fail loudly at
  startup if the role it connected as can write.
- `query_sql`: single statement only, `SELECT`/`WITH` only, `SET TRANSACTION READ ONLY`, a
  statement timeout, and the row cap. Reject multi-statement input rather than executing the
  first.
- `.mcp.json` at the repo root registering the server for the Claude CLI, plus the generic
  stdio invocation (command, args, env) documented beside it so another local client can be
  pointed at the same process. No Claude-specific assumptions in the tool definitions.
- Tests: every tool against a seeded database, the row cap, the timeout, the multi-statement
  rejection, and an explicit test that a write attempt through `query_sql` fails at the
  database.
- `context/` gets a page documenting the tool surface, and `CLAUDE.md`'s context index gains
  a row for it.

May be built before T77/T79 land — it only depends on the schemas existing, and the research
and terminal tools can be added as those modules arrive. Building it early is the fastest way
to find out whether the schema layout is actually queryable.

## Verified facts

- `quantdesk_ro` is created in T76 with `GRANT SELECT` on all three schemas plus
  `ALTER DEFAULT PRIVILEGES`, so tables added later are readable without a new grant.
- `research.trials` holds 134,377 rows today and grows every cycle — the row cap is load-
  bearing, not decorative.
- `terminal.observations` is the other large table, and its point-in-time reads need an
  explicit `as_of` or they silently return latest-known, which is the exact mistake the module
  exists to prevent.
- The repo has no auth of any kind and is reached over Tailscale remotely. Nothing in this
  task changes that, and nothing in it should assume a logged-in user exists.

## Acceptance

- `claude mcp list` shows the server connected, and every tool is callable in a real session.
- The same server connects, and every tool is callable, from a second non-Claude stdio client.
  This is the check that catches an accidental Claude-shaped assumption — tool-description
  length, annotation fields, resource support — because local clients' MCP coverage is
  patchier than the CLI's.
- A question requiring two modules — a GEX level and a terminal regime on the same date — is
  answered correctly from a populated lab database, verified by checking the answer against
  the UI.
- `query_sql` with an `INSERT`, an `UPDATE`, a `DROP`, and a `SELECT; DELETE` pair each fail,
  and the write failures come from Postgres.
- A `SELECT * FROM research.trials` returns the cap and says it truncated.
- A deliberately slow query is killed by the statement timeout rather than hanging the
  session.
- `research_leaderboard`'s payload contains the noise ceiling.

## Likely first-contact failures

- **Stdout is the transport.** A stray `print`, or the `logging.basicConfig` call this
  codebase makes at import time, will corrupt the stdio protocol and the server will fail in a
  way that looks like the client's fault. All logging goes to stderr or a file.
- **Connecting as the wrong role.** If `DATABASE_URL_RO` is missing and the server falls back
  to the app's own URL, everything works and nothing is read-only. Fail closed: no fallback,
  and assert the connection cannot write at startup.
- **`SELECT`-prefix checks are not a safety boundary.** `WITH x AS (DELETE … RETURNING *)
  SELECT * FROM x` starts with `WITH`. This is why the role, not the parser, is the control.
- **Timezones in tool arguments.** A bare date string reaching a `TIMESTAMPTZ` comparison
  resolves against the server's zone. Every date argument is parsed explicitly; invariant 4
  applies to the connector too.
- **Latest-known when a vintage was meant.** The terminal tools must default `as_of` to *now*
  explicitly and say so in the result, so a model asking a historical question cannot silently
  get a look-ahead answer.
- Context blowout from a wide `SELECT *` on a table with many columns, even under the row cap.
  Prefer named column lists in the domain tools.
- **A smaller model on the other end.** The safety boundary does not move — the role still
  cannot write — but "every result carries its caveats" stops being belt-and-braces and becomes
  the only thing between a leaderboard and a hallucinated edge. Whatever local model gets
  pointed at this, test the noise-ceiling case specifically.

## Out of scope

- Any write path. The connector reads. A tool that triggers a capture or promotes a paper
  candidate is a separate decision with a separate threat model, and `CLAUDE.md`'s no-order-
  routing rule means it can never reach an execution path anyway.
- Remote/HTTP MCP and browser access. Clients are local and launch the server over stdio.
- Shipping or configuring a local model. The connector is client-agnostic; choosing, hosting
  and tuning a model against it is the user's business, not this task's.
- Embeddings, vector search, RAG over the reports.
- Any use of the Anthropic API with credits. That is the thing this task exists to avoid.

## Result - T82

**Done 2026-09-20.** 1,094 backend tests green (34 added; 8 Postgres-gated), linter clean. Nine
tools and a schema resource, verified over a real stdio MCP handshake rather than only by unit
test: `initialize` returns `quantdesk 1.0.0`, `tools/list` returns all nine, `resources/list`
returns `quantdesk://schema`, a `research_leaderboard` call comes back with the noise ceiling on
its first line, and a `DELETE` through `query_sql` is refused.

### The safety model, and the part that matters

**`quantdesk_ro` is the boundary; nothing in the server is.** `sqlguard.py` opens by saying so,
because the obvious way to harden this -- adding keywords to a denylist -- is effort spent on the
wrong layer. The proof is in the tests: `WITH x AS (DELETE FROM research.trials RETURNING *)
SELECT * FROM x` **passes** `check_sql` by design (it begins with `WITH`, and no prefix check
ever written catches it) and is refused by Postgres. Both halves are asserted, so nobody
"fixes" the guard and quietly moves the boundary to the wrong place.

Fail-closed: `DATABASE_URL_RO` has no fallback to `DATABASE_URL`, because a fallback produces a
connector that works perfectly, answers every question, and is not read-only.

### Caught by doing

**The startup assertion was wrong on its first run, and refused to start a correct connector.**
It probed with `CREATE TEMPORARY TABLE` and treated success as proof of write access -- but
Postgres grants `TEMP` on a database to `PUBLIC` by default, so `quantdesk_ro` creates temporary
tables happily. A temp table cannot touch any of this data; the probe proved nothing and failed
closed on a false positive. Replaced with `has_schema_privilege` / `has_table_privilege`, which
answer the question that actually matters (can this role write *our* tables) authoritatively and
without writing anything. Recorded in the function's docstring and in `context/mcp-connector.md`
so the next person does not reinstate it.

Two smaller ones: `SET` takes no bind parameters in Postgres (`SET statement_timeout = $1` is a
syntax error), and a write that slips past the guard surfaced as an unhandled
`ReadOnlySqlTransaction` -- now caught and returned as an explanation, because a model reads a
message better than a transport error.

### Design notes

**Caveats are payload, not documentation.** `research_leaderboard` returns the total trial
count, a per-row `row_noise_ceiling`, an `above_ceiling` flag, a count of how many rows clear it,
and the OOS-reuse warning -- against the live registry it reports "136,507 trials ... **0 of the
3 rows below clear their own ceiling**". A model summarising a leaderboard without that *will*
report luck as an edge, and making it structurally impossible is cheaper than prompting against
it. The terminal tools state the `as_of` they used and say so loudly when it defaulted to
latest-known.

**Markdown tables, not JSON**, because JSON repeats every column name on every row and a
20-column result would spend its budget restating the schema. Nulls render as a middle dot --
never blank, never zero -- and truncation is stated *above* the table where a top-down read
cannot miss it.

**The schema resource reads column names live from the database** so it cannot drift, with the
meanings that a column name does not carry written by hand: what `as_of_basis` is, why `trials`
keeps its losers, and that a terminal query without an `as_of` filter silently returns a
look-ahead answer.

### Verified against live data

- Point-in-time through the connector: `terminal_series('macro.payrolls', as_of='2024-02-15')`
  returns the original 157700 print, not the 157032 revision.
- Row cap and truncation notice on `SELECT * FROM research.trials`.
- `pg_sleep(30)` is killed by the 10s statement timeout.
- A cross-schema query joining `research.trials` to `terminal.series_metadata` returns in one
  call -- which is the thing the whole module merge was for.

### Not verified

**The second, non-Claude client.** The acceptance list asks for every tool to be callable from a
non-Claude stdio client, to catch a Claude-shaped assumption. What was done instead is a raw
JSON-RPC handshake written against the MCP spec with no SDK client involved -- which exercises
the same surface and found nothing client-specific, but is not the same as LM Studio or Goose
actually driving it. Worth doing when one is installed.

`claude mcp list` was likewise not run: the connector is registered in `.mcp.json` but a CLI
restart is needed to pick it up, and that is the user's to do.

The GEX half of the two-module acceptance question could not be exercised: `gex.snapshots` is
empty in the lab database (no chains captured yet on this host). The query is valid and returns
cleanly; it simply has nothing to join to.
