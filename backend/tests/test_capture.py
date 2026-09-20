"""Tests for `app/modules/gex/jobs/capture.py`. Entirely offline: a hand-built stub provider stands in
for the network, a temp-directory SQLite `session_factory` stands in for Postgres, and
`data_dir` points writes at `tmp_path` -- see `test_snapshot_repository.py` /
`test_parquet_storage.py` for the same two isolation patterns used independently elsewhere.
"""

from __future__ import annotations

import datetime as dt
import json
import logging

import pytest
from sqlalchemy import select

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.jobs.capture import (
    CaptureResult,
    _log_result,
    capture_all_symbols,
    capture_snapshot,
)
from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
from app.modules.gex.models.db import Base, Snapshot
from app.modules.gex.providers.base import UpstreamUnavailable


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def make_contract(symbol: str, **kw) -> OptionContract:
    return OptionContract.from_occ(symbol, **kw)


def make_snapshot(underlying=Underlying.SPX, captured_at=None, n_contracts=2) -> ChainSnapshot:
    captured_at = captured_at or dt.datetime(2026, 9, 4, 20, 20, 0, tzinfo=dt.UTC)
    symbol_root = {
        "SPX": "SPX260918C0",
        "SPY": "SPY260918C0",
        "QQQ": "QQQ260918C0",
        "GLD": "GLD260918C0",
        "DIA": "DIA260918C0",
    }[underlying.value]
    contracts = [
        make_contract(
            f"{symbol_root}{4000 + i * 10}000",
            bid=1.0,
            ask=1.1,
            open_interest=100,
            iv=0.2,
            delta=0.5,
            gamma=0.001,
        )
        for i in range(n_contracts)
    ]
    return ChainSnapshot(
        underlying=underlying,
        spot=6500.0,
        captured_at=captured_at,
        source="stub",
        delayed_minutes=15,
        contracts=contracts,
    )


class StubProvider:
    """Minimal `OptionChainProvider`-shaped stub. Not a subclass -- `capture_snapshot` only
    relies on duck typing (`fetch_chain`, optionally `close`), same as the real providers."""

    def __init__(self, snapshot: ChainSnapshot | None = None, error: Exception | None = None):
        self._snapshot = snapshot
        self._error = error
        self.closed = False
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "stub"

    @property
    def delayed_minutes(self) -> int:
        return 15

    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        self.calls.append(underlying)
        if self._error is not None:
            raise self._error
        return self._snapshot

    async def close(self) -> None:
        self.closed = True


async def test_capture_snapshot_writes_parquet_and_index_row(tmp_path, session_factory):
    snapshot = make_snapshot()
    provider = StubProvider(snapshot=snapshot)

    result = await capture_snapshot(
        "SPX",
        is_eod=True,
        provider=provider,
        session_factory=session_factory,
        data_dir=tmp_path,
    )

    assert result.ok is True
    assert result.contract_count == 2
    assert result.spot == pytest.approx(6500.0)
    assert result.snapshot_id is not None
    assert result.skipped_duplicate is False
    assert result.error is None
    assert result.duration_seconds >= 0

    written = tmp_path / result.parquet_path
    assert written.exists()

    with session_factory() as session:
        row = session.get(Snapshot, result.snapshot_id)
        assert row.underlying == "SPX"
        assert row.is_eod is True
        assert row.contract_count == 2


async def test_capture_snapshot_does_not_close_an_injected_provider(tmp_path, session_factory):
    """Shared-provider ownership: `capture_all_symbols` reuses one provider across three
    calls, so a single `capture_snapshot` call must never close a provider it didn't create."""
    provider = StubProvider(snapshot=make_snapshot())
    await capture_snapshot(
        "SPX", is_eod=False, provider=provider, session_factory=session_factory, data_dir=tmp_path
    )
    assert provider.closed is False


async def test_capture_snapshot_closes_a_provider_it_constructed_itself(
    tmp_path, session_factory, monkeypatch
):
    provider = StubProvider(snapshot=make_snapshot())
    monkeypatch.setattr("app.modules.gex.jobs.capture.get_provider", lambda: provider)

    await capture_snapshot("SPX", is_eod=False, session_factory=session_factory, data_dir=tmp_path)

    assert provider.closed is True


