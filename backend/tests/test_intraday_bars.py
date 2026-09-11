"""Tests for T74: intraday bars (provider split, upsert convergence, session aggregation).

The behaviour worth pinning here is the part a future change could break with everything else
still green: that the vendor's trailing live-quote row never becomes a stored bar, that
re-polling a forming bucket corrects it rather than duplicating it, and that the derived session
bar can never be mistaken for a settled daily bar.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.jobs.intraday_bars import session_daily_bar, update_one_symbol_intraday
from app.models.bars import IntradayBar as IntradayBarIn
from app.models.db import Base, DailyBar, IntradayBar, get_engine, get_sessionmaker
from app.providers.yahoo import YahooBarProvider
from app.storage.bars_repository import read_intraday_bars, upsert_intraday_bars

# 2026-09-11 13:30:00Z == 09:30 ET, the session open. Buckets every 300 s.
OPEN_EPOCH = 1789133400


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'intraday.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def _payload(*, buckets: int = 3, with_quote: bool = True, quote_offset: int = 137):
    """A chart body shaped like the one measured live on 2026-09-11.

    `with_quote` appends the unaligned trailing live-quote row the vendor adds during a
    session: an epoch that is *not* a multiple of the interval, and `volume` 0.
    """
    timestamps = [OPEN_EPOCH + i * 300 for i in range(buckets)]
    opens = [100.0 + i for i in range(buckets)]
    highs = [101.0 + i for i in range(buckets)]
    lows = [99.0 + i for i in range(buckets)]
    closes = [100.5 + i for i in range(buckets)]
    volumes = [1000 + i for i in range(buckets)]

    if with_quote:
        timestamps.append(OPEN_EPOCH + (buckets - 1) * 300 + quote_offset)
        opens.append(None)
        highs.append(None)
        lows.append(None)
        closes.append(123.45)
        volumes.append(0)

    return {
        "chart": {
            "result": [
                {
                    "meta": {"exchangeTimezoneName": "America/New_York"},
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": opens,
                                "high": highs,
                                "low": lows,
                                "close": closes,
                                "volume": volumes,
                            }
                        ]
                    },
                }
            ]
        }
    }


# --- the provider split -----------------------------------------------------------------------


def test_the_trailing_quote_row_is_not_a_bar():
    """The core rule. Storing it would put a zero-volume bucket at a ragged timestamp in the
    middle of every session."""
    provider = YahooBarProvider()
    bars, quote = provider._parse_intraday_payload("SPY", _payload(), interval="5m")

    assert len(bars) == 3
    assert all(b.ts.timestamp() % 300 == 0 for b in bars)
    assert quote is not None
    assert quote.price == 123.45
    assert quote.ts.timestamp() % 300 != 0


def test_no_quote_row_is_not_an_error():
    """Outside a session the vendor publishes buckets only."""
    provider = YahooBarProvider()
    bars, quote = provider._parse_intraday_payload(
        "SPY", _payload(with_quote=False), interval="5m"
    )
    assert len(bars) == 3
    assert quote is None


def test_an_unaligned_row_is_rejected_wherever_it_appears():
    """Checked per row rather than positionally, so a vendor that ever inserts a quote row
    mid-payload cannot smuggle it into the series."""
    provider = YahooBarProvider()
    payload = _payload(buckets=3, with_quote=False)
    result = payload["chart"]["result"][0]
    result["timestamp"].insert(1, OPEN_EPOCH + 137)
    for key in ("open", "high", "low", "close", "volume"):
        result["indicators"]["quote"][0][key].insert(1, 1.0)

    bars, quote = provider._parse_intraday_payload("SPY", payload, interval="5m")

    assert len(bars) == 3
    assert quote is not None  # the misplaced row was routed to the quote, not dropped silently


def test_bars_are_utc_and_ascending():
    provider = YahooBarProvider()
    bars, _ = provider._parse_intraday_payload("SPY", _payload(buckets=4), interval="5m")
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)
    assert all(b.ts.tzinfo is dt.UTC for b in bars)


def test_an_unknown_interval_fails_loudly():
    """Guessing a divisor would silently misclassify every row as aligned or unaligned."""
    provider = YahooBarProvider()
    with pytest.raises(ValueError, match="unsupported intraday interval"):
        provider._parse_intraday_payload("SPY", _payload(), interval="7m")


def test_null_ohlc_rows_are_skipped_not_interpolated():
    provider = YahooBarProvider()
    payload = _payload(buckets=3, with_quote=False)
    payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None

    bars, _ = provider._parse_intraday_payload("SPY", payload, interval="5m")

    assert len(bars) == 2


# --- upsert convergence -----------------------------------------------------------------------


def _bar(i: int, *, close: float, volume: int | None = 1000) -> IntradayBarIn:
    return IntradayBarIn(
        symbol="SPY",
        interval="5m",
        ts=dt.datetime.fromtimestamp(OPEN_EPOCH + i * 300, dt.UTC),
        open=100.0,
        high=101.0,
        low=99.0,
        close=close,
        volume=volume,
        source="yahoo-splitadj",
    )


def test_repolling_a_forming_bucket_corrects_it_rather_than_duplicating(session_factory):
    """The mechanism that makes the series continuous: at 15:14 the 15:10 bucket is partial, and
    the 15:19 poll must overwrite it, not append beside it."""
    first = upsert_intraday_bars([_bar(0, close=100.5)], session_factory=session_factory)
    second = upsert_intraday_bars([_bar(0, close=102.75)], session_factory=session_factory)

    assert (first.inserted, first.updated) == (1, 0)
    assert (second.inserted, second.updated) == (0, 1)

    rows = read_intraday_bars("SPY", interval="5m", session_factory=session_factory)
    assert len(rows) == 1
    assert rows[0].close == 102.75


def test_the_same_instant_at_a_different_interval_is_a_different_bucket(session_factory):
    """Interval is part of the key: a 5m and a 15m bucket can legitimately open together."""
    upsert_intraday_bars([_bar(0, close=100.5)], session_factory=session_factory)
    fifteen = _bar(0, close=100.5).model_copy(update={"interval": "15m"})
    upsert_intraday_bars([fifteen], session_factory=session_factory)

    with session_factory() as session:
        assert len(session.execute(select(IntradayBar)).scalars().all()) == 2


def test_upserting_nothing_is_not_an_error(session_factory):
    result = upsert_intraday_bars([], session_factory=session_factory)
    assert (result.inserted, result.updated) == (0, 0)


def test_the_job_never_writes_to_daily_bars(session_factory):
    """The separation this whole design rests on -- a partial row in `daily_bars` would poison
    ATR, realized vol and every breakout level."""
    upsert_intraday_bars([_bar(i, close=100.0 + i) for i in range(5)], session_factory=session_factory)
    with session_factory() as session:
        assert session.execute(select(DailyBar)).scalars().all() == []


# --- the derived session bar -------------------------------------------------------------------


def test_session_bar_aggregates_todays_buckets(session_factory):
    bars = [
        _bar(0, close=100.5).model_copy(update={"open": 100.0, "high": 101.0, "low": 99.0}),
        _bar(1, close=103.0).model_copy(update={"open": 100.6, "high": 104.0, "low": 100.0}),
        _bar(2, close=102.0).model_copy(update={"open": 103.0, "high": 103.5, "low": 98.0}),
    ]
    upsert_intraday_bars(bars, session_factory=session_factory)

    day = dt.datetime.fromtimestamp(OPEN_EPOCH, dt.UTC).date()
    session_bar = session_daily_bar("SPY", day=day, interval="5m", session_factory=session_factory)

    assert session_bar is not None
    assert session_bar.open == 100.0  # first bucket's open
    assert session_bar.high == 104.0
    assert session_bar.low == 98.0
    assert session_bar.close == 102.0  # last bucket's close
    assert session_bar.volume == 3000


def test_session_bar_prefers_the_live_quote_for_close(session_factory):
    """The quote is up to one interval fresher than the newest bucket's close."""
    from app.models.bars import LiveQuote

    upsert_intraday_bars([_bar(0, close=100.5)], session_factory=session_factory)
    day = dt.datetime.fromtimestamp(OPEN_EPOCH, dt.UTC).date()
    quote = LiveQuote(
        symbol="SPY",
        ts=dt.datetime.fromtimestamp(OPEN_EPOCH + 137, dt.UTC),
        price=123.45,
        source="yahoo-splitadj",
    )

    session_bar = session_daily_bar(
        "SPY", day=day, interval="5m", session_factory=session_factory, live_quote=quote
    )

    assert session_bar.close == 123.45


