"""`alembic upgrade head` must actually run (T75).

**Why this file exists.** T75 moved the models from `app.models.db` to
`app.modules.gex.models.db`, and two frozen revisions in `alembic/versions/` still import the
old path. `alembic/env.py` aliases it (see its own comment for why the revision scripts are
not rewritten). The first version of that alias set `sys.modules` and nothing else, which
satisfies `import app.models.db` but leaves no `models` attribute on the `app` package -- so
the revisions' `app.models.db.UTCDateTime(...)` died with `AttributeError: module 'app' has no
attribute 'models'`.

The entire unit suite passed while that was broken, because nothing in it runs a migration.
The failure surfaced only when the backend container booted, where `alembic upgrade head`
gates uvicorn: the container exited 1, and on the homeserver it would have looked like a
60-second healthcheck `start_period` expiring on a deploy that was in fact dead. That is too
expensive a feedback loop for something a test can hold.

SQLite, not Postgres, so this stays offline like everything else here. What is being checked
is that the revision scripts *import and execute*, which is engine-independent.

**Upgrades to `LAST_SQLITE_REVISION`, not to `head`, and that limit is pre-existing.** The
final revision (`c7a1e93b5d02`, T71's `(underlying, captured_at)` uniqueness) issues
`ALTER TABLE ... ADD CONSTRAINT`, which SQLite has no support for -- alembic raises
`NotImplementedError` and tells you to use batch mode. Rewriting that revision to run on
SQLite would be editing `alembic/versions/**`, which T75 must not do. The five revisions this
does run include **both** of the ones that import `app.models.db`, which is the whole point;
the remaining one is covered by the real container boot, where `alembic upgrade head` gates
uvicorn against a real Postgres.
"""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parent.parent

#: The last revision that runs on SQLite -- see the module docstring. `b2d4f6a8c0e1` is the
#: decisions table; the one after it is Postgres-only.
LAST_SQLITE_REVISION = "b2d4f6a8c0e1"


def _alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    # `env.py` reads `settings.DATABASE_URL` and injects it, overriding whatever is set here,
    # so the URL is passed through the environment instead -- see the monkeypatch in each test.
    config.set_main_option("sqlalchemy.url", database_url)
    # Do not let `env.py` call `fileConfig`. It defaults to `disable_existing_loggers=True`,
    # which silences every logger already created in the pytest process -- observed cost: 25
    # unrelated tests failing in a full run and passing individually. See the guard in
    # `alembic/env.py`. The CLI path (no attribute -> logging configured) is unaffected, and
    # is what the container exercises.
    config.attributes["configure_logger"] = False
    return config


def test_alembic_upgrade_runs_every_revision_that_imports_the_moved_models(tmp_path, monkeypatch):
    """The container's boot command, as far as SQLite can follow it."""
    db_path = tmp_path / "upgrade.db"
    url = f"sqlite:///{db_path}"

    # `env.py` takes the URL from `settings`, which is exactly the coupling the container
    # relies on (`alembic.ini` deliberately leaves `sqlalchemy.url` unset). Patch the setting
    # rather than the ini so this test exercises that path rather than routing around it.
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", url)

    command.upgrade(_alembic_config(url), LAST_SQLITE_REVISION)

    tables = set(inspect(create_engine(url)).get_table_names())
    # The two revisions that import `app.models.db` are the ones that create these.
    assert "snapshots" in tables
    assert "gex_levels" in tables
    assert "gex_by_strike" in tables
    # ...and the rest of the chain ran too, rather than stopping at the first failure.
    assert "daily_bars" in tables
    assert "decisions" in tables
    assert "alembic_version" in tables


def test_the_frozen_revisions_old_model_import_still_resolves(tmp_path, monkeypatch):
    """The specific regression, named.

    `import app.models.db` must work *and* `app.models.db.UTCDateTime` must be reachable by
    attribute, because that is the form the revision scripts use. Asserting on both halves
    separately means a future change that fixes one and breaks the other says which.
    """
    url = f"sqlite:///{tmp_path / 'imports.db'}"
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATABASE_URL", url)

    command.upgrade(_alembic_config(url), LAST_SQLITE_REVISION)

    import app
    import app.models.db
    from app.modules.gex.models.db import UTCDateTime

    # The same class, not a second copy of it: a duplicate would compare unequal and would
    # mean the migrations and the models disagreed about the column type.
    assert app.models.db.UTCDateTime is UTCDateTime
