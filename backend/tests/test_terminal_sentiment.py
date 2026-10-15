"""The sentiment composite and the conditions reading (T171).

Offline: `query.get` is replaced by an in-memory point-in-time store that honours `as_of` the
way the real one does (newest vintage per value_date not later than the cutoff), so the tests
exercise the composite's own logic -- orientation, staleness, the insufficient floor, the
synthesis lookup and the as_of exclusion -- against known inputs.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.modules.terminal.analytics import sentiment as sent
from app.modules.terminal.errors import EmptyFetchError

AS_OF = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.UTC)


def _business_days(end: dt.date, n: int) -> list[dt.date]:
    return [d.date() for d in pd.bdate_range(end=end, periods=n)]


def _weekly(end: dt.date, n: int) -> list[dt.date]:
    return [d.date() for d in pd.date_range(end=end, periods=n, freq="7D")]


class Store:
    def __init__(self) -> None:
        self.rows: dict[str, list[tuple[dt.date, float, dt.datetime]]] = {}

    def put(self, sid: str, dates: list[dt.date], values, lag_hours: float = 20) -> None:
        self.rows[sid] = [
            (d, float(v), dt.datetime.combine(d, dt.time(), dt.UTC)
             + dt.timedelta(hours=lag_hours))
            for d, v in zip(dates, values, strict=True)
        ]

    def get(self, conn, sid, start=None, end=None, as_of=None, warn_empty=True):
        rows = [r for r in self.rows.get(sid, [])
                if (start is None or r[0] >= start) and (end is None or r[0] <= end)
                and (as_of is None or r[2] <= as_of)]
        return pd.DataFrame(rows, columns=["value_date", "value", "as_of"])


@pytest.fixture
def store(monkeypatch) -> Store:
    s = Store()
    monkeypatch.setattr(sent, "get", s.get)
    return s


def _fill_all(store: Store, *, last: float = 0.5) -> None:
    """Every component present, each ending at a value placed at `last` of its own range."""
    end = AS_OF.date() - dt.timedelta(days=1)
    rng = np.random.default_rng(7)
    n = 900
    days = _business_days(end, n)
    for sid in ("vol.vix", "vol.vix3m_ratio", "sent.pc.equity", "credit.hy.oas", "eq.spx"):
        base = 100 + np.cumsum(rng.normal(0, 1, n))
        store.put(sid, days, base)
    weeks = _weekly(end, 200)
    store.put("sent.aaii.bull", weeks, rng.uniform(20, 50, 200))
    store.put("sent.aaii.bear", weeks, rng.uniform(20, 50, 200))
    store.put("pos.cot.es.lev_money.pctile", weeks, np.full(200, 100 * last))


def test_bands():
    assert [sent.band(x) for x in (0, 19.9, 20, 45, 60, 79.9, 80, 100)] == [
        "extreme_fear", "extreme_fear", "fear", "neutral", "greed", "greed",
        "extreme_greed", "extreme_greed"]


def test_score_is_the_plain_mean_of_oriented_percentiles(store):
    _fill_all(store)
    r = sent.read_sentiment(None, AS_OF)

    assert r.status == "ok" and r.used == len(sent.COMPONENTS)
    scores = [c.score for c in r.components]
    assert r.score == pytest.approx(np.mean(scores))
    assert r.band == sent.band(r.score)
    for c in r.components:
        if c.key != "cot":
            assert c.score == pytest.approx(c.percentile if c.orientation > 0
                                            else 100 - c.percentile)


def test_a_vix_at_its_high_is_extreme_fear_for_that_component(store):
    _fill_all(store)
    days = _business_days(AS_OF.date() - dt.timedelta(days=1), 900)
    store.put("vol.vix", days, np.arange(900.0))  # the last value is the highest
    vix = next(c for c in sent.read_sentiment(None, AS_OF).components if c.key == "vix")
    assert vix.percentile == 100.0 and vix.score == 0.0


def test_missing_components_are_listed_and_never_imputed(store):
    _fill_all(store)
    for sid in ("vol.vix", "sent.pc.equity", "eq.spx", "credit.hy.oas"):
        del store.rows[sid]
    r = sent.read_sentiment(None, AS_OF)

    assert r.used == 3
    assert r.status == "insufficient" and r.score is None and r.band is None
    missing = {c.key: c for c in r.components if c.status != "ok"}
    assert set(missing) == {"vix", "put_call", "trend", "credit"}
    assert all(c.score is None and c.percentile is None for c in missing.values())


def test_a_stale_component_drops_out(store):
    _fill_all(store)
    old_end = AS_OF.date() - dt.timedelta(days=30)
    store.put("vol.vix", _business_days(old_end, 900), np.arange(900.0))
    vix = next(c for c in sent.read_sentiment(None, AS_OF).components if c.key == "vix")
    assert vix.status == "stale" and vix.score is None and "30 days" in vix.detail


def test_short_history_is_not_ranked(store):
    _fill_all(store)
    store.put("vol.vix", _business_days(AS_OF.date(), 100), np.arange(100.0))
    vix = next(c for c in sent.read_sentiment(None, AS_OF).components if c.key == "vix")
    assert vix.status == "short_history" and vix.percentile is None


def test_an_aaii_week_is_invisible_before_its_thursday_release(store):
    _fill_all(store)
    thursday = dt.date(2026, 10, 8)
    weeks = _weekly(thursday, 200)
    bull = np.full(200, 30.0)
    bull[-1] = 60.0  # the newest week is a bullish spike
    # Released Thursday 12:00 ET = 16:00 UTC.
    store.put("sent.aaii.bull", weeks, bull, lag_hours=16)
    store.put("sent.aaii.bear", weeks, np.full(200, 30.0), lag_hours=16)

    before = dt.datetime(2026, 10, 8, 15, 0, tzinfo=dt.UTC)
    after = dt.datetime(2026, 10, 8, 17, 0, tzinfo=dt.UTC)
    a_before = next(c for c in sent.read_sentiment(None, before).components if c.key == "aaii")
    a_after = next(c for c in sent.read_sentiment(None, after).components if c.key == "aaii")

    assert a_before.value_date == dt.date(2026, 10, 1)
    assert a_after.value_date == thursday
    assert a_after.value > a_before.value


def test_conditions_synthesis_and_missing_macro(store, monkeypatch):
    _fill_all(store, last=0.0)
    end = AS_OF.date() - dt.timedelta(days=1)
    days = _business_days(end, 30)
    store.put("desk.gex.spx.net", days, np.full(30, -2e9))
    store.put("desk.gex.spx.flip_dist", days, np.full(30, -1.2))

    def no_macro(*a, **k):
        raise EmptyFetchError("regime: ust.10y.real has no observations")
    monkeypatch.setattr(sent.regime, "classify", no_macro)
    # Force a fear reading so the cell is known.
    monkeypatch.setattr(sent, "read_sentiment", lambda conn, as_of: sent.SentimentReading(
        as_of, "ok", 15.0, "extreme_fear", []))

    c = sent.read_conditions(None, AS_OF)

    assert c.macro is None and "ust.10y.real" in c.macro_detail
    spx, qqq = c.gamma
    assert (spx.status, spx.sign, spx.flip_dist_pct) == ("ok", "negative", -1.2)
    assert qqq.status == "no_data"
    assert c.synthesis == "stressed"


def test_no_synthesis_without_spx_gamma(store, monkeypatch):
    _fill_all(store)
    monkeypatch.setattr(sent.regime, "classify",
                        lambda *a, **k: (_ for _ in ()).throw(EmptyFetchError("x")))
    c = sent.read_conditions(None, AS_OF)
    assert c.synthesis is None and "SPX gamma" in c.synthesis_detail


def test_every_cell_of_the_synthesis_is_defined():
    for sign in ("positive", "negative"):
        for side in ("fear", "neutral", "greed"):
            assert (sign, side) in sent.SYNTHESIS
    assert {sent.side(b) for _, b in sent.BANDS} == {"fear", "neutral", "greed"}


def test_endpoints_shape_nulls_not_zeros(store, monkeypatch):
    from app.main import app
    from app.modules.terminal.api import conditions as api

    _fill_all(store)
    del store.rows["vol.vix"]

    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(api, "connect", lambda **kw: _Conn())
    monkeypatch.setattr(sent.regime, "classify",
                        lambda *a, **k: (_ for _ in ()).throw(EmptyFetchError("x")))
    client = TestClient(app)

    body = client.get("/api/terminal/sentiment",
                      params={"as_of": AS_OF.isoformat()}).json()
    vix = next(c for c in body["components"] if c["key"] == "vix")
    assert vix["status"] == "no_data" and vix["score"] is None and vix["value"] is None
    assert body["used"] == len(sent.COMPONENTS) - 1

    cond = client.get("/api/terminal/conditions",
                      params={"as_of": AS_OF.isoformat()}).json()
    assert cond["macro"] is None and cond["synthesis"] is None
    assert [g["underlying"] for g in cond["gamma"]] == ["SPX", "QQQ"]

    assert client.get("/api/terminal/sentiment",
                      params={"as_of": "2026-10-15T12:00:00"}).status_code == 422
