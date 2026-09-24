"""Shared pytest fixtures.

T124: `app.modules.gex.api.scan` caches the universe trend pass in process. Tests swap the
database under that module per test (a fresh SQLite factory, a patched `SCAN_UNIVERSE`), so the
cache is dropped before every test rather than trusted to notice the swap.
"""

import pytest

from app.modules.gex.api.scan import clear_universe_cache


@pytest.fixture(autouse=True)
def _fresh_universe_cache():
    clear_universe_cache()
    yield
    clear_universe_cache()
