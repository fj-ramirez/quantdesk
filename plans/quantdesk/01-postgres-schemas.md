# 01 · Postgres schemas and the read-only role — T76

## Goal

Three namespaces in one database — `gex`, `research`, `terminal` — plus the read-only role the
MCP connector will authenticate as. GEX's existing tables move out of `public` so that no
module is privileged and `public` stays empty.

## What the user sees

Nothing. This is entirely below the API.

## Data

```
quantdesk (database)
  gex.        snapshots, gex_levels, decisions, bars, …   ← moved from public
  research.   trials, paper_candidates                    ← created empty by T77
  terminal.   observations, series_metadata, releases,
              ingest_batches, edge_definitions, edge_stats ← created empty by T79
  public.     empty, deliberately
```

`alembic_version` stays where Alembic puts it. One chain, one `alembic upgrade head`, three
schemas — not three chains. With a single application process and a single deploy step, three
version tables would be three ways for a deploy to be half-applied.

## Design decisions

**Schemas, not `module__table` prefixes.** The prefix form gives a naming convention that
nothing enforces. Schemas give `GRANT USAGE ON SCHEMA research TO quantdesk_ro`,
`pg_dump -n terminal`, a per-module `search_path`, and an unambiguous answer to "who owns this
table" that `psql \dn` prints.

**GEX moves too.** Leaving GEX in `public` while the newcomers get schemas would be the
cheaper migration and the worse design: the read-only role would need a different grant shape
for one module than the other two, and every cross-module query would have one unqualified
name in it. The move is one `ALTER TABLE … SET SCHEMA` per table in a single migration, and
the lab database can be rebuilt if it goes wrong.

**A read-only role now, used later.** `quantdesk_ro` is created here rather than in T82 so
that the schema grants live with the schemas, and so `ALTER DEFAULT PRIVILEGES` is in place
*before* T77 and T79 create their tables. Granting after the fact means remembering to
re-grant every time a module adds a table — the default-privileges form does not.

**Invariant 4 extends to every module.** `captured_at` is tz-aware UTC enforced at the DB
boundary by `UTCDateTime`. Everything the newcomers store follows the same rule: xactx's
`as_of` is already `TIMESTAMPTZ`; EdgeLab's `run_date`/`promoted_at` are currently `TEXT` and
become real timestamps in T77. No module gets to keep a naive or string timestamp.

## Tasks

### T76 · Opus · T75

Create the three schemas, move the GEX tables into `gex`, add `quantdesk_ro`, and make schema
placement declarative so later modules cannot forget it.

- One Alembic migration: `CREATE SCHEMA` ×3, `ALTER TABLE … SET SCHEMA gex` for every existing
  table, `CREATE ROLE quantdesk_ro NOLOGIN` + per-schema `GRANT USAGE`/`GRANT SELECT` +
  `ALTER DEFAULT PRIVILEGES … GRANT SELECT`.
- `env.py` gets `include_schemas=True` and a `version_table_schema` decision recorded in a
  comment.
- Module `Base` classes carry `__table_args__ = {"schema": SCHEMA_<MODULE>}` from
  `app/core/schemas.py`, so a new table lands in the right namespace by construction.
- `compose*.yaml` gains the `QUANTDESK_RO_PASSWORD` variable, required (`:?`) in prod and
  defaulted in dev, following the pattern the existing three credentials already use.
- Document the whole thing in `context/backend.md` and add it to `CLAUDE.md`'s invariants as
  invariant 7: *every module's tables live in its own schema; `public` stays empty.*

## Verified facts

- GEX's Alembic chain lives in `backend/alembic/versions/` with `sqlalchemy.url` injected by
  `env.py` from `app.config.settings`, not set in `alembic.ini`.
- The backend container runs `alembic upgrade head` before uvicorn binds, and
  `compose.prod.yaml` allows 60 s of `start_period` for it.
- The prod compose file already uses the `${VAR:?message}` form for the three Postgres
  credentials, so the pattern for a required new secret is established.
- `POSTGRES_*` defaults to `gex/gex/gex` in dev. The database *name* does not have to change
  for this initiative and should not — renaming it would repoint the dev volume.

