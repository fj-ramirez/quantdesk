"""T165: the pure parts of the intraday history loader."""

from __future__ import annotations

import datetime as dt

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
