# The MCP connector (T82)

Read-only access to all three schemas over stdio, so a model can answer questions about the desk
by querying it rather than by being told. This is the task the module merge was arguably for:
three stores became one database, and one database is something a connector can be pointed at.

## Running it

**It points at the homeserver**, because that is the authoritative store: the homeserver runs
the captures, and its `gex.snapshots` is ahead of any laptop copy. The laptop database is a lab.

```
ssh -T homeserver "docker exec -i quantdesk-backend-1 sh -c '…exec python -m app.mcp'"
```

Registered for the Claude CLI in `.mcp.json` at the repo root, which carries the full command.
That file is *an instance* of the registration — the interface is the command, and any MCP
client that launches a stdio server works.

**SSH is not a second transport.** It is still stdio; SSH only moves "local process" onto the
other machine. The server runs *inside* the backend container, where `postgres:5432` is
reachable on the stack's `internal` network, so `compose.prod.yaml`'s posture is untouched: the
database stays on `internal` only, off Caddy's `edge`, with no published host port. The
alternative — publishing 5432 on the Tailscale interface and connecting from the laptop — would
have worked too, and was rejected because it trades a deliberate network property for
convenience the SSH form already provides. Reached over Tailscale; `homeserver` is MagicDNS.

**No secret lives in the repo, on the laptop, or in the laptop's environment.**
`DATABASE_URL_RO` is derived inside the container from two values already present there:
`QUANTDESK_RO_PASSWORD`, and the host/port/dbname tail of `DATABASE_URL` (`${DATABASE_URL#*@}`).
The prod backend service does not set `DATABASE_URL_RO` itself; if it ever does, the command
collapses to `python -m app.mcp` and this paragraph can go.

For another client (LM Studio, Goose, Cline), give it that same command with `transport: stdio`.
To run against the **local** stack instead — offline, or when the homeserver is down — swap the
command for `uv run --directory backend python -m app.mcp` with
`DATABASE_URL_RO=postgresql://quantdesk_ro:<password>@localhost:5432/gex` in its env. Register
one or the other, not both: two servers put two sets of nine identically-named tools in front of
the model and make it guess which desk it is looking at.

> The Claude CLI expands `${VAR}` in `.mcp.json` from the **process environment only** — not
> from this repo's `.env`, and not from `.claude/settings.local.json`'s `env` block. Both were
> tried on 2026-09-20. The SSH form needs no variable at all, which is part of why it won.

Remote/HTTP MCP is still explicitly not designed here (the user's call, 2026-09-19), and the
SSH form is not a departure from that: there is no HTTP surface and nothing is listening.

## The safety model, in one line

**`quantdesk_ro` is the boundary.** Everything else is ergonomics.

The connector authenticates as the role T76 created: `SELECT` on the three schemas, and nothing
else. A role with no write grant cannot write however a query is phrased, which is why
`app/mcp/sqlguard.py` opens by saying it is *not* a security boundary. The obvious way to harden
this — adding keywords to a denylist — is effort spent on the wrong layer:

```sql
WITH x AS (DELETE FROM research.trials RETURNING *) SELECT * FROM x
```

begins with `WITH` and passes any prefix check ever written. It is refused by the role, and
`tests/test_mcp_connector.py` asserts that refusal comes from Postgres.

**Fail closed.** `DATABASE_URL_RO` has no fallback to `DATABASE_URL`. A fallback would mean a
missing setting produced a connector that works perfectly, answers every question, and is not
read-only — invisible until it matters. `assert_read_only()` additionally verifies at startup
that the role holds no `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` on any quantdesk table and no
`CREATE` on any quantdesk schema, because pointing the variable at the application user is a
plausible mistake that reading the setting cannot catch.

> Note for anyone revisiting that check: it asks Postgres's privilege functions rather than
> attempting a write. The first version probed with `CREATE TEMPORARY TABLE` and refused to start
> a perfectly correct connector — Postgres grants `TEMP` on a database to `PUBLIC` by default, so
> `quantdesk_ro` creates temp tables happily. A temp table cannot touch this data; it proved
> nothing.

The read-only transaction, the 10s statement timeout and the row cap stop a *runaway* query, not
a malicious one.

## Tool surface

| tool | answers |
|---|---|
| `gex_levels(symbol, date?)` | flip point, walls, net gamma for a symbol |
| `gex_decisions(symbol?, since?)` | the decision log and its resolved outcomes |
| `research_leaderboard(market?, top_n?)` | survivors, **with the noise ceiling** |
| `research_trial(hash)` | one trial, params and both splits |
| `research_paper()` | the forward-tracking watchlist |
| `terminal_board(as_of?)` | the cross-asset board, point-in-time |
| `terminal_series(series_id, as_of?)` | one series as known at a moment |
| `terminal_edges(conflicts_only?)` | transmission graph and sign conflicts |
| `query_sql(sql, limit?)` | anything else, read-only, row-capped |

Plus a `quantdesk://schema` resource: column names read live from the database (so it cannot
drift) with the meanings that a column name does not carry — what `as_of_basis` is, why
`trials` keeps its losers, which queries silently look ahead.

**Domain tools first, `query_sql` as the escape hatch.** A server exposing only raw SQL makes the
model guess at a schema it has never seen, and the failure mode is a confidently wrong query
rather than an error. This also matters for a smaller local model: the escape hatch is precisely
the part a 7B–14B will mangle, so the sharp tools are what make it usable at all.

## Every result carries its caveats

Not documentation — payload:

- `research_leaderboard` returns the total trial count, a per-row `row_noise_ceiling`, an
  `above_ceiling` flag, a count of how many rows clear it, and the OOS-reuse warning. A model
  summarising a leaderboard without the ceiling **will** report luck as an edge, and making that
  structurally impossible is cheaper and more reliable than prompting against it.
- Terminal tools state the `as_of` they used, and say so loudly when it defaulted to
  latest-known — so a historical question cannot silently get a look-ahead answer.
- `terminal_edges` explains that `expected_sign = 0` is a real value (regime-dependent), not a
  missing one.
- Nulls render as `·`, never blank and never zero. Truncation is stated above the table.

## Gotchas

- **Stdout is the transport.** Nothing in the process may `print`. `app/mcp/__main__.py`
  configures logging to stderr *before* importing `app.*`, and that ordering is load-bearing — a
  stray line on stdout corrupts the protocol and the failure looks like the client's bug.
- **`SET` takes no bind parameters** in Postgres; `statement_timeout` is interpolated from this
  module's own constant.
- Row cap defaults to 100, maximum 1000. `research.trials` holds >134k rows.
