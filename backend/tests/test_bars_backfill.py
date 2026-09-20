"""Tests for `app/modules/gex/bars_backfill.py`. Offline: a stub provider/registry stands in for Yahoo, a
temp-file SQLite `session_factory` stands in for Postgres. `today=` is always injected so
"already up to date" skip logic is deterministic regardless of the real calendar date -- see
`backfill`'s own docstring.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.bars_backfill import backfill
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
    """Returns `days` consecutive bars starting 2026-09-01 for any symbol not in `fail_for`."""

    def __init__(self, *, days: int = 5, fail_for: set[str] = frozenset()):
        self.days = days
        self.fail_for = fail_for
        self.calls: list[tuple[str, dt.date]] = []

    async def fetch_daily_bars(self, symbol, *, start, end=None):
        self.calls.append((symbol, start))
        if symbol in self.fail_for:
            raise UpstreamUnavailable(f"stub: {symbol} is down")
        base = dt.date(2026, 9, 1)
        return [
            DailyBar(
                symbol=symbol,
                date=base + dt.timedelta(days=i),
                open=100.0 + i,
                high=101.0 + i,
                low=99.0 + i,
                close=100.5 + i,
                volume=1000,
                source="stub",
            )
            for i in range(self.days)
        ]

    async def close(self) -> None:
        pass


class _StubRegistry:
    def __init__(self, provider: _StubProvider):
        self._provider = provider

    def for_symbol(self, symbol: str):
        return self._provider

    async def aclose(self) -> None:
        pass


# "Today" fixed well past the stub's last bar (2026-09-05) but not so far that the
# up-to-date-within-5-days skip kicks in on the second run.
_TODAY_JUST_AFTER = dt.date(2026, 9, 6)


async def test_backfill_populates_rows(session_factory):
    provider = _StubProvider(days=5)
    registry = _StubRegistry(provider)

    result = await backfill(
        symbols=["SPY", "QQQ", "SPX"],
        years=1,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )

    assert result["total"] == 3
    assert result["fetched"] == 3
    assert result["failed"] == 0
    assert result["inserted"] == 15  # 5 days x 3 symbols
    assert result["skipped_up_to_date"] == 0


async def test_second_backfill_run_inserts_zero_new_rows(session_factory):
    """The T42 acceptance check: `uv run python -m app.modules.gex.bars_backfill --years 2 --symbols
    SPY,XLK,SPX` populates rows and a second run inserts zero new rows."""
    provider = _StubProvider(days=5)
    registry = _StubRegistry(provider)
    symbols = ["SPY", "XLK", "SPX"]

    first = await backfill(
        symbols=symbols,
        years=2,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )
    assert first["inserted"] == 15

    second = await backfill(
        symbols=symbols,
        years=2,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )
    # Every symbol's last_bar_date (2026-09-05) is within 5 days of "today" (2026-09-06), so
    # the second run skips outright -- zero new requests, zero new rows, by construction.
    assert second["inserted"] == 0
    assert second["skipped_up_to_date"] == 3
    assert second["fetched"] == 0


async def test_up_to_date_symbol_makes_zero_requests(session_factory):
    provider = _StubProvider(days=5)
    registry = _StubRegistry(provider)

    await backfill(
        symbols=["SPY"],
        years=1,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )
    assert provider.calls == [("SPY", dt.date(2026, 9, 6) - dt.timedelta(days=365))]

    provider.calls.clear()
    await backfill(
        symbols=["SPY"],
        years=1,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )
    assert provider.calls == []  # already up to date -- no HTTP request at all


async def test_force_refetches_an_up_to_date_symbol_over_the_full_years_window(
    session_factory,
):
    """`--force` is the only way to deepen stored history.

    The resumability skip keys on "this symbol already has recent bars", which is permanently
    true once the 17:30 bars job has been running -- so without `force` a later
    `--years 5` can never reach further back than the store already goes. Asserts both halves:
    the request happens at all, and it starts from the full `years` window rather than the
    incremental `last_bar_date - 5 days` an unforced run would use.
    """
    provider = _StubProvider(days=5)
    registry = _StubRegistry(provider)
    common = {
        "symbols": ["SPY"],
        "sleep_seconds": 0.0,
        "registry": registry,
        "session_factory": session_factory,
        "today": _TODAY_JUST_AFTER,
    }

    await backfill(years=1, **common)
    provider.calls.clear()

    # Unforced: the symbol is now up to date, so no request at all (the existing behaviour).
    await backfill(years=1, **common)
    assert provider.calls == []

    # Forced: fetched again, and from the full 5-year window, not last_bar_date - 5 days.
    result = await backfill(years=5, force=True, **common)
    assert provider.calls == [("SPY", _TODAY_JUST_AFTER - dt.timedelta(days=365 * 5))]
    assert result["skipped_up_to_date"] == 0
    assert result["fetched"] == 1
    # Idempotent even when forced: the same dates are rewritten, never duplicated.
    assert result["inserted"] == 0
    assert result["updated"] == 5


async def test_one_symbol_failing_does_not_stop_the_others(session_factory):
    provider = _StubProvider(days=5, fail_for={"QQQ"})
    registry = _StubRegistry(provider)

    result = await backfill(
        symbols=["SPY", "QQQ", "DIA"],
        years=1,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )

    assert result["failed"] == 1
    assert result["fetched"] == 2
    assert {s for s, _ in provider.calls} == {"SPY", "QQQ", "DIA"}  # every symbol attempted


async def test_far_future_today_skips_resumable_symbol_but_not_a_never_fetched_one(
    session_factory,
):
    """A symbol with no stored bars at all is never skipped, regardless of `today` -- only an
    already-up-to-date symbol is."""
    provider = _StubProvider(days=5)
    registry = _StubRegistry(provider)

    # First populate SPY only.
    await backfill(
        symbols=["SPY"],
        years=1,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )
    provider.calls.clear()

    result = await backfill(
        symbols=["SPY", "QQQ"],
        years=1,
        sleep_seconds=0.0,
        registry=registry,
        session_factory=session_factory,
        today=_TODAY_JUST_AFTER,
    )
    assert result["skipped_up_to_date"] == 1  # SPY
    assert result["fetched"] == 1  # QQQ, never fetched before
    assert {s for s, _ in provider.calls} == {"QQQ"}