## Acceptance

- `alembic upgrade head` on a populated lab database moves every GEX table and leaves the row
  counts identical. `alembic downgrade -1` puts them back.
- `alembic upgrade head` on an empty database produces the same end state.
- `psql -c '\dt public.*'` returns nothing.
- Connecting as `quantdesk_ro` can `SELECT` from all three schemas and cannot `INSERT`,
  `UPDATE`, `DELETE`, `CREATE` or `ALTER` in any of them.
- A table added to any module afterwards is readable by `quantdesk_ro` with no new grant.
- Full GEX test suite still green; the app serves the same data.

## Likely first-contact failures

- **Autogenerate wants to drop everything.** Without `include_schemas=True` — and with it,
  without careful `include_object` filtering — Alembic compares model metadata carrying a
  schema against a database it is only inspecting in `public`, and cheerfully writes a
  migration that drops all the real tables. Any generated migration must be read line by line
  before it is kept.
- **`ALTER DEFAULT PRIVILEGES` is per-granting-role.** Set for the wrong role, it silently
  applies to nothing, and the failure only shows up when a module adds a table months later.
- **Foreign keys and sequences.** `SET SCHEMA` carries the table's own sequences but a
  sequence explicitly owned elsewhere, or a cross-schema FK, needs its own statement.
- **`search_path`.** Raw SQL in the GEX codebase (health probes, any `text()` query) that
  names an unqualified table will resolve to nothing once `public` is empty.
- Alembic's own `alembic_version` table being moved by a well-meaning bulk `SET SCHEMA` loop,
  which breaks the very migration performing it.

## Out of scope

- Creating the research or terminal tables. Each module's port owns its own DDL; this task
  creates the empty namespaces and the rule.
- Any cross-module view or join. Nothing joins until there is a question that needs it.
- Connection pooling, replicas, or a separate analytics database.

## Result

**Done 2026-09-19.** 1,003 backend tests green (990 before, 13 added), both linters clean, and
every acceptance check run against the live lab Postgres rather than reasoned about.

What was verified, on a database seeded with rows first so the move had something to lose:

- `alembic upgrade head` moved all seven gex tables out of `public` with row counts identical
  (`snapshots=2`, `gex_levels=2`, `daily_bars=1`), and carried their seven `_id_seq` sequences
  with them.
- `alembic downgrade -1` put them back exactly — schemas dropped, role dropped, same counts —
  and a second `upgrade`/`downgrade` cycle repeated it.
- A from-scratch `upgrade head` on an empty database (`gex_fresh`) reached the identical end
  state: three schemas, seven tables in `gex`, `public` holding only `alembic_version`.
- Re-running `upgrade head` on an already-migrated database is a no-op, and `alembic_version`
  stays in `public`.
- `alembic revision --autogenerate` produces an **empty** diff — the check that the models and
  the migrated database actually agree.
- As `quantdesk_ro`: `select count(*) from gex.snapshots` → 2; INSERT, UPDATE and DELETE →
  *permission denied for table snapshots*; `CREATE TABLE` in `gex` and in `research` →
  *permission denied for schema*; `ALTER TABLE` → *must be owner*.
- A table created in `research` **after** the migration was readable by `quantdesk_ro` with no
  new grant, which is the `ALTER DEFAULT PRIVILEGES` half doing its job.
- `docker compose -f compose.yaml -f compose.prod.yaml config` aborts naming
  `QUANTDESK_RO_PASSWORD`; the dev stack defaults it.
- The rebuilt stack boots clean: `alembic upgrade head`, then `quantdesk_ro can now log in`,
  then uvicorn; `/health` reports `db: ok`; `/api/gex/snapshots`, `/api/gex/bars/SPY` and
  `/api/gex/health/capture` all serve from `gex.*`; and the capture worker registers its eight
  jobs with no wait. Frontend untouched — 357 tests and 0 lint errors, unchanged.

### Judgment calls

