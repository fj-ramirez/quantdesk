"""Tests for the flip-cross forward record (T142): the pure detector and path in
`app/modules/gex/scan/flip_cross.py`, and the job in `app/modules/gex/jobs/flip_crosses.py`.
Offline: a stored fixture of real desk rows, and SQLite for the job."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.broker.tables import Bar as BrokerBar
from app.modules.broker.tables import Base as BrokerBase
from app.modules.gex.jobs import flip_crosses as job
from app.modules.gex.models.db import (
    Base,
    FlipCrossEvent,
    FlipCrossScore,
    GexLevel,
    IntradayBar,
    Snapshot,
)
from app.modules.gex.scan.flip_cross import (
    KEY_DOWN,
    KEY_UP,
    FlipCross,
    detect_crosses,
    excursion_path,
    horizon_returns,
    minute_points,
    path_unit,
)

UTC = dt.UTC
FIXTURE = Path(__file__).parent / "fixtures" / "flip_cross_2026-10-08.json"


@dataclass(frozen=True)
class B:
    ts: dt.datetime
    close: float


@dataclass(frozen=True)
class C:
    captured_at: dt.datetime
    flip: float
    net_gex: float | None = None


@dataclass(frozen=True)
class M:
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float


def _at(day: str, hhmm: str) -> dt.datetime:
    fmt = "%Y-%m-%d %H:%M:%S" if hhmm.count(":") == 2 else "%Y-%m-%d %H:%M"
    return dt.datetime.strptime(f"{day} {hhmm}", fmt).replace(tzinfo=UTC)


def _fixture():
    data = json.loads(FIXTURE.read_text())
    day = data["session"]
    cols = data["bars"]["cols"]
    bars = {sym: [B(_at(day, r[0]), r[cols.index(sym)]) for r in data["bars"]["rows"]] for sym in cols[1:]}
    caps: dict[str, list[C]] = {}
    for t, sym, flip, net in data["captures"]["rows"]:
        caps.setdefault(sym, []).append(C(_at(day, t), flip, net * 1e6))
    return day, bars, caps, data["expected"]


# --- the detector -----------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["SPY", "GLD", "SPX"])
def test_detector_reproduces_the_reference_sql_on_stored_rows(symbol):
    """Acceptance: the events the reference SQL found on 2026-10-08, from the same rows."""
    _day, bars, caps, expected = _fixture()
    got = [
        (c.entry_at.strftime("%H:%M"), "DOWN" if c.key == KEY_DOWN else "UP")
        for c in detect_crosses(symbol, bars[symbol], caps[symbol])
    ]
    assert got == [tuple(e) for e in expected[symbol]]


def test_the_flip_is_the_latest_capture_at_or_before_the_bar_close():
    day, bars, caps, _ = _fixture()
    first = detect_crosses("SPY", bars["SPY"], caps["SPY"])[0]
    # 13:55 bar closes 14:00:00; the 14:00:10 capture is ten seconds too late to count
    assert first.capture_at == _at(day, "13:45:09") and first.flip == 775.334


def test_bars_before_the_first_capture_never_form_a_cross():
    t0 = _at("2026-10-13", "13:30")
    bars = [B(t0 + dt.timedelta(minutes=5 * i), c) for i, c in enumerate([99, 99, 101, 101, 101])]
    caps = [C(t0 + dt.timedelta(minutes=20), 100.0)]  # known only from the 13:45 bar's close on
    assert detect_crosses("SPY", bars, caps) == []
    caps = [C(t0, 100.0)]
    (cross,) = detect_crosses("SPY", bars, caps)
    assert cross.key == KEY_UP and cross.bar_ts == t0 + dt.timedelta(minutes=15)


def test_a_close_exactly_on_the_flip_breaks_the_run():
    t0 = _at("2026-10-13", "14:00")
    bars = [B(t0 + dt.timedelta(minutes=5 * i), c) for i, c in enumerate([101, 100, 99, 99])]
    assert detect_crosses("SPY", bars, [C(t0, 100.0)]) == []


def test_horizon_returns_are_signed_with_the_cross_and_null_without_a_bar():
    t0 = _at("2026-10-13", "14:00")
    bars = [B(t0 + dt.timedelta(minutes=5 * i), 100.0 - i) for i in range(8)]
    cross = FlipCross("SPY", t0, KEY_DOWN, 100.0, 100.5, None, t0, 0)
    rets, r_close = horizon_returns(cross, bars)
    assert rets[15] == pytest.approx(300.0)  # fell 3 %, a down-cross that followed through
    assert rets[30] == pytest.approx(600.0)
    assert rets[60] is None  # no bar 60 minutes later: unknown, not zero
    assert r_close == pytest.approx(700.0)


# --- the excursion path -------------------------------------------------------------------------


def test_the_path_reads_10_against_then_20_with_then_50_against():
    """The user's own example: after entry the trade went 10 against, 20 in favour, 50 against."""
    pts = [(0, -4), (1, -10), (2, 0), (3, 20), (4, 5), (5, -50), (6, -48)]
    exc = excursion_path(pts, unit=5.0)
    assert [x for _, x in exc.swings] == [-10, 20, -50]
    assert (exc.mae, exc.mfe, exc.close) == (-50, 20, -48)


