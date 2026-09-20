"""Postgres schema names, one per module (T75; **T76 is what makes them real**).

One database, three schemas -- see `plans/quantdesk/README.md` for why this rather than
`gex__snapshots` table-name prefixes or three separate databases. The short version: per-module
grants are one `GRANT USAGE` statement each instead of a naming convention nothing enforces,
`pg_dump -n research` backs up one module, and cross-module joins stay ordinary joins rather
than going through `postgres_fdw`.

**Nothing imports these yet, and that is the intended state at the end of T75.** This task is
a pure restructuring with no schema change: `backend/alembic/versions/**` is untouched and
every table is still where it was. T76 owns moving the gex tables into `gex.` and creating the
read-only role that gets `USAGE` on all three. The constants exist now so that T76, T77 and
T79 all spell the names the same way, and so the decision is recorded in code rather than only
in a plan file.
"""

from __future__ import annotations

__all__ = ["SCHEMAS", "SCHEMA_GEX", "SCHEMA_RESEARCH", "SCHEMA_TERMINAL"]

#: The shipped GEX app: snapshots index, computed levels, per-strike series, bars, flows,
#: decisions.
SCHEMA_GEX = "gex"

#: EdgeLab (T77): the edge-search trial registry and paper candidates.
SCHEMA_RESEARCH = "research"

#: xactx (T79): series metadata, observations, releases, ingest batches, edge definitions.
SCHEMA_TERMINAL = "terminal"

#: Every schema this application owns, in dependency-graph order. The MCP connector's
#: read-only role (T82) grants `USAGE` across exactly this tuple.
SCHEMAS: tuple[str, ...] = (SCHEMA_GEX, SCHEMA_RESEARCH, SCHEMA_TERMINAL)