**The brief contradicted itself about the role, and this is how it was resolved.** It asks for
`CREATE ROLE quantdesk_ro NOLOGIN` in the migration *and* for a `QUANTDESK_RO_PASSWORD`
variable, but its own acceptance criterion requires *connecting* as the role — which NOLOGIN
forbids. Split by kind: the migration owns **privileges** (role, `USAGE`, `SELECT`, default
privileges) because those are versioned schema state with no secret in them, and
`app/core/ro_role.py` owns the **password**, applied idempotently after `alembic upgrade head`
in both Dockerfile stages. Rotation is an env edit and a restart rather than a new revision,
and no credential enters git. It is a no-op when the variable is unset and exits 0 when it
fails, so it can never gate uvicorn.

**Schema placement is on `Base.metadata`, not per-model `__table_args__`.** The brief asked for
the latter; every gex model already defines its own `__table_args__` tuple, so that form would
have meant seven edits and an eighth thing for a new table to forget — the exact failure the
requirement exists to prevent. `MetaData(schema=SCHEMA_GEX)` is one line, inherited, and
unforgettable.

**`alembic_version` stays in `public`, so the "`\dt public.*` returns nothing" criterion is met
in spirit rather than letter.** Moving it is unsafe in a way that is not obvious: with
`version_table_schema` pointed at `gex`, any database whose version table is still in `public`
looks like a database at base, and Alembic re-runs the entire chain against tables that already
exist. There is no ordering of the move and the setting that is safe both on a fresh database
and on an existing one. `public` therefore holds exactly one table and it belongs to Alembic,
not to a module — no module has a privileged namespace, which is what the criterion was for.

### The one that would have bitten later

The brief's list of likely first-contact failures named `search_path`, but for the wrong
reason: it expected unqualified raw SQL in the GEX codebase to break. There is none (the only
`text()` in the app is `/health`'s `SELECT 1`). The real problem was that **this project's
database role is called `gex`, and T76 created a schema called `gex`** — so the default
`"$user", public` search path silently made `gex` the default schema. Reflection then labelled
the moved tables as living in the default schema while `Base.metadata` labelled them `"gex"`,
and `--autogenerate` emitted a migration recreating all seven tables. Fixed by pinning
`options=-csearch_path=public` as a **connect argument** on both the app engine and Alembic's:
SQLAlchemy resolves `default_schema_name` once at first connect and caches it for the engine's
life, so issuing `SET search_path` after connecting fixes query resolution and leaves
autogenerate just as wrong — that was tried first, and the migration it generated still
recreated every table.

**The one the test suite could not see.** `app/workers/gex_capture.py` waits for a sentinel
table before scheduling anything, via `inspect(engine).has_table("snapshots")` — unqualified,
so after the move it looked in `public`, found nothing, and sat out its full 60-second timeout
against a perfectly migrated database before starting anyway. Every test of that function
injects `wait_for_schema=False` or a fake inspector, because what they exist to check is the
*waiting*, so all 1,003 passed while the real worker was broken. Found by running the stack and
reading `docker compose logs`, which is the second time in two tasks that a container-only
failure got past a green suite (T75's was the `app.models` alias). The fake inspector now
asserts `schema == "gex"`.

**This is the shape of the risk T77 and T79 inherit**: anything that names a table outside the
ORM — `has_table`, `information_schema` queries, `text()` SQL, a `pg_dump` argument — needs the
schema passed explicitly now, and the unit suite will not tell you.

Two smaller ones, both live-observed: `ALTER ROLE ... PASSWORD` takes no bind parameters, so
the statement is built server-side with `format('%I %L', ...)` — whose arguments need explicit
`CAST(:p AS text)`, because `format` is variadic and Postgres otherwise fails with "could not
determine data type of parameter $1", and because `:p::text` stops SQLAlchemy's `text()` parser
recognising the bind at all.

### Follow-up found, not fixed

`alembic heads` and `alembic history` fail from the CLI with `ModuleNotFoundError: No module
named 'app.models'`. T75's alias for the two frozen revisions lives in `env.py`, which those
two commands never run. `upgrade`, `downgrade`, `current` and `revision` all run `env.py` and
are unaffected, so nothing in the container or in CI is broken — it is a developer-facing
wart. Logged rather than fixed silently; see T83.
