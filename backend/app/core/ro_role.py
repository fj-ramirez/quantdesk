"""Turn on `quantdesk_ro`'s login from the environment (T76).

**Why this is not in the migration.** T76's brief asks for both `CREATE ROLE quantdesk_ro
NOLOGIN` in the Alembic chain *and* a `QUANTDESK_RO_PASSWORD` variable in compose -- and its
acceptance criterion is that you can *connect* as the role, which NOLOGIN forbids. The two
halves are different kinds of thing and that is the resolution used here:

* **Privileges are schema state.** `quantdesk_ro` exists, has `USAGE` on three schemas,
  `SELECT` on their tables and a matching `ALTER DEFAULT PRIVILEGES`. All of that is
  versioned, reviewable and identical on every host, so it lives in revision `a3f1c7d92b64`.
* **The password is a secret.** It differs per host, must be rotatable without a new
  revision, and must never be committed. So it comes from `QUANTDESK_RO_PASSWORD` and is
  applied here, idempotently, on every container start -- rotation is an env edit and a
  restart.

Run after `alembic upgrade head` (see both `CMD`s in `backend/Dockerfile`), or by hand:

    uv run python -m app.core.ro_role

A no-op when the variable is unset, so a dev stack that never asked for a read-only
connection is not blocked by a missing credential -- the same rule `MARKETDATA_TOKEN` and
`TIINGO_TOKEN` already follow. It is also a no-op, with a warning rather than a failure, when
the role does not exist: a boot must not be gated on this.
"""

from __future__ import annotations

import logging
import sys

import sqlalchemy as sa

from app.core.config import settings
from app.core.db import get_engine

logger = logging.getLogger(__name__)

__all__ = ["RO_ROLE", "apply_readonly_login", "main"]

#: Must match `RO_ROLE` in revision `a3f1c7d92b64`, which creates it.
RO_ROLE = "quantdesk_ro"


def apply_readonly_login(engine: sa.Engine | None = None) -> bool:
    """Give `quantdesk_ro` LOGIN and the configured password. True if it was applied.

    Idempotent: `ALTER ROLE` sets the password to whatever the environment currently says on
    every call, which is what makes rotation work and what makes running this on every boot
    harmless.
    """
    password = settings.QUANTDESK_RO_PASSWORD
    if not password:
        logger.info("QUANTDESK_RO_PASSWORD is unset; leaving %s without login", RO_ROLE)
        return False

    engine = engine or get_engine()
    if engine.dialect.name != "postgresql":
        logger.info("not a Postgres database; skipping %s login", RO_ROLE)
        return False

    with engine.begin() as conn:
        exists = conn.scalar(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": RO_ROLE}
        )
        if not exists:
            logger.warning(
                "role %s does not exist -- has `alembic upgrade head` run? skipping", RO_ROLE
            )
            return False

        # Postgres does its own quoting. `ALTER ROLE ... PASSWORD` takes no bind parameters,
        # and hand-rolling the escaping for a value that arrives from the environment is
        # exactly the wrong place to be clever: `format`'s %I/%L do it correctly, server-side,
        # for any password including one full of quotes.
        statement = conn.scalar(
            # The casts are load-bearing: `format` is variadic, so without them Postgres
            # cannot infer the parameter types and fails with "could not determine data type
            # of parameter $1". Spelled `CAST(... AS text)` rather than `:r::text`, because
            # `::` immediately after a bind parameter confuses SQLAlchemy's own `text()`
            # parser and the parameter stops being substituted at all.
            sa.text(
                "SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', "
                "CAST(:r AS text), CAST(:p AS text))"
            ),
            {"r": RO_ROLE, "p": password},
        )
        conn.execute(sa.text(statement))

    logger.info("%s can now log in", RO_ROLE)
    return True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        apply_readonly_login()
    except Exception:
        # Never gate a boot on this. The API and the capture worker connect as the application
        # user and do not care whether the read-only role is usable; only the MCP connector
        # does, and it fails loudly on its own when it cannot connect.
        logger.exception("could not configure the %s login; continuing", RO_ROLE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
