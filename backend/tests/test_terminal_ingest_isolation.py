"""One unavailable source must not cost the sources after it (T97).

Found by running the deployed nightly sequence by hand on 2026-09-21. `XA_FRED_API_KEY` is
empty on the homeserver, so `build_adapter("fred", ...)` raised `UnknownSeriesError` -- and
that exception left `cmd_ingest` entirely. Sources are iterated in sorted order, so `treasury`
sat behind `fred` and never ran: the par yield curve stopped updating because of a missing key
for a *different* vendor. The only visible symptom was an absent `terminal.ingest_batches`
row, which is an absence, and nobody reads absences.

This is the same shape as T90 one level down, and it is tested the same way: the failure is
injected, and the assertion is that the work after it still happened.
"""

from __future__ import annotations

import argparse
import datetime as dt

import pytest

from app.modules.terminal import cli
from app.modules.terminal.errors import UnknownSeriesError


class _FakeStore:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeLoader:
    """Records what a run did, without a database."""

    def __init__(self, store):
        self.started: list[str] = []
        self.finished: list[tuple[str, str]] = []
        self.persisted: list[object] = []

    def register_series(self, meta) -> None:
        pass

    def start_batch(self, source: str, window: str) -> str:
        self.started.append(source)
        return f"batch-{source}"

    def persist(self, observations) -> None:
        self.persisted.append(observations)

    def finish_batch(self, batch: str, status: str, error: str | None = None) -> None:
        self.finished.append((batch.removeprefix("batch-"), status))


class _FakeAdapter:
    def __init__(self, source: str):
        self.source = source

    def fetch(self, codes, start, end, batch, **kw):
        return [f"{self.source}:{code}" for code in codes]

    def close(self) -> None:
        pass


@pytest.fixture
def run_ingest(monkeypatch):
    """Drives `cmd_ingest` over the real universe with fake I/O.

    The universe is real on purpose: the ordering of `sorted(FETCHABLE_SOURCES)` is the thing
    that turned a missing FRED key into missing Treasury data, and a test with an invented
    two-source universe would not reproduce it.
    """
    loaders: list[_FakeLoader] = []

    def _loader(store):
        loader = _FakeLoader(store)
        loaders.append(loader)
        return loader

    monkeypatch.setattr(cli, "Store", _FakeStore)
    monkeypatch.setattr(cli, "Loader", _loader)

    def _run(build_adapter, settings=None):
        monkeypatch.setattr(cli, "build_adapter", build_adapter)
        args = argparse.Namespace(
            since=dt.date(2026, 9, 1),
            until=dt.date(2026, 9, 21),
            source="all",
            register_only=False,
        )
        code = cli.cmd_ingest(args, settings or cli.load_settings())
        return code, loaders[-1]

    return _run


def test_a_source_whose_adapter_cannot_be_built_does_not_stop_the_others(run_ingest):
    """The homeserver's exact failure: no FRED key, everything else fine."""

    def _build(source, settings):
        if source == "fred":
            raise UnknownSeriesError("FRED adapter requires an API key: set XA_FRED_API_KEY")
        return _FakeAdapter(source)

    code, loader = run_ingest(_build)

    assert code == 1, "a source that did not run must still make the run fail"
    # fred never got a batch -- there was nothing to record against it -- but treasury, which
    # sorts after it, did and finished clean.
    assert "fred" not in loader.started
    assert "treasury" in loader.started
    assert ("treasury", "ok") in loader.finished
    assert {"cboe", "cftc"} <= set(loader.started)


def test_a_source_that_fails_mid_fetch_does_not_stop_the_others(run_ingest):
    """The other half: the adapter built, then the vendor fell over."""

    class _Exploding(_FakeAdapter):
        def fetch(self, *a, **kw):
            raise TimeoutError("vendor timed out")

    def _build(source, settings):
        return _Exploding(source) if source == "cboe" else _FakeAdapter(source)

    code, loader = run_ingest(_build)

    assert code == 1
    assert ("cboe", "failed") in loader.finished
    # And the batch bookkeeping stays honest for the sources that were fine, which it does
    # not if "were there any failures at all" decides each batch's status.
    assert ("treasury", "ok") in loader.finished
    assert ("cftc", "ok") in loader.finished


def test_a_clean_run_marks_every_batch_ok(run_ingest):
    code, loader = run_ingest(lambda source, settings: _FakeAdapter(source))

    assert code == 0
    assert sorted(loader.started) == ["cboe", "cftc", "fred", "treasury"]
    assert all(status == "ok" for _, status in loader.finished)
