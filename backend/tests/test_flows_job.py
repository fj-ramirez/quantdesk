"""Tests for `app/modules/gex/jobs/flows.py` (T52). Offline: stub providers stand in for
SPDR/iShares, a temp-file SQLite `session_factory` stands in for Postgres -- same isolation
pattern as `test_bars_job.py`.
"""

from __future__ import annotations

import datetime as dt
import logging

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.jobs.flows import update_flows_job
from app.modules.gex.models.db import Base
from app.modules.gex.providers.etf_flows import (
    ProviderError,
    SharesOutstandingFetchResult,
    SharesOutstandingRow,
)


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


class _StubProvider:
    """Minimal `SharesOutstandingProvider` stand-in for one family."""

    def __init__(self, name: str, symbols: tuple[str, ...], *, rows=None, failures=None, raises=None):
        self._name = name
        self._symbols = symbols
        self._rows = rows if rows is not None else []
        self._failures = failures if failures is not None else {}
        self._raises = raises
        self.fetch_calls: list = []
        self.closed = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    async def fetch(self, symbols=None):
        self.fetch_calls.append(symbols)
        if self._raises is not None:
            raise self._raises
        rows = [r for r in self._rows if symbols is None or r.symbol in symbols]
        failures = (
            self._failures
            if symbols is None
            else {k: v for k, v in self._failures.items() if k in symbols}
        )
        return SharesOutstandingFetchResult(rows=rows, failures=failures)

    async def close(self) -> None:
        self.closed = True


def _row(symbol, as_of=dt.date(2026, 9, 8)) -> SharesOutstandingRow:
    return SharesOutstandingRow(
        symbol=symbol, as_of_date=as_of, shares_outstanding=1_000_000, nav=10.0, source="stub"
    )


async def test_update_flows_job_inserts_rows_from_a_successful_family(session_factory):
    provider = _StubProvider("spdr", ("XLK", "SPY"), rows=[_row("XLK"), _row("SPY")])

    results = await update_flows_job(providers=[provider], session_factory=session_factory)

    assert len(results) == 1
    assert results[0].family == "spdr"
    assert results[0].ok is True
    assert results[0].inserted == 2


async def test_update_flows_job_provider_error_is_a_named_family_failure_not_a_crash(
    session_factory, caplog
):
    provider = _StubProvider("spdr", ("XLK",), raises=ProviderError("upstream down"))

    with caplog.at_level(logging.ERROR, logger="app.modules.gex.jobs.flows"):
        results = await update_flows_job(providers=[provider], session_factory=session_factory)

    assert results[0].ok is False
    assert "upstream down" in results[0].error


async def test_update_flows_job_survives_a_non_provider_error(session_factory, caplog):
    provider = _StubProvider("spdr", ("XLK",), raises=RuntimeError("a bug, not a ProviderError"))

    with caplog.at_level(logging.ERROR, logger="app.modules.gex.jobs.flows"):
        results = await update_flows_job(providers=[provider], session_factory=session_factory)

    assert results[0].ok is False
    assert "RuntimeError" in results[0].error


async def test_update_flows_job_one_family_failing_does_not_stop_another(session_factory):
    failing = _StubProvider("spdr", ("XLK",), raises=ProviderError("down"))
    healthy = _StubProvider("ishares", ("IWM",), rows=[_row("IWM")])

    results = await update_flows_job(providers=[failing, healthy], session_factory=session_factory)

    by_family = {r.family: r for r in results}
    assert by_family["spdr"].ok is False
    assert by_family["ishares"].ok is True
    assert by_family["ishares"].inserted == 1


async def test_update_flows_job_per_symbol_failures_are_named_not_a_crash(session_factory, caplog):
    provider = _StubProvider(
        "ishares", ("IWM", "TLT"), rows=[_row("TLT")], failures={"IWM": "moved page"}
    )

    with caplog.at_level(logging.ERROR, logger="app.modules.gex.jobs.flows"):
        results = await update_flows_job(providers=[provider], session_factory=session_factory)

    assert results[0].ok is True
    assert results[0].inserted == 1
    assert results[0].failed_symbols == ["IWM"]
    assert any("IWM" in r.message for r in caplog.records)


async def test_update_flows_job_restricts_to_requested_symbols_per_family(session_factory):
    spdr = _StubProvider("spdr", ("XLK", "SPY"), rows=[_row("XLK"), _row("SPY")])
    ishares = _StubProvider("ishares", ("IWM",), rows=[_row("IWM")])

    results = await update_flows_job(
        ["XLK"], providers=[spdr, ishares], session_factory=session_factory
    )

    # ishares has nothing requested of it -- skipped entirely, not reported as a zero-row
    # success.
    assert len(results) == 1
    assert results[0].family == "spdr"
    assert spdr.fetch_calls == [["XLK"]]
    assert ishares.fetch_calls == []


async def test_update_flows_job_second_run_of_the_same_row_inserts_nothing(session_factory):
    """The T52 acceptance check, at the job level rather than the repository level directly."""
    provider = _StubProvider("spdr", ("XLK",), rows=[_row("XLK")])

    first = await update_flows_job(providers=[provider], session_factory=session_factory)
    second = await update_flows_job(providers=[provider], session_factory=session_factory)

    assert first[0].inserted == 1
    assert second[0].inserted == 0
    assert second[0].skipped == 1


async def test_update_flows_job_closes_providers_it_owns_not_ones_the_caller_supplied(
    session_factory,
):
    provider = _StubProvider("spdr", ("XLK",), rows=[_row("XLK")])

    await update_flows_job(providers=[provider], session_factory=session_factory)

    assert provider.closed is False  # caller-supplied: not this job's to close
