"""Tests for the gamma-level forward record (T142, T153): the pure detectors and path in
`app/modules/gex/scan/level_events.py`, and the job and alerts in
`app/modules/gex/jobs/level_events.py`. Offline: a stored fixture of real desk rows, synthetic
sessions for the wall bounce, and SQLite for the job."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.broker.tables import Bar as BrokerBar
from app.modules.broker.tables import Base as BrokerBase
from app.modules.gex.jobs import level_events as job
from app.modules.gex.models.db import (
    Base,
    GexLevel,
    IntradayBar,
    LevelEvent,
    LevelEventScore,
    Snapshot,
)
from app.modules.gex.scan.level_events import (
    KEY_CALL_BOUNCE,
    KEY_DOWN,
    KEY_PUT_BOUNCE,
    KEY_UP,
    Capture,
    detect_crosses,
    detect_wall_bounces,
    excursion_path,
    horizon_returns,
    minute_points,
    path_unit,
)
from app.modules.gex.scan.level_events import (
    LevelEvent as Event,
)

UTC = dt.UTC
FIXTURE = Path(__file__).parent / "fixtures" / "flip_cross_2026-10-08.json"


@dataclass(frozen=True)
class M:
    ts: dt.datetime
    open: float
    high: float
    low: float
    close: float


def _bar(ts: dt.datetime, close: float, low: float | None = None, high: float | None = None) -> M:
    return M(ts, close, close if high is None else high, close if low is None else low, close)


def _at(day: str, hhmm: str) -> dt.datetime:
    fmt = "%Y-%m-%d %H:%M:%S" if hhmm.count(":") == 2 else "%Y-%m-%d %H:%M"
    return dt.datetime.strptime(f"{day} {hhmm}", fmt).replace(tzinfo=UTC)


def _fixture():
    data = json.loads(FIXTURE.read_text())
    day = data["session"]
    cols = data["bars"]["cols"]
    bars = {sym: [_bar(_at(day, r[0]), r[cols.index(sym)]) for r in data["bars"]["rows"]] for sym in cols[1:]}
    caps: dict[str, list[Capture]] = {}
    for t, sym, flip, net in data["captures"]["rows"]:
        caps.setdefault(sym, []).append(Capture(_at(day, t), flip, net_gex=net * 1e6))
    return day, bars, caps, data["expected"]


# --- flip crosses -------------------------------------------------------------------------------


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
    assert first.capture_at == _at(day, "13:45:09") and first.level == 775.334
    assert first.level_kind == "flip" and first.touch_ts is None


def test_bars_before_the_first_capture_never_form_a_cross():
    t0 = _at("2026-10-13", "13:30")
    bars = [_bar(t0 + dt.timedelta(minutes=5 * i), c) for i, c in enumerate([99, 99, 101, 101, 101])]
    caps = [Capture(t0 + dt.timedelta(minutes=20), 100.0)]  # known only from the 13:45 bar's close on
    assert detect_crosses("SPY", bars, caps) == []
    (cross,) = detect_crosses("SPY", bars, [Capture(t0, 100.0)])
    assert cross.key == KEY_UP and cross.bar_ts == t0 + dt.timedelta(minutes=15)


def test_a_capture_without_a_flip_does_not_hide_the_last_known_flip():
    t0 = _at("2026-10-13", "13:30")
    bars = [_bar(t0 + dt.timedelta(minutes=5 * i), c) for i, c in enumerate([99, 101, 101])]
    caps = [Capture(t0, 100.0), Capture(t0 + dt.timedelta(minutes=1), None, put_wall=95.0)]
    assert [c.key for c in detect_crosses("SPY", bars, caps)] == [KEY_UP]


def test_a_close_exactly_on_the_flip_breaks_the_run():
    t0 = _at("2026-10-13", "14:00")
    bars = [_bar(t0 + dt.timedelta(minutes=5 * i), c) for i, c in enumerate([101, 100, 99, 99])]
    assert detect_crosses("SPY", bars, [Capture(t0, 100.0)]) == []


# --- wall bounces -------------------------------------------------------------------------------


def _session(t0, spec):
    """`spec` rows are (close, low, high); None keeps the extreme at the close."""
    return [_bar(t0 + dt.timedelta(minutes=5 * i), c, lo, hi) for i, (c, lo, hi) in enumerate(spec)]


def test_a_put_wall_touch_then_two_higher_closes_is_a_bounce():
    t0 = _at("2026-10-13", "14:00")
    bars = _session(t0, [
        (771.8, None, None),
        (770.9, 770.2, None),  # touch: low within 0.05 % of 770, closes back above
        (771.3, None, None),   # away 1
        (771.6, None, None),   # away 2 -> event here
        (772.0, None, None),
    ])
    (ev,) = detect_wall_bounces("SPY", bars, [Capture(t0, 775.0, call_wall=780.0, put_wall=770.0)])
    assert ev.key == KEY_PUT_BOUNCE and ev.level == 770.0 and ev.level_kind == "put_wall"
    assert ev.touch_ts == t0 + dt.timedelta(minutes=5)
    assert ev.bar_ts == t0 + dt.timedelta(minutes=15) and ev.spot == 771.6


def test_a_call_wall_rejection_is_the_mirror_image():
    t0 = _at("2026-10-13", "14:00")
    bars = _session(t0, [(778.5, None, None), (779.4, None, 780.1), (779.0, None, None), (778.6, None, None)])
    (ev,) = detect_wall_bounces("SPY", bars, [Capture(t0, 775.0, call_wall=780.0, put_wall=770.0)])
    assert ev.key == KEY_CALL_BOUNCE and ev.level == 780.0


@pytest.mark.parametrize("spec, why", [
    ([(771.8, None, None), (771.0, 770.5, None), (771.3, None, None), (771.6, None, None)],
     "low 0.5 away is outside 0.05 % (0.385)"),
    ([(771.8, None, None), (769.9, 769.5, None), (771.3, None, None), (771.6, None, None)],
     "the touch bar closed below the wall"),
    ([(769.5, None, None), (770.9, 770.2, None), (771.3, None, None), (771.6, None, None)],
     "price came from below the put wall, so it is a reclaim, not a bounce"),
    ([(771.8, None, None), (770.9, 770.2, None), (771.3, None, None), (771.2, None, None)],
     "the second close away did not go further"),
])
def test_what_is_not_a_bounce(spec, why):
    t0 = _at("2026-10-13", "14:00")
    caps = [Capture(t0, 775.0, call_wall=780.0, put_wall=770.0)]
    assert detect_wall_bounces("SPY", _session(t0, spec), caps) == [], why


def test_one_touch_sequence_is_one_event():
    t0 = _at("2026-10-13", "14:00")
    bars = _session(t0, [
        (771.8, None, None), (770.9, 770.2, None), (771.0, 770.3, None), (771.3, None, None),
        (771.6, None, None),
    ])
    events = detect_wall_bounces("SPY", bars, [Capture(t0, 775.0, put_wall=770.0)])
    assert len(events) == 1


def test_the_wall_must_be_known_at_the_touch():
    t0 = _at("2026-10-13", "14:00")
    bars = _session(t0, [(771.8, None, None), (770.9, 770.2, None), (771.3, None, None), (771.6, None, None)])
    later = [Capture(t0 + dt.timedelta(minutes=11), 775.0, put_wall=770.0)]  # after the touch closed
    assert detect_wall_bounces("SPY", bars, later) == []


# --- returns and the excursion path --------------------------------------------------------------


def test_horizon_returns_are_signed_with_the_event_and_null_without_a_bar():
    t0 = _at("2026-10-13", "14:00")
    bars = [_bar(t0 + dt.timedelta(minutes=5 * i), 100.0 - i) for i in range(8)]
    ev = Event("SPY", t0, KEY_DOWN, 100.0, 100.5, "flip", None, t0)
    rets, r_close = horizon_returns(ev, bars)
    assert rets[15] == pytest.approx(300.0)  # fell 3 %, a down-cross that followed through
    assert rets[30] == pytest.approx(600.0)
    assert rets[60] is None  # no bar 60 minutes later: unknown, not zero
    assert r_close == pytest.approx(700.0)
    bounce = Event("SPY", t0, KEY_PUT_BOUNCE, 100.0, 99.0, "put_wall", None, t0)
    assert horizon_returns(bounce, bars)[0][15] == pytest.approx(-300.0)  # a put bounce points up


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
    # a down event: falling price is favourable. Up bar low first, down bar high first.
    assert pts == [(0.0, 1.0), (0.5, -3.0), (1.0, -4.0), (1.5, 2.0)]


def test_path_unit_is_half_the_median_five_minute_range():
    t0 = _at("2026-10-13", "14:00")
    bars = [M(t0 + dt.timedelta(minutes=i), 0, 1 + (i // 5), 0, 0) for i in range(15)]
    assert path_unit(bars) == pytest.approx(1.0)  # ranges 1, 2, 3 -> median 2 -> half is 1
    assert path_unit([]) is None


# --- alert selection ------------------------------------------------------------------------------


def _ev(symbol, hhmm, key=KEY_DOWN, day="2026-10-13"):
    bar_ts = _at(day, hhmm) - dt.timedelta(minutes=5)
    return Event(symbol, bar_ts, key, 100.0, 100.5, "flip", None, bar_ts)


def test_alerts_are_fresh_in_window_and_one_per_family():
    now = _at("2026-10-13", "15:02")  # 11:02 ET
    picked = job.select_alerts([
        _ev("SPX", "15:00"), _ev("SPY", "15:00"),       # same family, same bar: SPX only
        _ev("DIA", "15:00"),                             # US30 suppressed by the US500 move
        _ev("QQQ", "15:00", KEY_UP),                     # its own family
        _ev("GLD", "14:40"),                             # 22 minutes old: caught up, not live
    ], [], now)
    assert {s for s, _, _ in picked} == {"SPX", "QQQ"}


def test_an_earlier_alert_suppresses_the_family_for_an_hour_but_not_another_key():
    now = _at("2026-10-13", "15:32")
    earlier = [("SPY", KEY_DOWN, _at("2026-10-13", "15:00"))]
    picked = job.select_alerts([_ev("SPX", "15:30"), _ev("SPX", "15:30", KEY_UP)], earlier, now)
    assert picked == {("SPX", _at("2026-10-13", "15:25"), KEY_UP)}


def test_dia_alone_is_alerted_and_entries_outside_the_window_are_not():
    assert job.select_alerts([_ev("DIA", "15:00")], [], _at("2026-10-13", "15:01"))
    late = _at("2026-10-13", "19:10")  # 15:10 ET, after the 15:00 cutoff
    assert job.select_alerts([_ev("SPX", "19:10")], [], late) == set()


def test_alert_text_names_the_cfd_level_and_the_stage():
    ev = Event("SPY", _at("2026-10-13", "14:10"), KEY_PUT_BOUNCE, 771.6, 770.0, "put_wall", -2e9,
               _at("2026-10-13", "14:00"), touch_ts=_at("2026-10-13", "14:00"))
    text = job.alert_text(ev, cfd_price=7800.0)
    assert text.splitlines()[0] == "🟢 PUT WALL BOUNCE · S&P.fs (SPY)"
    assert "S&P.fs 7,800.00 · put wall ≈ 7,783.83" in text  # ratio method: 770 × 7800/771.6
    assert "net GEX -2.00bn (short gamma)" in text
    assert "untested" in text
    assert "price not available" in job.alert_text(ev, cfd_price=None)


# --- the job ---------------------------------------------------------------------------------------


@pytest.fixture
def factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'f.db'}")
    Base.metadata.create_all(engine)
    BrokerBase.metadata.create_all(engine)
    yield get_sessionmaker(engine)
    engine.dispose()


def _store(factory, session_date, bars_by_symbol, caps_by_symbol):
    with factory() as s:
        for sym, bars in bars_by_symbol.items():
            for b in bars:
                s.add(IntradayBar(symbol=sym, interval="5m", ts=b.ts, open=b.open, high=b.high,
                                  low=b.low, close=b.close, volume=None, source="test"))
        for sym, caps in caps_by_symbol.items():
            for c in caps:
                snap = Snapshot(underlying=sym, captured_at=c.captured_at, source="test", spot=0.0,
                                contract_count=1, parquet_path="x", is_eod=False, session_date=session_date)
                s.add(snap)
                s.flush()
                s.add(GexLevel(snapshot_id=snap.id, filter="ALL", net_gex=c.net_gex, spot=0.0,
                               flip_point=c.flip, call_wall=c.call_wall, put_wall=c.put_wall,
                               computed_at=c.captured_at))
        s.commit()


def _load_fixture_day(factory, symbols=("SPY", "SPX")):
    day, bars, caps, _ = _fixture()
    _store(factory, dt.date.fromisoformat(day), {s: bars[s] for s in symbols}, {s: caps[s] for s in symbols})


class Outbox(list):
    def __call__(self, message):
        self.append(message)


def test_record_inserts_only_closed_bars_never_twice_and_alerts_only_fresh(factory):
    _load_fixture_day(factory)
    sent = Outbox()
    first = job.record_level_events(now=_at("2026-10-08", "14:12"), session_factory=factory, send=sent)
    assert [(r.symbol, r.entry_at.strftime("%H:%M"), r.key, r.alerted) for r in first] == [
        ("SPY", "14:00", KEY_DOWN, False),  # 12 minutes old by now: logged, not alerted
        ("SPY", "14:10", KEY_UP, True),
    ]
    assert first[0].spx_side == 1 and first[0].in_window is True
    assert len(sent) == 1 and sent[0].startswith("🔺 FLIP CROSS UP · S&P.fs (SPY)")
    later = job.record_level_events(now=_at("2026-10-08", "20:06"), session_factory=factory, send=sent)
    assert [r.entry_at.strftime("%H:%M") for r in later] == ["15:00", "16:35"]
    assert len(sent) == 1  # both caught up long after their bars: recorded, never alerted
    assert job.record_level_events(now=_at("2026-10-08", "20:06"), session_factory=factory, send=sent) == []


def test_record_reads_today_only_so_it_cannot_backfill(factory):
    _load_fixture_day(factory)
    assert job.record_level_events(now=_at("2026-10-09", "15:00"), session_factory=factory, send=Outbox()) == []


def test_a_live_wall_bounce_is_stored_and_alerted_with_the_cfd_price(factory):
    t0 = _at("2026-10-13", "14:00")
    bars = _session(t0, [(771.8, None, None), (770.9, 770.2, None), (771.3, None, None), (771.6, None, None)])
    _store(factory, dt.date(2026, 10, 13), {"SPY": bars},
           {"SPY": [Capture(t0 - dt.timedelta(minutes=10), 775.0, call_wall=780.0, put_wall=770.0)]})
    _add_cfd_minutes(factory, t0, t0 + dt.timedelta(minutes=20), price=7800.0)
    sent = Outbox()
    (row,) = job.record_level_events(now=t0 + dt.timedelta(minutes=21), session_factory=factory, send=sent)
    assert row.key == KEY_PUT_BOUNCE and row.alerted and row.touch_ts == t0 + dt.timedelta(minutes=5)
    assert len(sent) == 1 and "S&P.fs 7,800.00 · put wall ≈" in sent[0]


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
    job.record_level_events(now=_at("2026-10-08", "20:06"), session_factory=factory, send=Outbox())
    assert job.score_level_events(now=_at("2026-10-08", "19:00"), session_factory=factory) == []
    # session over, but no CFD minutes yet: wait rather than write a pathless score
    assert job.score_level_events(now=_at("2026-10-08", "20:40"), session_factory=factory) == []

    _add_cfd_minutes(factory, _at("2026-10-01", "13:30"), _at("2026-10-01", "20:00"))  # unit history
    _add_cfd_minutes(factory, _at("2026-10-08", "13:30"), _at("2026-10-08", "20:00"))
    written = job.score_level_events(now=_at("2026-10-08", "20:41"), session_factory=factory)
    assert len(written) == 4
    row = next(r for r in written if r.bar_ts == _at("2026-10-08", "13:55"))
    assert row.ret_15m_bps is not None and row.ret_close_bps is not None
    assert row.cfd_symbol == "S&P.fs" and row.path_unit == pytest.approx(1.0)
    assert json.loads(row.path_swings) and row.path_note is None
    assert row.path_end_at == _at("2026-10-08", "20:00")
    assert job.score_level_events(now=_at("2026-10-09", "20:41"), session_factory=factory) == []


def test_after_the_wait_a_missing_path_is_null_with_a_reason(factory):
    _load_fixture_day(factory)
    job.record_level_events(now=_at("2026-10-08", "20:06"), session_factory=factory, send=Outbox())
    written = job.score_level_events(now=_at("2026-10-12", "21:00"), session_factory=factory)
    assert len(written) == 4
    for r in written:
        assert r.path_swings is None and r.path_mae is None
        assert r.path_note == "no CFD bar at entry"
        assert r.ret_close_bps is not None
    with factory() as s:
        assert s.query(LevelEventScore).count() == 4 and s.query(LevelEvent).count() == 4


# --- wiring ---------------------------------------------------------------------------------------


def test_the_score_job_is_registered_with_the_intraday_bars(monkeypatch):
    from app.core.config import settings
    from app.modules.gex.jobs.scheduler import LEVEL_EVENT_SCORE_JOB_ID, build_scheduler

    monkeypatch.setattr(settings, "INTRADAY_BARS_ENABLED", True)
    j = build_scheduler().get_job(LEVEL_EVENT_SCORE_JOB_ID)
    assert j is not None and j.misfire_grace_time is None
    monkeypatch.setattr(settings, "INTRADAY_BARS_ENABLED", False)
    assert build_scheduler().get_job(LEVEL_EVENT_SCORE_JOB_ID) is None


def test_the_hourly_digest_is_off_by_default():
    from app.core.config import Settings

    assert Settings.model_fields["SIGNALS_HOURLY_CRON"].default == "off"


def test_the_read_tool_is_registered():
    from app.mcp.server import gex_level_events

    assert callable(gex_level_events)
