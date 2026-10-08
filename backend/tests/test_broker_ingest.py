"""The broker clock and ingest (T144, plans/charter-mt5/README.md).

Fully offline: SQLite, with the `broker` schema translated away, and a fake bridge whose bars
are **synthetic**. They are labelled as such, generated from a known UTC series, and stamped
in server time with the New York close convention, so every conversion has a right answer to
check against.
"""

from __future__ import annotations

import datetime as dt
import itertools
import logging

import pandas as pd
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.modules.broker import servertime
from app.modules.broker.client import Reply
from app.modules.broker.ingest import CHUNK, ClockGate, Ingestor, symbol_slug
from app.modules.broker.tables import Bar, ClockCheck, SymbolSpec, TickFile, ensure_schema

UTC = dt.UTC


# --------------------------------------------------------------------------- servertime


def test_model_offset_follows_us_dst():
    assert servertime.model_offset(dt.datetime(2026, 1, 15, 12, tzinfo=UTC)) == 2 * 3600
    assert servertime.model_offset(dt.datetime(2026, 7, 15, 12, tzinfo=UTC)) == 3 * 3600
    # US DST starts 2026-03-08 (Europe only on 03-29); the convention follows New York.
    assert servertime.model_offset(dt.datetime(2026, 3, 10, 12, tzinfo=UTC)) == 3 * 3600


def test_new_york_close_is_server_midnight():
    close = dt.datetime(2026, 10, 8, 17, tzinfo=servertime.NY)
    server = dt.datetime.fromtimestamp(servertime.utc_to_server_epoch(close), tz=UTC)
    assert (server.hour, server.minute) == (0, 0)


@pytest.mark.parametrize("utc", [
    dt.datetime(2026, 3, 6, 21, 59, tzinfo=UTC),   # Friday before the US switch
    dt.datetime(2026, 3, 9, 13, 30, tzinfo=UTC),   # Monday after it
    dt.datetime(2026, 11, 2, 14, 0, tzinfo=UTC),
    dt.datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
])
def test_server_epoch_round_trip(utc):
    assert servertime.server_epoch_to_utc(servertime.utc_to_server_epoch(utc)) == utc


def test_measure_offset_rounds_and_rejects_unclean_readings():
    utc_ms = 1_791_499_132_091
    assert servertime.measure_offset(utc_ms + 3 * 3600_000 - 20_000, utc_ms) == 3 * 3600
    assert servertime.measure_offset(utc_ms + 3 * 3600_000 - 400_000, utc_ms) is None


def test_slug():
    assert symbol_slug("S&P.fs") == "s-p.fs"
    assert symbol_slug("NAS100.fs") == "nas100.fs"


# --------------------------------------------------------------------------- fake bridge


def _closed(t: dt.datetime) -> bool:
    """Friday 17:00 to Sunday 18:00 New York: the index CFDs' weekend."""
    ny = t.astimezone(servertime.NY)
    wd = ny.weekday()
    return (wd == 4 and ny.hour >= 17) or wd == 5 or (wd == 6 and ny.hour < 18)


def _open_minutes(start: dt.datetime, end: dt.datetime) -> int:
    n, t = 0, start
    while t < end:
        n += not _closed(t)
        t += dt.timedelta(minutes=1)
    return n


