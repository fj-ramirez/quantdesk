import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# app.core.config.settings is the single source of truth for the database URL (it reads
# DATABASE_URL from the environment / .env, same as the running app). alembic.ini
# deliberately leaves sqlalchemy.url unset -- we inject it here instead of duplicating
# the value (or a stale copy of it) in two places.
#
# T75 moved the models from `app.models.db` to `app.modules.gex.models.db`. `Base.metadata` is
# still what autogenerate diffs against and must still be imported here, or every
# `--autogenerate` run produces a migration that drops every table.
import app
from alembic import context
from app.core.config import settings
from app.modules.gex.models.db import Base

# --- T75: keep the frozen revision scripts importable -------------------------------------
#
# Two revisions in `versions/` do `import app.models.db` and reference
# `app.models.db.UTCDateTime` in their column definitions. They were written when the backend
# was a flat `app/` package. `app.models` no longer exists, so without the aliases below
# `alembic upgrade head` dies with an ImportError at container boot, the backend healthcheck's
# 60-second `start_period` expires, and a perfectly good deploy reports itself broken.
#
# Aliases rather than an edit to those scripts, for two reasons. First, T75's brief forbids
# touching `alembic/versions/**` at all -- T76 owns the database, and a revision file is the
# last place a restructuring task should leave fingerprints. Second, and more durably: a
# migration is a historical record of what was applied to a real database. Refactoring one
# rewrites history to match code that did not exist when it ran, and the next rename would
# demand the identical edit again.
#
# `sys.modules` (not a shim package on disk) so nothing in the application can reach the old
# name by accident: the binding exists only in a process that has imported this env, and
# `app.models.db` *is* `app.modules.gex.models.db` -- the same module object, so `UTCDateTime`
# is the same class rather than a second copy of it.
#
# `setdefault`, not assignment, so a real `app.models` would win if one ever existed again.
# Read out of `sys.modules` rather than imported under an alias: the `from ... import Base`
# above has already put both the package and the leaf module there, and two more import
# statements would only give isort somewhere to move them away from this comment.
#
# **The `setattr` is not redundant, and leaving it out is a live bug** -- observed, not
# theorised: `alembic upgrade head` failed in the backend container with `AttributeError:
# module 'app' has no attribute 'models'`. `import app.models.db` is satisfied by the
# `sys.modules` entry alone, so the import statement succeeds, but the *attribute* binding
# that a real import would leave on the parent package never happens. The revisions then use
# the dotted form (`app.models.db.UTCDateTime(...)`), which walks attributes rather than
# sys.modules, and dies. `tests/test_alembic_upgrade.py` exists so this cannot regress
# silently again -- the unit suite never runs a migration, which is why it passed while a
# container boot did not.
sys.modules.setdefault("app.models", sys.modules["app.modules.gex.models"])
sys.modules.setdefault("app.models.db", sys.modules["app.modules.gex.models.db"])
if not hasattr(app, "models"):
    app.models = sys.modules["app.models"]
# -------------------------------------------------------------------------------------------

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# The `configure_logger` guard is Alembic's own idiom for programmatic invocation, and it is
# here because `fileConfig` defaults to `disable_existing_loggers=True`: it silences every
# logger that already exists in the process. From the CLI that is harmless (nothing else is
# running). From inside pytest it is not -- `tests/test_alembic_upgrade.py` running an upgrade
# disabled the application's loggers for the rest of the session, and twenty-five later tests
# that assert on log output failed, in a full run only, while each passed on its own. The
# attribute is absent for the CLI, so `alembic upgrade head` in the container behaves exactly
# as before.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
