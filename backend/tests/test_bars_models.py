"""Tests for `app/models/bars.py`."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from app.models.bars import DailyBar


def _bar(**overrides) -> dict:
    base = {
        "symbol": "SPY",
        "date": dt.date(2026, 9, 8),
        "open": 650.0,
        "high": 655.0,
        "low": 648.0,
        "close": 652.0,
        "volume": 44_000_000,
        "source": "yahoo-splitadj",
    }
    base.update(overrides)
    return base


def test_valid_bar_round_trips():
    bar = DailyBar(**_bar())
    assert bar.symbol == "SPY"
    assert bar.date == dt.date(2026, 9, 8)
    assert isinstance(bar.date, dt.date)
    assert not isinstance(bar.date, dt.datetime)  # a daily bar has no instant
    assert bar.volume == 44_000_000


def test_zero_volume_is_accepted_and_distinct_from_none():
    """^VIX genuinely trades zero volume -- 0 must survive, not collapse to None."""
    zero = DailyBar(**_bar(symbol="^VIX", volume=0))
    unknown = DailyBar(**_bar(symbol="^VIX", volume=None))
    assert zero.volume == 0
    assert zero.volume is not None
    assert unknown.volume is None


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_missing_price_field_is_rejected(field):
    """No None case exists for price fields -- a provider must skip the row instead."""
    payload = _bar()
    del payload[field]
    with pytest.raises(ValidationError):
        DailyBar(**payload)


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
def test_non_positive_price_field_is_rejected(field):
    payload = _bar(**{field: 0.0})
    with pytest.raises(ValidationError):
        DailyBar(**payload)


def test_negative_volume_is_rejected():
    with pytest.raises(ValidationError):
        DailyBar(**_bar(volume=-1))


def test_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        DailyBar(**_bar(), extra_field="nope")


def test_model_is_frozen():
    bar = DailyBar(**_bar())
    with pytest.raises(ValidationError):
        bar.close = 999.0
