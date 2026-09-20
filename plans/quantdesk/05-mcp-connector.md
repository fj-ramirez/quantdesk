# 05 · MCP connector — T82

## Goal

Point Claude at everything the desk knows, without paying for API credits. An MCP server
exposing the three schemas read-only, driven from the Claude CLI on the existing
subscription rather than through the Anthropic API.

This is the task the whole merge was arguably for: three stores became one database, and one
database is something a connector can be pointed at.

## What the user sees

In a `claude` session on this machine:

> *"What did GEX say about SPY on the three days the terminal flagged a regime change last
> month, and did any research survivor trade SPY on those days?"*

and Claude answers it by querying, rather than by being told.

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

**stdio transport, CLI only.** The server runs as a local process the client launches,
registered in a project-level `.mcp.json` at the repo root. Nothing is exposed on the
network, nothing needs authentication in front of it, and the connector has no HTTP surface
to get wrong. The user's call, 2026-09-19: the CLI is the client, so no remote/HTTP variant is
designed, built or left half-specified here.

**Billing.** Driving the connector from the Claude CLI on a subscription plan
consumes that plan's usage, not API credits — which is the constraint that motivated this
whole approach. Confirm against current plan terms before relying on it for heavy use; the
design does not depend on the answer, only the cost does.

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
- `.mcp.json` at the repo root registering the server for the Claude CLI.
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

## Out of scope

- Any write path. The connector reads. A tool that triggers a capture or promotes a paper
  candidate is a separate decision with a separate threat model, and `CLAUDE.md`'s no-order-
  routing rule means it can never reach an execution path anyway.
- Remote/HTTP MCP and browser access. The CLI is the client.
- Embeddings, vector search, RAG over the reports.
- Any use of the Anthropic API with credits. That is the thing this task exists to avoid.
