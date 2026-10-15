"""Sentiment composite and the desk-wide conditions reading (T171, plans/sentiment).

A **reading, not a signal.** It describes the market as of an instant; whether its extremes
carry a forward return is an EdgeLab question (T173), and nothing here is fitted to returns.
Everything that shapes the number is a named constant in this file:

* seven components, each ranked within its own trailing history (0-100, inclusive of the
  current value, `derive.rolling_percentile`'s convention) and oriented so 100 = greed;
* the score is their plain mean -- equal weights, because any other weights would be a trial;
* a component with no value as of `as_of`, a last value older than its `max_age_days`, or too
  little history is **missing**, listed with its reason and left out, never imputed;
* fewer than `MIN_COMPONENTS` present -> `insufficient`, no score. Same honesty as the macro
  regime's `quiet`.

Every component's input comes from `terminal.observations` through `query.get(as_of=...)`, so a
reading for a past instant uses only what had been published by then (invariant 10).

The conditions reading puts this beside the macro regime (`regime.classify`) and the desk's
own dealer gamma (`desk.gex.*`, T170), and names the combination with a fixed lookup over gamma
sign x sentiment side. The lookup is description of the dealer-hedging mechanism the GEX pages
already rest on, not a forecast.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from ..errors import EmptyFetchError
from ..logging import get_logger
from ..store.db import Connection
from ..store.query import get
from . import regime

log = get_logger("analytics.sentiment")

#: Fewer components than this and the composite says `insufficient` instead of a number.
MIN_COMPONENTS = 4

#: Band upper bounds (exclusive) on the 0-100 score, in order.
BANDS: tuple[tuple[float, str], ...] = (
    (20.0, "extreme_fear"),
    (40.0, "fear"),
    (60.0, "neutral"),
    (80.0, "greed"),
    (float("inf"), "extreme_greed"),
)

#: ~3 years of daily observations, and of weekly ones.
DAILY_WINDOW = 756
WEEKLY_WINDOW = 156

Transform = Callable[[dict[str, pd.Series]], pd.Series]


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    #: Series the transform reads, all aligned on value_date.
    series: tuple[str, ...]
    #: Plain-language statement of the transform, echoed in the response.
    transform: str
    fn: Transform
    #: +1 when a high value means greed, -1 when it means fear.
    orientation: int
    #: Trailing history the percentile is taken within (observations).
    window: int
    #: Oldest last value_date still counted as current, in calendar days before as_of.
    max_age_days: int
    #: The input is already a 0-100 percentile (COT), so it is used as is, not re-ranked.
    is_percentile: bool = False


def _level(sid: str) -> Transform:
    return lambda s: s[sid]


def _mean(sid: str, n: int) -> Transform:
    return lambda s: s[sid].rolling(n, min_periods=n).mean()


COMPONENTS: tuple[Component, ...] = (
    Component("vix", "VIX level", ("vol.vix",), "level", _level("vol.vix"),
              -1, DAILY_WINDOW, 5),
    Component("term", "VIX term structure", ("vol.vix3m_ratio",),
              "VIX3M / VIX (below 1 = backwardation)", _level("vol.vix3m_ratio"),
              +1, DAILY_WINDOW, 5),
    Component("put_call", "Equity put/call", ("sent.pc.equity",), "10-day mean",
              _mean("sent.pc.equity", 10), -1, DAILY_WINDOW, 5),
    Component("aaii", "AAII bull - bear", ("sent.aaii.bull", "sent.aaii.bear"),
              "bullish minus bearish, 4-week mean",
              lambda s: (s["sent.aaii.bull"] - s["sent.aaii.bear"])
              .rolling(4, min_periods=4).mean(),
              +1, WEEKLY_WINDOW, 14),
    Component("credit", "HY credit spread", ("credit.hy.oas",), "20-day change",
              lambda s: s["credit.hy.oas"].diff(20), -1, DAILY_WINDOW, 5),
    Component("trend", "S&P 500 vs 125-day mean", ("eq.spx",), "close / 125-day mean - 1",
              lambda s: s["eq.spx"] / s["eq.spx"].rolling(125, min_periods=125).mean() - 1.0,
              +1, DAILY_WINDOW, 5),
    # Tuesday-dated, published Friday: the newest value is up to ~10 days old at the next
    # release, so twice the weekly cadence plus that lag.
    Component("cot", "S&P leveraged-funds positioning", ("pos.cot.es.lev_money.pctile",),
              "3-year percentile of net position", _level("pos.cot.es.lev_money.pctile"),
              +1, WEEKLY_WINDOW, 17, is_percentile=True),
)

#: Shown beside the composite, never scored: whether a high SKEW is fear (tail bid) or
#: complacency (cheap wings, rich skew) depends on what the rest of the surface is doing.
DISPLAY_ONLY = ("vol.skew",)


@dataclass(frozen=True)
class ComponentReading:
    key: str
    label: str
    series: tuple[str, ...]
    transform: str
    orientation: int
    status: str  # ok | no_data | stale | short_history
    value: float | None = None
    percentile: float | None = None
    #: The percentile after orientation: 100 = greed. What the score averages.
    score: float | None = None
    value_date: date | None = None
    detail: str = ""


@dataclass(frozen=True)
class SentimentReading:
    as_of: datetime
    status: str  # ok | insufficient
    score: float | None
    band: str | None
    components: list[ComponentReading]
    display: dict[str, float | None] = field(default_factory=dict)

    @property
    def used(self) -> int:
        return sum(1 for c in self.components if c.status == "ok")


def band(score: float) -> str:
    return next(name for bound, name in BANDS if score < bound)


def _load(conn: Connection, sid: str, as_of: datetime, days: int) -> pd.Series:
    df = get(conn, sid, as_of.date() - timedelta(days=days), as_of.date(), as_of=as_of,
             warn_empty=False)
    if df.empty:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(df["value_date"]).dt.date
    return pd.Series(df["value"].astype(float).to_numpy(), index=idx).sort_index()


def read_component(conn: Connection, comp: Component, as_of: datetime) -> ComponentReading:
    # Calendar lookback: the window plus the transform's own warm-up, generously.
    per_year = 252 if comp.window == DAILY_WINDOW else 52
    days = int((comp.window + 130) / per_year * 365 * 1.1) + 30
    base = {"key": comp.key, "label": comp.label, "series": comp.series,
            "transform": comp.transform, "orientation": comp.orientation}

    inputs = {sid: _load(conn, sid, as_of, days) for sid in comp.series}
    empty = [sid for sid, s in inputs.items() if s.empty]
    if empty:
        return ComponentReading(**base, status="no_data",
                                detail=f"no observations as of this instant: {', '.join(empty)}")

    frame = pd.DataFrame(inputs).dropna()
    values = comp.fn({sid: frame[sid] for sid in comp.series}).dropna()
    if values.empty:
        return ComponentReading(**base, status="short_history",
                                detail="not enough history for the transform")

    last_date = values.index[-1]
    current = float(values.iloc[-1])
    age = (as_of.date() - last_date).days
    if age > comp.max_age_days:
        return ComponentReading(**base, status="stale", value=current, value_date=last_date,
                                detail=f"last value {age} days old (limit {comp.max_age_days})")

    if comp.is_percentile:
        pct = current
    else:
        history = values.tail(comp.window)
        if len(history) < comp.window // 2:
            return ComponentReading(
                **base, status="short_history", value=current, value_date=last_date,
                detail=f"{len(history)} observations, needs {comp.window // 2}")
        pct = 100.0 * float((history <= current).sum()) / len(history)

    oriented = pct if comp.orientation > 0 else 100.0 - pct
    return ComponentReading(**base, status="ok", value=current, percentile=pct,
                            score=oriented, value_date=last_date,
                            detail=f"{pct:.0f}th percentile of {comp.window} observations"
                            if not comp.is_percentile else "already a 3-year percentile")


def read_sentiment(conn: Connection, as_of: datetime) -> SentimentReading:
    components = [read_component(conn, c, as_of) for c in COMPONENTS]
    scores = [c.score for c in components if c.status == "ok" and c.score is not None]
    display: dict[str, float | None] = {}
    for sid in DISPLAY_ONLY:
        s = _load(conn, sid, as_of, 14)
        display[sid] = None if s.empty else float(s.iloc[-1])

    if len(scores) < MIN_COMPONENTS:
        return SentimentReading(as_of, "insufficient", None, None, components, display)
    score = float(np.mean(scores))
    return SentimentReading(as_of, "ok", score, band(score), components, display)


# --- gamma -----------------------------------------------------------------------------

#: The desk's own EOD gamma, as of the instant. One business day plus a weekend.
GAMMA_MAX_AGE_DAYS = 4
GAMMA_UNDERLYINGS = ("SPX", "QQQ")


@dataclass(frozen=True)
class GammaReading:
    underlying: str
    status: str  # ok | no_data | stale
    sign: str | None = None  # positive | negative
    net_gex: float | None = None
    flip_dist_pct: float | None = None
    value_date: date | None = None
    detail: str = ""


def read_gamma(conn: Connection, underlying: str, as_of: datetime) -> GammaReading:
    u = underlying.lower()
    net = _load(conn, f"desk.gex.{u}.net", as_of, 14)
    if net.empty:
        return GammaReading(underlying, "no_data", detail="no EOD gamma as of this instant")
    last_date = net.index[-1]
    age = (as_of.date() - last_date).days
    if age > GAMMA_MAX_AGE_DAYS:
        return GammaReading(underlying, "stale", value_date=last_date,
                            detail=f"last EOD gamma {age} days old")
    flip = _load(conn, f"desk.gex.{u}.flip_dist", as_of, 14)
    flip_val = float(flip.iloc[-1]) if not flip.empty and flip.index[-1] == last_date else None
    value = float(net.iloc[-1])
    return GammaReading(underlying, "ok", "positive" if value >= 0 else "negative", value,
                        flip_val, last_date)


# --- conditions ------------------------------------------------------------------------

#: (gamma sign, sentiment side) -> (name, reading). Sides collapse the five bands to three.
SYNTHESIS: dict[tuple[str, str], tuple[str, str]] = {
    ("negative", "fear"): ("stressed",
                           ("dealers short gamma amplify moves while sentiment is fearful; "
                           "moves tend to extend rather than revert")),
    ("negative", "neutral"): ("unstable",
                              ("dealers short gamma amplify moves with no sentiment extreme "
                              "behind them; ranges are less dependable")),
    ("negative", "greed"): ("fragile",
                            ("optimism is stretched while dealers are short gamma; a shock "
                            "would be amplified, not absorbed")),
    ("positive", "fear"): ("cushioned",
                           ("sentiment is washed out while dealer hedging dampens moves; "
                           "the hedging flow leans toward reversion")),
    ("positive", "neutral"): ("orderly",
                              "dealers long gamma dampen moves; walls tend to hold"),
    ("positive", "greed"): ("complacent",
                            ("dealers dampen moves and sentiment is stretched; realized vol "
                            "stays suppressed until a wall breaks")),
}


def side(band_name: str) -> str:
    if band_name in ("extreme_fear", "fear"):
        return "fear"
    if band_name in ("greed", "extreme_greed"):
        return "greed"
    return "neutral"


@dataclass(frozen=True)
class Conditions:
    as_of: datetime
    macro: regime.RegimeReading | None
    macro_detail: str
    gamma: list[GammaReading]
    sentiment: SentimentReading
    synthesis: str | None
    synthesis_detail: str


def read_conditions(conn: Connection, as_of: datetime, **regime_kwargs) -> Conditions:
    try:
        macro = regime.classify(conn, as_of, **regime_kwargs)
        macro_detail = macro.detail
    except EmptyFetchError as e:
        macro, macro_detail = None, str(e)

    gamma = [read_gamma(conn, u, as_of) for u in GAMMA_UNDERLYINGS]
    sentiment = read_sentiment(conn, as_of)

    # SPX is the anchor: it is the desk's primary book and the deepest options market.
    anchor = gamma[0]
    if anchor.status != "ok":
        synthesis, why = None, f"no synthesis: SPX gamma is {anchor.status} ({anchor.detail})"
    elif sentiment.band is None:
        synthesis, why = None, "no synthesis: sentiment is insufficient"
    else:
        synthesis, why = SYNTHESIS[(anchor.sign, side(sentiment.band))]  # type: ignore[index]
    return Conditions(as_of, macro, macro_detail, gamma, sentiment, synthesis, why)
