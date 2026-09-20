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
