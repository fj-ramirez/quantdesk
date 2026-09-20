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
from app.core.schemas import SCHEMAS
from app.modules.gex.models.db import Base
from app.modules.research.models.db import Base as ResearchBase
from app.modules.terminal.tables import Base as TerminalBase

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
#
# --- T77: one chain, one `alembic upgrade head`, several modules ---------------------------
#
# Each module owns a `Base` with its own `MetaData`, because each owns a schema (invariant 8).
# Autogenerate diffs against a single `MetaData`, so they are combined here rather than in the
# models -- which keeps the modules independent of each other (nothing in `research` imports
# `gex`) while still giving Alembic one view of the whole database.
#
# A **list**, which is Alembic's supported form for exactly this. Copying the tables into one
# throwaway `MetaData` was tried first and is wrong: `to_metadata` preserves each table's
# schema but re-resolves its string foreign keys against the new metadata's default schema, so
# gex's `ForeignKey("snapshots.id")` went looking for `public.snapshots` and autogenerate died
# with `NoReferencedTableError`.
#
# Separate bases rather than one shared base, because the modules must stay independent:
# nothing in `research` imports `gex`, and a test calling `create_all` for one must not create
# the other's tables.
target_metadata = [Base.metadata, ResearchBase.metadata, TerminalBase.metadata]


# --- T76: one chain, three schemas ---------------------------------------------------------
#
# `include_schemas=True` is required now that the models carry a schema. Without it,
# autogenerate compares metadata that says `gex.snapshots` against a database it only
# inspects in the default search path, concludes every table is missing, and writes a
# migration that creates them all again -- or, with the tables found where it did not expect
# them, drops them. Any generated migration still gets read line by line before it is kept.
#
# `include_name` is the other half, and it is not optional. `include_schemas=True` on its own
# reflects *every* non-system schema in the database; anything found in one this application
# does not own would be diffed against metadata that has never heard of it, and autogenerate
# would propose dropping it. Restricting reflection to `SCHEMAS` means the diff covers exactly
# the three namespaces this app owns and nothing else.
#
# **`alembic_version` stays in `public`, deliberately.** Alembic's default -- no
# `version_table_schema` is set below -- and the alternative is worse than it looks. Pointing
# `version_table_schema` at `gex` would mean that, on any database whose version table is
# still in `public`, Alembic looks for `gex.alembic_version`, does not find it, concludes the
# database is at base, and re-runs the entire chain against tables that already exist. There
# is no ordering of "move the table" and "change the setting" that is safe in both directions
# on both a fresh and an existing database.
#
# So `public` holds exactly one table, and it belongs to Alembic rather than to any module.
# That is the letter of "public stays empty" bent and its intent kept: no *module* has a
# privileged namespace, and the read-only role's grants have the same shape for all three.
def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    """Confine autogenerate's reflection to the schemas this application owns."""
    if type_ == "schema":
        return name in SCHEMAS
    return True

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
        include_schemas=True,
        include_name=include_name,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # `-csearch_path=public` has to be a *connection* parameter, not a `SET` issued after
    # connecting. SQLAlchemy resolves `default_schema_name` once, during the dialect's
    # first-connect initialisation, and caches it for the engine's life -- a later `SET
    # search_path` changes how queries resolve but leaves the inspector still reporting the
    # old default, so autogenerate goes on comparing against the wrong schema. (Tried in that
    # order; the migration it generated still recreated all seven tables.)
    section = dict(config.get_section(config.config_ini_section, {}))
    connect_args = (
        {"options": "-csearch_path=public"}
        if section.get("sqlalchemy.url", "").startswith("postgresql")
        else {}
    )
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    with connectable.connect() as connection:
        # --- T76: pin the search path, or autogenerate lies -------------------------------
        #
        # Measured, not theorised. The application's Postgres role is called `gex` and the
        # default `search_path` is `"$user", public` -- so the moment T76 created a schema
        # *also* called `gex`, `$user` started resolving to it and SQLAlchemy began reporting
        # `default_schema_name == "gex"`. Reflection then labels the gex tables as living in
        # the default schema (`None`) while `Base.metadata` labels them `"gex"`, the two never
        # match, and `--autogenerate` emits a migration that creates all seven tables again.
        # That was the observed output before this line existed.
        #
        # Pinning the path makes the default schema `public` again, which is what the metadata
        # is diffed against and what every pre-T76 revision assumed when it created tables
        # unqualified. It also removes the coincidence entirely: the behaviour no longer
        # depends on whether the database user happens to share a name with a schema, which is
        # the kind of thing that works on one host and not the next.
        #
        # Alembic's own `alembic_version` lookup is unaffected -- it resolves unqualified and
        # the table is in `public`, which is still on the path.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
