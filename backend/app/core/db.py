"""Engine and session plumbing shared by every module (T75).

One of exactly three things in `app/core/`: settings, this, and the schema-name constants.
The initiative README is explicit that a shared `core/` is deliberately *not* being designed
yet -- bars, symbols, calendars and provider ABCs stay inside their module until there are
three real consumers to design against -- so this file holds the irreducible minimum and
nothing more.

Why these three functions and not more. They are the only database code with no opinion about
what is in the database: a URL becomes an `Engine`, an `Engine` becomes a `sessionmaker`, and
the process caches one of the latter. None of them names a table. That is what lets this
module import nothing from `app.modules.*` -- the layering points one way, and a module that
wants a session asks `core` rather than reaching into another module's `models` package.

`Base`, `UTCDateTime` and the gex tables stayed in `app/modules/gex/models/db.py`. Hoisting
`UTCDateTime` here is the obvious next move -- invariant 4 (`captured_at` is tz-aware UTC,
enforced at the DB boundary) is codebase-wide, not gex-specific -- but doing it now would be
guessing at consumers that do not exist yet. T76 owns the database and will have a real
reason.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.schemas import SCHEMAS

__all__ = ["connect_args_for", "get_engine", "get_session_factory", "get_sessionmaker"]


def connect_args_for(url: str) -> dict[str, object]:
    """DBAPI connect arguments for `url`. Its own function so it can be asserted on.

    Both entries are Postgres-only; SQLite accepts neither and `create_engine` would fail
    outright rather than ignore them.

    `connect_timeout` is T35 -- see `get_engine`.

    `options` is T76, and it pins the search path rather than inheriting Postgres's default
    `"$user", public`. This project's database role is called `gex`, and T76 created a
    *schema* called `gex`, so `$user` started resolving to it. The application itself does not
    care (the ORM emits fully qualified names, because the metadata carries the schema), but
    the coincidence is the kind that quietly becomes load-bearing: unqualified raw SQL would
    find the module's tables on this host and find nothing on a host whose database user is
    named anything else. `alembic/env.py` sets the same option for a sharper reason -- there
    it is the difference between autogenerate producing an empty diff and producing a
    migration that recreates all seven tables.
    """
    if not url.startswith("postgresql"):
        return {}
    return {"connect_timeout": 5, "options": "-csearch_path=public"}


def get_engine(database_url: str | None = None) -> Engine:
    """Create an engine for `database_url`, defaulting to `settings.DATABASE_URL`.

    Never called at import time -- Alembic's ``env.py`` and tests each need a different URL,
    and constructing an engine has side effects (connection pool setup) that don't belong at
    module import.

    T35: psycopg's default connect timeout is "however long the OS takes to give up on the
    TCP handshake" -- unbounded from this app's point of view, and the actual mechanism behind
    the startup hang the moment any caller runs a connection attempt synchronously (as
    `app/modules/gex/jobs/catchup.py`'s `has_eod_snapshot_today` used to). Moving that call to
    a worker thread (see `catch_up_missed_eod`) already keeps a slow connect off the event
    loop, but a short, explicit `connect_timeout` still bounds how long that thread -- and,
    for the catch-up's own logging, how long the user waits to see it give up -- is on the
    hook for. Only applied to Postgres URLs: SQLite (every test's `session_factory`) has no
    such keyword and would fail `create_engine` outright.

    T76: **the schema translation below is what lets the test suite stay on SQLite.** The
    models now carry a schema (`gex.snapshots`, not `snapshots` -- see
    `app.modules.gex.models.db.Base`), and SQLite has no concept of one: a plain
    `Base.metadata.create_all` against it would emit `CREATE TABLE gex.snapshots` and fail
    with "unknown database gex". `schema_translate_map` is SQLAlchemy's designed answer --
    the schema is a symbolic name resolved per connection, applying to DDL and queries alike,
    so 28 test files that each build their own SQLite engine kept working with no edit at all
    and go on exercising the same model code production runs.

    Mapping every schema in `SCHEMAS`, not just `gex`, so research (T77) and terminal (T79)
    inherit this for free rather than each rediscovering it.

    The map is applied only off Postgres. On the real database the schemas exist and the
    qualified names are the point; translating there would silently undo the migration.
    """
    url = database_url or settings.DATABASE_URL
    is_postgres = url.startswith("postgresql")
    engine = create_engine(url, connect_args=connect_args_for(url))
    if is_postgres:
        return engine
    return engine.execution_options(schema_translate_map=dict.fromkeys(SCHEMAS))


def get_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    """Session factory bound to `engine`.

    ``expire_on_commit=False`` so a `Snapshot` returned by `SnapshotRepository.add` stays
    readable (e.g. for logging its `id`) after the commit that persisted it, without an extra
    round trip.
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


# One engine (and its connection pool) for the process's lifetime, shared by every capture
# call and by the read API -- see `get_engine` above for why it is deliberately not called at
# import time. Creating a fresh engine per capture would open (and never close) a new
# connection pool every time the EOD job runs.
#
# T75 moved this out of `app/jobs/capture.py`. It was never capture-specific: `main.py`'s
# `/health` probe already imported it from there, which is how a route ended up depending on a
# job module. Now both the API process and the `gex-capture` worker ask `core` for it, and
# each gets one factory for its own process -- which is exactly right, since they no longer
# share one.
_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    """Cached, lazily-created sessionmaker bound to `settings.DATABASE_URL`.

    Tests should not use this -- pass an explicit `session_factory` (built on a temp SQLite
    URL, per the pattern in `tests/test_snapshot_repository.py`) to `capture_snapshot`
    instead, so the test suite never touches the real database this points at.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = get_sessionmaker(get_engine())
    return _session_factory