def test_session_bar_is_labelled_so_it_cannot_pass_as_a_settled_daily_bar(session_factory):
    upsert_intraday_bars([_bar(0, close=100.5)], session_factory=session_factory)
    day = dt.datetime.fromtimestamp(OPEN_EPOCH, dt.UTC).date()
    session_bar = session_daily_bar("SPY", day=day, interval="5m", session_factory=session_factory)
    assert session_bar.interval == "1d-live"


def test_session_bar_volume_is_none_when_no_bucket_published_any(session_factory):
    """`^VIX` never reports volume. Unknown must not read back as "a session with no trading"."""
    upsert_intraday_bars(
        [_bar(i, close=15.0 + i, volume=None) for i in range(3)], session_factory=session_factory
    )
    day = dt.datetime.fromtimestamp(OPEN_EPOCH, dt.UTC).date()
    session_bar = session_daily_bar("SPY", day=day, interval="5m", session_factory=session_factory)
    assert session_bar.volume is None


def test_session_bar_is_none_before_any_bucket_exists(session_factory):
    """Before the open, on a holiday, or with polling off -- a caller falls back to the last
    settled daily bar and says so, rather than rendering an empty today."""
    assert session_daily_bar("SPY", interval="5m", session_factory=session_factory) is None


# --- the job ------------------------------------------------------------------------------------


