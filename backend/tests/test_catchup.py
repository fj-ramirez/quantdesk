"""Tests for `app/modules/gex/jobs/catchup.py` (TASKS.md T29).

Entirely offline, same isolation pattern as `test_capture.py`: a stub provider stands in for
Cboe, a temp-directory SQLite `session_factory` stands in for Postgres. The clock is always
injected via `catch_up_missed_eod(..., now=...)` -- never real time -- so "before 16:20",
"after 16:20", a weekend, and a holiday are all exercised deterministically regardless of when
this suite actually runs.

Acceptance (TASKS.md T29): "with the clock faked past 16:20 on a trading day and an empty DB,
startup produces an is_eod=True row per symbol; starting twice does not double-capture." See
`test_catch_up_fires_and_produces_is_eod_rows_for_every_symbol` and
`test_calling_catch_up_twice_does_not_double_capture` below.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.jobs.catchup import (
    EOD_CUTOFF,
    catch_up_missed_eod,
    has_eod_snapshot_today,
    last_completed_trading_day,
    previous_trading_day,
    startup_catchup_job,
)
from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
from app.modules.gex.models.db import Base, Snapshot

# 2026-09-04 is a Friday and a trading day (verified against app/modules/gex/jobs/calendar.py's 2026
# table); 09-05/09-06 are the following weekend; 09-07 is Labor Day (a listed 2026 holiday).
TRADING_DAY = dt.date(2026, 9, 4)
SATURDAY = dt.date(2026, 9, 5)
LABOR_DAY_HOLIDAY = dt.date(2026, 9, 7)

_NY = dt.timezone(dt.timedelta(hours=-4))  # EDT offset in early September; good enough for
# constructing fixed instants in tests -- production code always goes through `ZoneInfo`.


def _at(day: dt.date, hour: int, minute: int) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=_NY)


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def make_contract(symbol: str, **kw) -> OptionContract:
    return OptionContract.from_occ(symbol, **kw)


def make_snapshot(underlying=Underlying.SPX, captured_at=None) -> ChainSnapshot:
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
        for i in range(2)
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
    """Same shape as `test_capture.py`'s stub -- one instance's `fetch_chain` returns a fresh,
    strictly-increasing `captured_at` per call so consecutive symbols never collide on the
    duplicate-capture check in `app/modules/gex/jobs/capture.py`.
    """

    def __init__(self):
        self.calls: list[str] = []
        self.closed = False
        self._next_offset = 0

    @property
    def name(self) -> str:
        return "stub"

    @property
    def delayed_minutes(self) -> int:
        return 15

    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        self.calls.append(underlying)
        captured_at = dt.datetime(2026, 9, 4, 20, 20, 0, tzinfo=dt.UTC) + dt.timedelta(
            seconds=self._next_offset
        )
        self._next_offset += 1
        return make_snapshot(Underlying(underlying), captured_at=captured_at)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def stub_provider(monkeypatch):
    provider = StubProvider()
    monkeypatch.setattr("app.modules.gex.jobs.catchup.get_provider", lambda: provider)
    return provider


def test_eod_cutoff_matches_the_scheduled_eod_job_time():
    """The catch-up guard and `app/modules/gex/jobs/scheduler.py`'s cron trigger must agree on 16:20 --
    drifting apart would either catch up too early (racing the vendor) or leave a real gap.
    """
    assert EOD_CUTOFF == dt.time(16, 20)


# --- catch_up_missed_eod: the five required scenarios --------------------------------------


async def test_before_1620_on_a_trading_day_does_not_capture(
    tmp_path, session_factory, stub_provider
):
    results = await catch_up_missed_eod(
        ["SPX", "SPY", "QQQ"],
        session_factory=session_factory,
        data_dir=tmp_path,
        now=_at(TRADING_DAY, 16, 19),
    )
    assert results == []
    assert stub_provider.calls == []
    with session_factory() as session:
        assert session.execute(select(Snapshot)).first() is None


async def test_after_1620_with_no_row_fires_catchup_and_produces_is_eod_rows_per_symbol(
    tmp_path, session_factory, stub_provider
):
    results = await catch_up_missed_eod(
        ["SPX", "SPY", "QQQ"],
        session_factory=session_factory,
        data_dir=tmp_path,
        now=_at(TRADING_DAY, 20, 0),
    )
    assert {r.underlying for r in results} == {"SPX", "SPY", "QQQ"}
    assert all(r.ok for r in results)

    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert {row.underlying for row in rows} == {"SPX", "SPY", "QQQ"}
        assert all(row.is_eod is True for row in rows)


async def test_after_1620_with_row_already_present_is_a_noop(
    tmp_path, session_factory, stub_provider
):
    # Pre-seed one symbol's EOD row directly, as if an earlier catch-up (or the 16:20 job
    # itself) already ran today.
    with session_factory() as session:
        session.add(
            Snapshot(
                underlying="SPX",
                captured_at=dt.datetime(2026, 9, 4, 20, 20, 0, tzinfo=dt.UTC),
                source="cboe",
                spot=6500.0,
                contract_count=2,
                parquet_path="spx-preexisting.parquet",
                is_eod=True,
            )
        )
        session.commit()

    results = await catch_up_missed_eod(
        ["SPX"],
        session_factory=session_factory,
        data_dir=tmp_path,
        now=_at(TRADING_DAY, 20, 0),
    )

    assert results == []
    assert stub_provider.calls == []  # never even hit the provider for the symbol that's covered
    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert len(rows) == 1  # still just the pre-seeded row -- no duplicate


async def test_weekend_does_not_capture(tmp_path, session_factory, stub_provider):
    results = await catch_up_missed_eod(
        ["SPX"], session_factory=session_factory, data_dir=tmp_path, now=_at(SATURDAY, 20, 0)
    )
    assert results == []
    assert stub_provider.calls == []


async def test_holiday_does_not_capture(tmp_path, session_factory, stub_provider):
    results = await catch_up_missed_eod(
        ["SPX"],
        session_factory=session_factory,
        data_dir=tmp_path,
        now=_at(LABOR_DAY_HOLIDAY, 20, 0),
    )
    assert results == []
    assert stub_provider.calls == []


# --- acceptance: starting twice must not double-capture -------------------------------------


async def test_calling_catch_up_twice_does_not_double_capture(
    tmp_path, session_factory, monkeypatch
):
    """The exact T29 acceptance scenario: empty DB, clock faked past 16:20, catch-up runs;
    running it again (simulating a second process start the same evening) must not add a
    second row per symbol.
    """
    provider1 = StubProvider()
    provider2 = StubProvider()
    calls = iter([provider1, provider2])
    monkeypatch.setattr("app.modules.gex.jobs.catchup.get_provider", lambda: next(calls))

    now = _at(TRADING_DAY, 20, 0)
    first = await catch_up_missed_eod(
        ["SPX", "SPY", "QQQ"], session_factory=session_factory, data_dir=tmp_path, now=now
    )
    second = await catch_up_missed_eod(
        ["SPX", "SPY", "QQQ"], session_factory=session_factory, data_dir=tmp_path, now=now
    )

    assert len(first) == 3
    assert all(r.ok for r in first)
    assert second == []  # nothing left to do the second time
    assert provider2.calls == []

    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert len(rows) == 3
        assert all(row.is_eod is True for row in rows)


async def test_catch_up_never_double_capture_when_time_advances_within_the_evening(
    tmp_path, session_factory, monkeypatch
):
    """Same idea as the previous test but at two different `now` values within the same
    evening -- confirms the guard is "row exists for today", not "identical `now` was passed".
    """
    provider1 = StubProvider()
    provider2 = StubProvider()
    calls = iter([provider1, provider2])
    monkeypatch.setattr("app.modules.gex.jobs.catchup.get_provider", lambda: next(calls))

    await catch_up_missed_eod(
        ["SPX"], session_factory=session_factory, data_dir=tmp_path, now=_at(TRADING_DAY, 16, 25)
    )
    second = await catch_up_missed_eod(
        ["SPX"], session_factory=session_factory, data_dir=tmp_path, now=_at(TRADING_DAY, 21, 0)
    )

    assert second == []
    with session_factory() as session:
        assert len(session.execute(select(Snapshot)).scalars().all()) == 1


# --- has_eod_snapshot_today -------------------------------------------------------------------


def test_has_eod_snapshot_today_true_only_for_is_eod_row_on_that_ny_date(session_factory):
    with session_factory() as session:
        session.add(
            Snapshot(
                underlying="SPX",
                captured_at=dt.datetime(2026, 9, 4, 20, 20, 0, tzinfo=dt.UTC),
                source="cboe",
                spot=6500.0,
                contract_count=1,
                parquet_path="x.parquet",
                is_eod=True,
            )
        )
        session.add(
            Snapshot(
                underlying="SPY",
                captured_at=dt.datetime(2026, 9, 4, 15, 0, 0, tzinfo=dt.UTC),
                source="cboe",
                spot=500.0,
                contract_count=1,
                parquet_path="y.parquet",
                is_eod=False,  # intraday, not EOD -- must not count
            )
        )
        session.commit()

    assert has_eod_snapshot_today("SPX", session_factory, TRADING_DAY) is True
    assert has_eod_snapshot_today("SPY", session_factory, TRADING_DAY) is False
    assert has_eod_snapshot_today("QQQ", session_factory, TRADING_DAY) is False


def test_has_eod_snapshot_today_respects_ny_date_not_utc_date(session_factory):
    """21:30 ET on 2026-09-04 is already 2026-09-05 in UTC -- the check must use the NY
    calendar date, not whatever date the raw UTC timestamp happens to fall on.
    """
    captured_at_utc = dt.datetime(2026, 9, 4, 21, 30, 0, tzinfo=_NY).astimezone(dt.UTC)
    assert captured_at_utc.date() == dt.date(2026, 9, 5)  # confirms the UTC/NY date mismatch

    with session_factory() as session:
        session.add(
            Snapshot(
                underlying="SPX",
                captured_at=captured_at_utc,
                source="cboe",
                spot=6500.0,
                contract_count=1,
                parquet_path="x.parquet",
                is_eod=True,
            )
        )
        session.commit()

    assert has_eod_snapshot_today("SPX", session_factory, TRADING_DAY) is True
    assert has_eod_snapshot_today("SPX", session_factory, dt.date(2026, 9, 5)) is False


# --- last_completed_trading_day / previous_trading_day ---------------------------------------


def test_last_completed_trading_day_is_today_when_past_cutoff_on_a_trading_day():
    assert last_completed_trading_day(_at(TRADING_DAY, 16, 20)) == TRADING_DAY
    assert last_completed_trading_day(_at(TRADING_DAY, 23, 59)) == TRADING_DAY


def test_last_completed_trading_day_is_previous_trading_day_before_cutoff():
    assert last_completed_trading_day(_at(TRADING_DAY, 9, 30)) == previous_trading_day(TRADING_DAY)


def test_last_completed_trading_day_on_a_weekend_or_holiday_is_the_prior_friday():
    assert last_completed_trading_day(_at(SATURDAY, 12, 0)) == TRADING_DAY
    assert last_completed_trading_day(_at(LABOR_DAY_HOLIDAY, 6, 0)) == TRADING_DAY


def test_previous_trading_day_skips_weekend_and_holiday():
    # Monday 2026-09-07 is Labor Day; the trading day before the following Tuesday (09-08) is
    # therefore Friday 09-04, skipping both the holiday Monday and the weekend before it.
    tuesday = dt.date(2026, 9, 8)
    assert previous_trading_day(tuesday) == TRADING_DAY


# --- startup_catchup_job: must never raise ----------------------------------------------------


async def test_startup_catchup_job_survives_an_unexpected_exception(monkeypatch, caplog):
    import logging

    async def boom(*args, **kwargs):
        raise RuntimeError("cboe is unreachable")

    monkeypatch.setattr("app.modules.gex.jobs.catchup.catch_up_missed_eod", boom)

    with caplog.at_level(logging.ERROR, logger="app.modules.gex.jobs.catchup"):
        await startup_catchup_job()  # must not raise

    assert any("unexpected top-level failure" in r.message for r in caplog.records)


async def test_startup_catchup_job_calls_catch_up_missed_eod_with_configured_symbols(monkeypatch):
    seen = {}

    async def fake_catch_up(symbols, **kwargs):
        seen["symbols"] = symbols
        return []

    monkeypatch.setattr("app.modules.gex.jobs.catchup.catch_up_missed_eod", fake_catch_up)

    await startup_catchup_job()

    assert seen["symbols"] == ["SPX", "SPY", "QQQ", "GLD", "DIA"]
