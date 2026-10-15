"""T165: the pure parts of the intraday history loader."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.modules.gex.gex import intraday_history as ih
from app.modules.gex.gex.intraday_history import bars_from_minutes


def _minutes(start: str, prices: list[float]) -> list[dict[str, str]]:
    t0 = dt.datetime.fromisoformat(start)
    return [{"timestamp": (t0 + dt.timedelta(minutes=i)).isoformat(timespec="milliseconds"),
             "underlying_price": str(p)} for i, p in enumerate(prices)]


def test_a_bar_closes_at_the_sample_stamped_with_its_close():
    # 09:30 .. 09:40, eleven samples: the 09:30 bar is 09:30..09:35, the 09:35 bar 09:35..09:40
    bars = bars_from_minutes("SPY", _minutes("2026-10-01T09:30:00", [10, 11, 9, 12, 13, 14, 15, 16, 8, 17, 18]))
    first, second = bars
    assert first["ts"] == dt.datetime(2026, 10, 1, 13, 30, tzinfo=dt.UTC)
    assert (first["open"], first["high"], first["low"], first["close"]) == (10, 14, 9, 14)
    assert (second["open"], second["high"], second["low"], second["close"]) == (14, 18, 8, 18)


def test_a_bar_without_its_closing_sample_is_not_written_and_zero_is_absent():
    # SPX prints 0 at 09:30 before the index opens; the last bar has no 09:40 sample
    bars = bars_from_minutes("SPX", _minutes("2026-10-01T09:30:00", [0, 11, 9, 12, 13, 14, 15, 16, 8, 17]))
    assert len(bars) == 0  # 09:30 has no open sample, 09:35 has no close sample


def test_bars_stop_at_the_close():
    bars = bars_from_minutes("SPY", _minutes("2026-10-01T15:50:00", list(range(1, 13))))
    assert [b["ts"].astimezone(dt.timezone(dt.timedelta(hours=-4))).strftime("%H:%M") for b in bars] == ["15:50", "15:55"]


def test_compact_rows_round_trip_into_what_assemble_reads():
    from app.modules.gex.gex.intraday_history import _rows, compact

    raw = [{"symbol": "SPXW", "expiration": "2026-10-01", "strike": "7655.000", "right": "CALL",
            "timestamp": "2026-10-01T10:00:00.000", "bid": "4.1", "ask": "4.3", "delta": "0.5",
            "theta": "-1.2", "vega": "0.8", "implied_vol": "0.14", "underlying_price": "7655.3",
            "rho": "0.01", "iv_error": "0.0"},
           {"symbol": "SPXW", "expiration": "2026-10-01", "strike": "7660.000", "right": "PUT",
            "timestamp": "2026-10-01T10:00:00.000", "bid": "", "ask": "x", "delta": "-0.4",
            "theta": "-1.1", "vega": "0.7", "implied_vol": "0.15", "underlying_price": "7655.3"}]
    (root, first), (_, second) = _rows(compact("SPXW", raw))
    assert root == "SPXW" and "rho" not in first
    assert float(first["strike"]) == 7655.0 and first["right"] == "CALL" and float(first["bid"]) == 4.1
    assert second["bid"] == "" and second["ask"] == ""  # missing or unparseable -> empty, never "nan"


# --- T166: the run window and the pipelined loop --------------------------------------------------

NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize("when, expected_min", [
    (dt.datetime(2026, 10, 14, 9, 14, tzinfo=NY), 0),       # Wednesday, before the window closes
    (dt.datetime(2026, 10, 14, 9, 15, tzinfo=NY), 465),     # capture hours start: wait to 17:00
    (dt.datetime(2026, 10, 14, 16, 30, tzinfo=NY), 30),     # covers the 16:20 and 16:45 captures
    (dt.datetime(2026, 10, 14, 17, 0, tzinfo=NY), 0),
    (dt.datetime(2026, 10, 17, 12, 0, tzinfo=NY), 0),       # Saturday
])
def test_the_backfill_never_shares_the_terminal_with_capture_hours(when, expected_min):
    assert ih.seconds_until_window(when) == pytest.approx(expected_min * 60)


def _args(tmp_path: Path, **kw) -> argparse.Namespace:
    base = {"symbols": "SPY", "start": dt.date(2026, 10, 5), "end": dt.date(2026, 10, 9), "out": str(tmp_path),
            "max_dte": None, "not_before": None, "ignore_hours": True}
    return argparse.Namespace(**{**base, **kw})


def test_every_missing_session_is_loaded_once_and_a_failure_does_not_stop_the_run(tmp_path, monkeypatch):
    calls: list[tuple[dt.date, bool, bool]] = []

    async def fake_fetch(client, underlying, day, cache, *, max_dte, need_levels, need_bars):
        calls.append((day, need_levels, need_bars))
        if day == dt.date(2026, 10, 7):
            raise RuntimeError("terminal hiccup")
        return ih.Fetched(day, None, [], [{"symbol": "SPY", "ts": dt.datetime(2026, 10, 5, tzinfo=dt.UTC),
                                            "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}],
                          {"requests": 1, "rows": 0})

    class FakeClient:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): ...

    monkeypatch.setattr(ih, "fetch_session", fake_fetch)
    monkeypatch.setattr(ih, "ThetaDataClient", FakeClient)
    (tmp_path / "SPY").mkdir()
    for name in ("2026-10-05.levels.parquet", "2026-10-05.bars.parquet", "2026-10-06.bars.parquet"):
        (tmp_path / "SPY" / name).write_bytes(b"")  # 10-05 complete, 10-06 has bars only

    failed = asyncio.run(ih._run(_args(tmp_path)))

    assert [c[0].day for c in calls] == [6, 7, 8, 9]
    assert calls[0] == (dt.date(2026, 10, 6), True, False)  # only what is missing is fetched
    assert failed == 1
    assert not (tmp_path / "SPY" / "2026-10-07.levels.parquet").exists()  # retried on the next run
    assert (tmp_path / "SPY" / "2026-10-09.levels.parquet").exists()


def test_the_end_date_must_be_before_today(tmp_path):
    with pytest.raises(SystemExit, match="before today"):
        asyncio.run(ih._run(_args(tmp_path, end=dt.datetime.now(NY).date())))