class _StubProvider:
    def __init__(self, bars, quote=None, error=None):
        self._bars, self._quote, self._error = bars, quote, error
        self.calls = []

    async def fetch_intraday_bars(self, symbol, *, interval="5m", lookback="1d"):
        self.calls.append((symbol, interval))
        if self._error:
            raise self._error
        return self._bars, self._quote


async def test_one_symbol_poll_reports_insert_and_update_counts(session_factory):
    provider = _StubProvider([_bar(0, close=100.5), _bar(1, close=101.5)])

    first = await update_one_symbol_intraday("SPY", provider=provider, session_factory=session_factory)
    second = await update_one_symbol_intraday("SPY", provider=provider, session_factory=session_factory)

    assert (first.ok, first.bars, first.inserted, first.updated) == (True, 2, 2, 0)
    assert (second.inserted, second.updated) == (0, 2)


async def test_a_provider_failure_returns_a_result_rather_than_raising(session_factory):
    """One symbol's bad poll must never stop the other five."""
    from app.providers.bars import UpstreamUnavailable

    provider = _StubProvider([], error=UpstreamUnavailable("yahoo exploded"))
    result = await update_one_symbol_intraday("SPY", provider=provider, session_factory=session_factory)

    assert result.ok is False
    assert "yahoo exploded" in result.error


async def test_a_non_provider_error_is_also_contained(session_factory):
    provider = _StubProvider([], error=RuntimeError("kaboom"))
    result = await update_one_symbol_intraday("SPY", provider=provider, session_factory=session_factory)
    assert result.ok is False
    assert "RuntimeError" in result.error
