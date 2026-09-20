"""Tests for `app/modules/gex/jobs/bars.py`. Offline: a stub provider/registry stands in for Yahoo, a
temp-file SQLite `session_factory` stands in for Postgres -- same isolation pattern as
`test_capture.py`.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.jobs.bars import update_bars_job, update_one_symbol
from app.modules.gex.models.bars import DailyBar
from app.modules.gex.models.db import Base
from app.modules.gex.providers.bars import UpstreamUnavailable


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


class _StubProvider:
    """Minimal `BarProvider` stand-in: returns one bar for every symbol except those in
    `fail_for`, which raise `UpstreamUnavailable` instead."""

    def __init__(self, *, fail_for: set[str] = frozenset()):
        self.fail_for = fail_for
        self.calls: list[str] = []

    async def fetch_daily_bars(self, symbol, *, start, end=None):
        self.calls.append(symbol)
        if symbol in self.fail_for:
            raise UpstreamUnavailable(f"stub: {symbol} is down")
        return [
            DailyBar(
                symbol=symbol,
                date=dt.date(2026, 9, 4),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000,
                source="stub",
            )
        ]

    async def close(self) -> None:
        pass


class _StubRegistry:
    """Duck-typed `BarProviderRegistry` stand-in -- one shared `_StubProvider` for every
    symbol, so `fail_for` applies uniformly regardless of routing."""

    def __init__(self, provider: _StubProvider):
        self._provider = provider
        self.closed = False

    def for_symbol(self, symbol: str):
        return self._provider

    async def aclose(self) -> None:
        self.closed = True


async def test_update_one_symbol_success_reports_inserted_count(session_factory):
    provider = _StubProvider()
    registry = _StubRegistry(provider)

    result = await update_one_symbol("SPY", registry=registry, session_factory=session_factory)

    assert result.ok is True
    assert result.inserted == 1
    assert result.error is None


async def test_update_one_symbol_provider_error_never_raises(session_factory):
    provider = _StubProvider(fail_for={"SPY"})
    registry = _StubRegistry(provider)

    result = await update_one_symbol("SPY", registry=registry, session_factory=session_factory)

    assert result.ok is False
    assert "SPY" in result.error


async def test_update_one_symbol_survives_a_non_provider_error(session_factory, caplog):
    class _BuggyProvider:
        async def fetch_daily_bars(self, symbol, *, start, end=None):
            raise RuntimeError("a bug, not a ProviderError")

        async def close(self):
            pass

    registry = _StubRegistry(_BuggyProvider())
    with caplog.at_level(logging.ERROR, logger="app.modules.gex.jobs.bars"):
        result = await update_one_symbol(
            "SPY", registry=registry, session_factory=session_factory
        )

    assert result.ok is False
    assert "RuntimeError" in result.error


async def test_update_bars_job_one_symbol_failing_does_not_stop_the_others(
    session_factory, caplog
):
    """The T42 acceptance check: a stub provider that raises on one symbol still updates every
    other symbol, and logs the failure."""
    provider = _StubProvider(fail_for={"QQQ"})
    registry = _StubRegistry(provider)

    with caplog.at_level(logging.ERROR, logger="app.modules.gex.jobs.bars"):
        results = await update_bars_job(
            ["SPY", "QQQ", "DIA"], registry=registry, session_factory=session_factory
        )

    by_symbol = {r.symbol: r for r in results}
    assert by_symbol["SPY"].ok is True
    assert by_symbol["QQQ"].ok is False
    assert by_symbol["DIA"].ok is True
    # Every symbol was still attempted, including the two after the alphabetically-earlier
    # failure would have short-circuited a naive implementation.
    assert provider.calls == ["SPY", "QQQ", "DIA"]
    assert any("QQQ" in r.message for r in caplog.records)


async def test_update_bars_job_closes_a_registry_it_owns(session_factory):
    """A caller-supplied registry (as every test above uses) is left open for the caller to
    close; a default, job-constructed one must be closed automatically. This test only checks
    the "owns it" half via the stub, since exercising the real default registry would require
    network-capable providers."""
    provider = _StubProvider()
    registry = _StubRegistry(provider)

    await update_bars_job(["SPY"], registry=registry, session_factory=session_factory)

    assert registry.closed is False  # caller-supplied: not this job's to close


async def test_update_bars_job_defaults_to_scan_universe(session_factory, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "SCAN_UNIVERSE", "SPY,QQQ")
    provider = _StubProvider()
    registry = _StubRegistry(provider)

    results = await update_bars_job(registry=registry, session_factory=session_factory)

    assert {r.symbol for r in results} == {"SPY", "QQQ"}