async def test_capture_snapshot_provider_failure_returns_error_result_without_raising(
    tmp_path, session_factory
):
    provider = StubProvider(error=UpstreamUnavailable("cboe is down"))

    result = await capture_snapshot(
        "SPX", is_eod=True, provider=provider, session_factory=session_factory, data_dir=tmp_path
    )

    assert result.ok is False
    assert result.snapshot_id is None
    assert "cboe is down" in result.error
    # Nothing should have been written for a failed fetch.
    with session_factory() as session:
        assert session.execute(select(Snapshot)).scalar_one_or_none() is None


async def test_capture_snapshot_storage_failure_returns_error_result_without_raising(
    tmp_path, session_factory, monkeypatch
):
    """A `_persist_sync` exception (bad DATA_DIR, DB down, whatever) must degrade to a
    CaptureResult, not propagate -- this is the "failure must not crash the scheduler"
    requirement exercised directly at the storage boundary."""
    provider = StubProvider(snapshot=make_snapshot())

    def boom(*args, **kwargs):
        raise RuntimeError("disk is full")

    monkeypatch.setattr("app.modules.gex.jobs.capture._persist_sync", boom)

    result = await capture_snapshot(
        "SPX", is_eod=True, provider=provider, session_factory=session_factory, data_dir=tmp_path
    )

    assert result.ok is False
    assert result.contract_count == 2  # fetch succeeded; only persistence failed
    assert "disk is full" in result.error


async def test_capture_snapshot_skips_exact_duplicate_captured_at(tmp_path, session_factory):
    """Cboe's timestamp advances continuously (see the module docstring on `_persist_sync`),
    so an identical (underlying, captured_at) pair on a second call is a genuine duplicate --
    it must not overwrite the first Parquet file or create a second index row."""
    snapshot = make_snapshot()
    provider1 = StubProvider(snapshot=snapshot)
    provider2 = StubProvider(snapshot=snapshot)

    first = await capture_snapshot(
        "SPX", is_eod=True, provider=provider1, session_factory=session_factory, data_dir=tmp_path
    )
    second = await capture_snapshot(
        "SPX", is_eod=True, provider=provider2, session_factory=session_factory, data_dir=tmp_path
    )

    assert first.skipped_duplicate is False
    assert second.skipped_duplicate is True
    assert second.snapshot_id == first.snapshot_id
    assert second.parquet_path == first.parquet_path

    with session_factory() as session:
        assert session.execute(select(Snapshot)).scalars().all().__len__() == 1


async def test_capture_all_symbols_one_failure_does_not_stop_the_others(tmp_path, session_factory):
    """The core multi-symbol resilience requirement: SPY failing must not prevent SPX/QQQ."""
    snapshots = {
        "SPX": make_snapshot(Underlying.SPX),
        "QQQ": make_snapshot(Underlying.QQQ),
    }
    calls: list[str] = []

    class MultiStubProvider:
        name = "stub"
        delayed_minutes = 15

        async def fetch_chain(self, underlying: str) -> ChainSnapshot:
            calls.append(underlying)
            if underlying == "SPY":
                raise UpstreamUnavailable("SPY endpoint returned 500")
            return snapshots[underlying]

        async def close(self) -> None:
            calls.append("closed")

    monkeypatch_provider = MultiStubProvider()

    import app.modules.gex.jobs.capture as capture_module

    original_get_provider = capture_module.get_provider
    capture_module.get_provider = lambda: monkeypatch_provider
    try:
        results = await capture_all_symbols(
            ["SPX", "SPY", "QQQ"],
            is_eod=True,
            session_factory=session_factory,
            data_dir=tmp_path,
        )
    finally:
        capture_module.get_provider = original_get_provider

    by_symbol = {r.underlying: r for r in results}
    assert by_symbol["SPX"].ok is True
    assert by_symbol["QQQ"].ok is True
    assert by_symbol["SPY"].ok is False
    assert "SPY endpoint returned 500" in by_symbol["SPY"].error
    assert calls[-1] == "closed"  # the shared provider is closed exactly once, at the end

    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert {r.underlying for r in rows} == {"SPX", "QQQ"}