class FakeBridge:
    """Synthetic M1 bars every minute from `history_start`, bar *i* closing at 100 + i/1000,
    stamped in server time by the convention, unless `offset_error_s` skews the tick clock."""

    def __init__(self, history_start: dt.datetime, *, offset_error_s: int = 0):
        self.history_start = history_start
        self.offset_error_s = offset_error_s
        self.now = history_start
        self.tick_ms = 0
        self.spec_value = {"swap_long": -1.0, "volume_min": 0.01}
        self.rates_calls = 0

    async def tick(self, symbol):
        self.tick_ms += 1000  # always moving: a fresh tick each poll
        utc_ms = int(self.now.timestamp() * 1000)
        server_ms = int(servertime.utc_to_server_epoch(self.now) * 1000) + self.offset_error_s * 1000
        return Reply({"time_msc": server_ms + self.tick_ms // 1000}, utc_ms)

    async def rates(self, symbol, timeframe, start, end):
        self.rates_calls += 1
        lo = max(servertime.server_epoch_to_utc(start), self.history_start)
        hi = servertime.server_epoch_to_utc(end)
        rows = []
        t = lo.replace(second=0, microsecond=0)
        while t < hi:
            if t <= self.now and not _closed(t):  # includes the forming bar, as MT5 does
                i = int((t - self.history_start).total_seconds() // 60)
                c = 100 + i / 1000
                rows.append((servertime.utc_to_server_epoch(t), c, c + 0.5, c - 0.5, c, 10, 3, 0))
            t += dt.timedelta(minutes=1)
        cols = ["server_time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]
        return pd.DataFrame(rows, columns=cols)

    async def ticks(self, symbol, start, end):
        ms = [start * 1000 + k * 600_000 for k in range(6)]
        return pd.DataFrame({"time_msc": ms, "bid": [1.0] * 6, "ask": [1.1] * 6,
                             "last": [0.0] * 6, "flags": [6] * 6})

    async def spec(self, symbol):
        return Reply(dict(self.spec_value), 0)


@pytest.fixture
def factory(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'broker.db'}").execution_options(
        schema_translate_map={"broker": None})
    ensure_schema(engine)
    yield sessionmaker(engine)
    engine.dispose()


def _ingestor(bridge, factory, tmp_path, **kw):
    return Ingestor(client=bridge, factory=factory, symbols=["S&P.fs"], data_dir=tmp_path, **kw)


async def _confirm_clock(ing, bridge, factory):
    """Two polls: the first only remembers the tick, the second measures from a fresh one."""
    with factory() as s:
        await ing.gate.check(bridge, ing.symbols, s, bridge.now)
        await ing.gate.check(bridge, ing.symbols, s, bridge.now)


# --------------------------------------------------------------------------- the clock gate


async def test_no_bars_until_the_clock_is_confirmed(factory, tmp_path):
    now = dt.datetime(2026, 10, 8, 14, 0, tzinfo=UTC)
    bridge = FakeBridge(now - dt.timedelta(days=3))
    bridge.now = now
    ing = _ingestor(bridge, factory, tmp_path)
    await ing.run_minute(now)  # first sight of the tick: nothing measured
    with factory() as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(Bar)) == 0
    await ing.run_minute(now)
    with factory() as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(Bar)) > 0
        check = s.execute(sa.select(ClockCheck)).scalar_one()
        assert check.agrees is True and check.measured_s == 3 * 3600


async def test_a_disagreeing_clock_stops_writes(factory, tmp_path, caplog):
    now = dt.datetime(2026, 10, 8, 14, 0, tzinfo=UTC)
    bridge = FakeBridge(now - dt.timedelta(days=1), offset_error_s=3600)  # broker moved an hour
    bridge.now = now
    ing = _ingestor(bridge, factory, tmp_path)
    with caplog.at_level(logging.ERROR):
        await ing.run_minute(now)
        await ing.run_minute(now)
    assert "DISAGREES" in caplog.text
    with factory() as s:
        assert s.scalar(sa.select(sa.func.count()).select_from(Bar)) == 0


async def test_stale_ticks_do_not_measure(factory):
    gate = ClockGate()
    now = dt.datetime(2026, 10, 10, 14, tzinfo=UTC)  # a Saturday

    class Frozen:
        async def tick(self, symbol):
            return Reply({"time_msc": 1_000}, int(now.timestamp() * 1000))

    with factory() as s:
        for _ in range(3):
            await gate.check(Frozen(), ["XAUUSD"], s, now)
        assert s.scalar(sa.select(sa.func.count()).select_from(ClockCheck)) == 0


# --------------------------------------------------------------------------- bars


async def test_bars_convert_to_utc_and_skip_the_forming_bar(factory, tmp_path):
    now = dt.datetime(2026, 10, 8, 14, 0, 30, tzinfo=UTC)
    bridge = FakeBridge(now - dt.timedelta(hours=2))
    bridge.now = now
    ing = _ingestor(bridge, factory, tmp_path)
    await _confirm_clock(ing, bridge, factory)
    await ing.bars_forward("S&P.fs", now)
    with factory() as s:
        bars = s.execute(sa.select(Bar).order_by(Bar.ts)).scalars().all()
    assert bars[0].ts == dt.datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    assert bars[-1].ts == dt.datetime(2026, 10, 8, 13, 59, tzinfo=UTC)  # 14:00 is still forming
    assert {b.offset_s for b in bars} == {3 * 3600}
    assert bars[0].ts.tzinfo is not None


async def test_backward_walk_finds_the_start_of_history_and_stops(factory, tmp_path):
    now = dt.datetime(2026, 10, 8, 14, tzinfo=UTC)
    start = now - dt.timedelta(days=30)  # crosses no DST change, so offsets stay put
    bridge = FakeBridge(start)
    bridge.now = now
    ing = _ingestor(bridge, factory, tmp_path)
    await ing.bars_forward("S&P.fs", now)  # first run: the last week only
    with factory() as s:
        first = s.scalar(sa.select(sa.func.min(Bar.ts)))
    assert first == now - CHUNK
    assert await ing.bars_backward("S&P.fs", now) is True
    with factory() as s:
        assert s.scalar(sa.select(sa.func.min(Bar.ts))) == start
        assert s.scalar(sa.select(sa.func.count()).select_from(Bar)) == _open_minutes(start, now)


async def test_backfill_across_us_dst_change_keeps_bars_contiguous(factory, tmp_path):
    now = dt.datetime(2026, 11, 3, 15, tzinfo=UTC)  # US DST ended 2026-11-01
    bridge = FakeBridge(now - dt.timedelta(days=4))
    bridge.now = now
    ing = _ingestor(bridge, factory, tmp_path)
    await ing.bars_forward("S&P.fs", now)
    with factory() as s:
        rows = s.execute(sa.select(Bar.ts, Bar.offset_s).order_by(Bar.ts)).all()
    stamps = [r.ts for r in rows]
    gaps = [b - a for a, b in itertools.pairwise(stamps) if b - a != dt.timedelta(minutes=1)]
    # One gap, the weekend: Friday 16:59 EDT to Sunday 18:00 EST is 49h01m of New York wall
    # time plus the hour the clocks went back. A conversion that ignored DST would show 49h01m.
    assert gaps == [dt.timedelta(hours=50, minutes=1)]
    assert len(rows) == _open_minutes(stamps[0], now)
    assert {r.offset_s for r in rows} == {2 * 3600, 3 * 3600}


async def test_a_changed_closed_bar_is_updated_and_logged(factory, tmp_path, caplog):
    now = dt.datetime(2026, 10, 8, 14, tzinfo=UTC)
    bridge = FakeBridge(now - dt.timedelta(hours=1))
    bridge.now = now
    ing = _ingestor(bridge, factory, tmp_path)
    await ing.bars_forward("S&P.fs", now)
    with factory() as s:
        last = s.execute(sa.select(Bar).order_by(Bar.ts.desc()).limit(1)).scalar_one()
        last.close = 1.0
        s.commit()
    with caplog.at_level(logging.WARNING):
        await ing.bars_forward("S&P.fs", now)  # re-reads the newest stored bar
    assert "changed" in caplog.text
    with factory() as s:
        assert s.execute(sa.select(Bar.close).order_by(Bar.ts.desc()).limit(1)).scalar() != 1.0


# --------------------------------------------------------------------------- specs and ticks


async def test_specs_add_a_row_only_on_change(factory, tmp_path):
    bridge = FakeBridge(dt.datetime(2026, 10, 1, tzinfo=UTC))
    ing = _ingestor(bridge, factory, tmp_path)
    t0 = dt.datetime(2026, 10, 8, 14, tzinfo=UTC)
    assert await ing.spec("S&P.fs", t0) is True
    assert await ing.spec("S&P.fs", t0 + dt.timedelta(hours=1)) is False
    bridge.spec_value["swap_long"] = -1.5
    assert await ing.spec("S&P.fs", t0 + dt.timedelta(hours=2)) is True
    with factory() as s:
        assert [r.spec["swap_long"] for r in s.execute(
            sa.select(SymbolSpec).order_by(SymbolSpec.observed_at)).scalars()] == [-1.0, -1.5]


async def test_ticks_are_written_per_closed_hour_once(factory, tmp_path):
    now = dt.datetime(2026, 10, 8, 14, 30, tzinfo=UTC)
    bridge = FakeBridge(now - dt.timedelta(days=1))
    ing = _ingestor(bridge, factory, tmp_path, tick_hours=3)
    assert await ing.ticks("S&P.fs", now) == 18
    assert await ing.ticks("S&P.fs", now) == 0  # already indexed
    with factory() as s:
        files = s.execute(sa.select(TickFile).order_by(TickFile.hour)).scalars().all()
    assert [f.hour.hour for f in files] == [11, 12, 13]
    frame = pd.read_parquet(tmp_path / files[-1].path)
    assert frame["ts"].iloc[0] == pd.Timestamp("2026-10-08 13:00", tz="UTC")
    assert "s-p.fs" in files[-1].path
