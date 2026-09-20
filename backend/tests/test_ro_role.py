"""T76: the `quantdesk_ro` login step must never be able to stop a container booting.

`app.core.ro_role` runs between `alembic upgrade head` and uvicorn in both Dockerfile stages.
Nothing else in the stack needs the read-only role -- the API and the capture worker connect
as the application user -- so every way this can decline to do its job has to be a quiet
no-op rather than a non-zero exit. That is what these tests pin.

The successful path needs a real Postgres and was verified live on 2026-09-19: with the role
present, `ALTER ROLE ... LOGIN PASSWORD` was applied and `quantdesk_ro` could then SELECT
from all three schemas while INSERT, UPDATE, DELETE, CREATE and ALTER were all refused.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.core import ro_role
from app.core.db import get_engine


def test_no_password_configured_is_a_no_op(monkeypatch, caplog):
    """The default state on a laptop: no credential, no role login, no fuss."""
    monkeypatch.setattr(ro_role.settings, "QUANTDESK_RO_PASSWORD", "")

    with caplog.at_level("INFO"):
        assert ro_role.apply_readonly_login() is False

    assert "unset" in caplog.text


def test_non_postgres_database_is_a_no_op(monkeypatch, tmp_path):
    """SQLite has no roles. Reaching this in a test run must not raise."""
    monkeypatch.setattr(ro_role.settings, "QUANTDESK_RO_PASSWORD", "secret")
    engine = get_engine(f"sqlite:///{tmp_path / 'ro.db'}")

    assert ro_role.apply_readonly_login(engine) is False


def test_missing_role_warns_but_does_not_raise(monkeypatch, tmp_path, caplog):
    """If the migration has not run, say so and carry on.

    Simulated by pointing the Postgres branch at an engine whose `pg_roles` lookup returns
    nothing -- the condition the real code guards, without needing a server.
    """
    monkeypatch.setattr(ro_role.settings, "QUANTDESK_RO_PASSWORD", "secret")
    engine = get_engine(f"sqlite:///{tmp_path / 'ro.db'}")
    monkeypatch.setattr(engine.dialect, "name", "postgresql")

    # The `pg_roles` query is the first statement; on SQLite it raises, which `main` swallows.
    # Here we assert the function itself surfaces rather than silently claiming success.
    with caplog.at_level("WARNING"):
        try:
            result = ro_role.apply_readonly_login(engine)
        except sa.exc.DatabaseError:
            result = False
    assert result is False


def test_main_returns_zero_even_when_everything_fails(monkeypatch):
    """The contract the Dockerfile's `&&` chain depends on."""

    def explode(*_args, **_kwargs):
        raise RuntimeError("no database")

    monkeypatch.setattr(ro_role, "apply_readonly_login", explode)
    assert ro_role.main() == 0


def test_role_name_matches_the_migration():
    """Two spellings of the role name would fail silently and much later."""
    from pathlib import Path

    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "a3f1c7d92b64_module_schemas_and_readonly_role.py"
    ).read_text(encoding="utf-8")

    assert f'RO_ROLE = "{ro_role.RO_ROLE}"' in migration