async def test_capture_snapshot_survives_a_non_provider_error(tmp_path, session_factory):
    """T06 review: a provider bug (or an unanticipated vendor payload) that leaks something
    other than a ProviderError must still degrade to a CaptureResult. Anything else aborts
    `capture_all_symbols` mid-loop and silently loses the symbols after it."""
    provider = StubProvider(error=KeyError("option"))

    result = await capture_snapshot(
        "SPX", is_eod=True, provider=provider, session_factory=session_factory, data_dir=tmp_path
    )

    assert result.ok is False
    assert "KeyError" in result.error


async def test_capture_all_symbols_continues_past_a_non_provider_error(tmp_path, session_factory):
    """The multi-symbol resilience requirement, for the non-ProviderError case."""
    snapshots = {
        "SPX": make_snapshot(Underlying.SPX),
        "QQQ": make_snapshot(Underlying.QQQ),
    }

    class MultiStubProvider:
        name = "stub"
        delayed_minutes = 15

        async def fetch_chain(self, underlying: str) -> ChainSnapshot:
            if underlying == "SPY":
                raise KeyError("option")  # not a ProviderError
            return snapshots[underlying]

        async def close(self) -> None:
            pass

    import app.modules.gex.jobs.capture as capture_module

    original_get_provider = capture_module.get_provider
    capture_module.get_provider = MultiStubProvider
    try:
        results = await capture_all_symbols(
            ["SPX", "SPY", "QQQ"], is_eod=True, session_factory=session_factory, data_dir=tmp_path
        )
    finally:
        capture_module.get_provider = original_get_provider

    by_symbol = {r.underlying: r for r in results}
    assert by_symbol["SPY"].ok is False
    assert by_symbol["SPX"].ok is True
    assert by_symbol["QQQ"].ok is True  # would never have been attempted before the fix


async def test_duplicate_capture_promotes_is_eod(tmp_path, session_factory):
    """A manual capture followed by the 16:20 EOD job landing on the same vendor timestamp
    must not leave the day with no is_eod row at all."""
    snapshot = make_snapshot()

    manual = await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    eod = await capture_snapshot(
        "SPX",
        is_eod=True,
        provider=StubProvider(snapshot=snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )

    assert eod.skipped_duplicate is True
    assert eod.snapshot_id == manual.snapshot_id
    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert len(rows) == 1
        assert rows[0].is_eod is True


async def test_duplicate_capture_never_demotes_is_eod(tmp_path, session_factory):
    """Promotion is one-way: a later manual capture must not clear the EOD flag."""
    snapshot = make_snapshot()
    await capture_snapshot(
        "SPX",
        is_eod=True,
        provider=StubProvider(snapshot=snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert len(rows) == 1
        assert rows[0].is_eod is True


def test_log_result_emits_valid_json_with_required_fields(caplog):
    result = CaptureResult(
        underlying="SPX",
        ok=True,
        contract_count=246,
        spot=6500.12,
        duration_seconds=1.234,
        snapshot_id=1,
        parquet_path="chains/SPX/2026/09/x.parquet",
    )
    with caplog.at_level(logging.INFO, logger="app.modules.gex.jobs.capture"):
        _log_result(result)

    record = next(r for r in caplog.records if r.name == "app.modules.gex.jobs.capture")
    payload = json.loads(record.message)
    assert payload["event"] == "capture"
    assert payload["underlying"] == "SPX"
    assert payload["contract_count"] == 246
    assert payload["spot"] == pytest.approx(6500.12)
    assert payload["duration_seconds"] == pytest.approx(1.234)
    assert payload["error"] is None


def test_log_result_failure_logs_at_error_level(caplog):
    result = CaptureResult(underlying="SPX", ok=False, error="boom")
    with caplog.at_level(logging.INFO, logger="app.modules.gex.jobs.capture"):
        _log_result(result)
    record = next(r for r in caplog.records if r.name == "app.modules.gex.jobs.capture")
    assert record.levelno == logging.ERROR
    assert json.loads(record.message)["error"] == "boom"