def test_moves_smaller_than_the_unit_are_not_swings():
    exc = excursion_path([(0, 1), (1, -1), (2, 2), (3, 30)], unit=5.0)
    # the -1 dip is under a unit from the entry, so the only swing is the open leg's extreme
    assert [x for _, x in exc.swings] == [30]


def test_minute_points_order_extremes_by_bar_colour_and_sign_by_direction():
    entry_at = _at("2026-10-13", "14:00")
    up = M(entry_at, 100, 103, 99, 102)
    down = M(entry_at + dt.timedelta(minutes=1), 102, 104, 98, 99)
    pts = minute_points([up, down], entry=100.0, direction=-1, entry_at=entry_at)
    # a down-cross: falling price is favourable. Up bar low first, down bar high first.
    assert pts == [(0.0, 1.0), (0.5, -3.0), (1.0, -4.0), (1.5, 2.0)]


def test_path_unit_is_half_the_median_five_minute_range():
    t0 = _at("2026-10-13", "14:00")
    bars = [M(t0 + dt.timedelta(minutes=i), 0, 1 + (i // 5), 0, 0) for i in range(15)]
    assert path_unit(bars) == pytest.approx(1.0)  # ranges 1, 2, 3 -> median 2 -> half is 1
    assert path_unit([]) is None


# --- the job --------------------------------------------------------------------------------------


@pytest.fixture
def factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'f.db'}")
    Base.metadata.create_all(engine)
    BrokerBase.metadata.create_all(engine)
    yield get_sessionmaker(engine)
    engine.dispose()


def _load_fixture_day(factory, symbols=("SPY", "SPX")):
    day, bars, caps, _ = _fixture()
    session_date = dt.date.fromisoformat(day)
    with factory() as s:
        for sym in symbols:
            for b in bars[sym]:
                s.add(IntradayBar(symbol=sym, interval="5m", ts=b.ts, open=b.close, high=b.close,
                                  low=b.close, close=b.close, volume=None, source="test"))
            for c in caps[sym]:
                snap = Snapshot(underlying=sym, captured_at=c.captured_at, source="test", spot=0.0,
                                contract_count=1, parquet_path="x", is_eod=False, session_date=session_date)
                s.add(snap)
                s.flush()
                s.add(GexLevel(snapshot_id=snap.id, filter="ALL", net_gex=c.net_gex, spot=0.0,
                               flip_point=c.flip, computed_at=c.captured_at))
        s.commit()
    return session_date


def test_record_inserts_only_closed_bars_and_never_twice(factory):
    _load_fixture_day(factory)
    mid = _at("2026-10-08", "14:12")  # 14:10's cross needs the 14:05 bar, closed at 14:10
    first = job.record_flip_crosses(now=mid, session_factory=factory)
    assert [(r.symbol, r.entry_at.strftime("%H:%M"), r.key) for r in first] == [
        ("SPY", "14:00", KEY_DOWN), ("SPY", "14:10", KEY_UP),
    ]
    assert first[0].spx_side == 1 and first[0].in_window is True
    later = job.record_flip_crosses(now=_at("2026-10-08", "20:06"), session_factory=factory)
    assert [r.entry_at.strftime("%H:%M") for r in later] == ["15:00", "16:35"]
    assert job.record_flip_crosses(now=_at("2026-10-08", "20:06"), session_factory=factory) == []


def test_record_reads_today_only_so_it_cannot_backfill(factory):
    _load_fixture_day(factory)
    assert job.record_flip_crosses(now=_at("2026-10-09", "15:00"), session_factory=factory) == []


def _add_cfd_minutes(factory, start, end, price=7800.0):
    with factory() as s:
        t = start
        while t < end:
            s.add(BrokerBar(symbol="S&P.fs", timeframe="M1", ts=t, open=price, high=price + 1,
                            low=price - 1, close=price, tick_volume=1, spread=1, real_volume=0,
                            offset_s=0, ingested_at=t))
            t += dt.timedelta(minutes=1)
        s.commit()


def test_scoring_waits_for_the_close_and_the_cfd_then_writes_once(factory):
    _load_fixture_day(factory)
    job.record_flip_crosses(now=_at("2026-10-08", "20:06"), session_factory=factory)
    assert job.score_flip_crosses(now=_at("2026-10-08", "19:00"), session_factory=factory) == []
    # session over, but no CFD minutes yet: wait rather than write a pathless score
    assert job.score_flip_crosses(now=_at("2026-10-08", "20:40"), session_factory=factory) == []

    _add_cfd_minutes(factory, _at("2026-10-01", "13:30"), _at("2026-10-01", "20:00"))  # unit history
    _add_cfd_minutes(factory, _at("2026-10-08", "13:30"), _at("2026-10-08", "20:00"))
    written = job.score_flip_crosses(now=_at("2026-10-08", "20:41"), session_factory=factory)
    assert len(written) == 4
    row = next(r for r in written if r.bar_ts == _at("2026-10-08", "13:55"))
    assert row.ret_15m_bps is not None and row.ret_close_bps is not None
    assert row.cfd_symbol == "S&P.fs" and row.path_unit == pytest.approx(1.0)
    assert json.loads(row.path_swings) and row.path_note is None
    assert row.path_end_at == _at("2026-10-08", "20:00")
    assert job.score_flip_crosses(now=_at("2026-10-09", "20:41"), session_factory=factory) == []


def test_after_the_wait_a_missing_path_is_null_with_a_reason(factory):
    _load_fixture_day(factory)
    job.record_flip_crosses(now=_at("2026-10-08", "20:06"), session_factory=factory)
    written = job.score_flip_crosses(now=_at("2026-10-12", "21:00"), session_factory=factory)
    assert len(written) == 4
    for r in written:
        assert r.path_swings is None and r.path_mae is None
        assert r.path_note == "no CFD bar at entry"
        assert r.ret_close_bps is not None
    with factory() as s:
        assert s.query(FlipCrossScore).count() == 4 and s.query(FlipCrossEvent).count() == 4


# --- wiring ---------------------------------------------------------------------------------------


def test_the_score_job_is_registered_with_the_intraday_bars(monkeypatch):
    from app.core.config import settings
    from app.modules.gex.jobs.scheduler import FLIP_CROSS_SCORE_JOB_ID, build_scheduler

    monkeypatch.setattr(settings, "INTRADAY_BARS_ENABLED", True)
    sched = build_scheduler()
    j = sched.get_job(FLIP_CROSS_SCORE_JOB_ID)
    assert j is not None and j.misfire_grace_time is None
    monkeypatch.setattr(settings, "INTRADAY_BARS_ENABLED", False)
    assert build_scheduler().get_job(FLIP_CROSS_SCORE_JOB_ID) is None


def test_the_read_tool_is_registered():
    from app.mcp.server import gex_flip_crosses

    assert callable(gex_flip_crosses)
