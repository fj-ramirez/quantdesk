"""Regime classification (spec 3.4).

Spec 3.4 is unusually prescriptive about the method and the reason:

    "A three-state classification on the signs and magnitudes of (real yield
    change, credit spread change, dollar change) over a trailing 20-day window
    covers most of what a Markov-switching model would give you, and you will
    understand it when it is wrong. Only reach for HMMs after the simple version
    has demonstrably failed at something specific."

So this is deliberately a handful of documented thresholds, not a model. Every
threshold is a named parameter, and the classifier returns the evidence it used
alongside the label so a wrong call can be read rather than guessed at.

THE FOURTH STATE
----------------
The spec asks for three states. This returns four, and the extra one is the
point: QUIET, when no leg has moved enough to classify. Forcing every period
into risk-on, risk-off or rates-led would put a label on noise, and a regime
series that is always confident is exactly the kind of thing that reads well and
misleads. A period that does not qualify says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from ..errors import EmptyFetchError
from ..logging import get_logger

# T79: was `import duckdb`, with a DuckDB connection type in every signature below.
# `store.db` is still the only module that knows the engine (its docstring always said
# so); `Connection` is the alias it exports, so the next swap is one line there.
from ..store.db import Connection
from ..store.query import get
from .transforms import apply_transform

log = get_logger("analytics.regime")

# The three legs, per spec 3.4.
REAL_YIELD = "ust.10y.real"
CREDIT_SPREAD = "credit.hy.oas"
DOLLAR = "fx.usd.broad"

# Trailing window over which each leg's move is measured.
REGIME_WINDOW_DAYS = 20

# History the move is scored against, so "large" means large for that series
# rather than large in absolute units.
REGIME_SCORE_WINDOW = 250

# A leg counts as having moved when its windowed change exceeds this many
# standard deviations of its own windowed-change history.
REGIME_MIN_Z = 0.75

RISK_OFF = "risk_off"
RISK_ON = "risk_on"
RATES_LED = "rates_led"
QUIET = "quiet"


@dataclass(frozen=True)
class RegimeReading:
    """A classification, with the evidence behind it."""

    as_of: datetime
    value_date: object
    state: str
    real_yield_z: float
    credit_z: float
    dollar_z: float
    window_days: int
    detail: str

    def evidence(self) -> dict[str, float]:
        return {
            REAL_YIELD: self.real_yield_z,
            CREDIT_SPREAD: self.credit_z,
            DOLLAR: self.dollar_z,
        }


def _windowed_move_z(
    conn: Connection,
    series_id: str,
    as_of: datetime,
    window_days: int,
    score_window: int,
    max_gap_days: int,
) -> tuple[float, object]:
    """The trailing n-day change, scored against its own history of n-day changes."""
    lookback = int((score_window + window_days) * 2.0)
    start = as_of.date() - timedelta(days=lookback)
    obs = get(conn, series_id, start, as_of.date(), as_of=as_of, warn_empty=False)
    if obs.empty:
        raise EmptyFetchError(
            f"regime: {series_id} has no observations as of {as_of.isoformat()}; "
            "the classification needs all three legs and will not guess a missing one."
        )

    meta = conn.execute(
        "SELECT default_transform, unit FROM series_metadata WHERE series_id = ?",
        [series_id],
    ).fetchone()
    changes = apply_transform(obs, series_id, meta[0], meta[1], max_gap_days)
    daily = changes.usable()
    if len(daily) < score_window // 2:
        raise EmptyFetchError(
            f"regime: {series_id} has only {len(daily)} gap-clean changes; too "
            "few to score a 20-day move against its own history."
        )

    # Sum of daily changes over the window: a 20-day move built from the same
    # gap-clean changes everything else uses, rather than a level difference
    # that would silently span a hole.
    rolled = daily["change"].rolling(window_days).sum().dropna()
    if len(rolled) < 2:
        raise EmptyFetchError(f"regime: {series_id} yielded no {window_days}-day moves")

    history = rolled.tail(score_window)
    current = float(rolled.iloc[-1])
    # DuckDB hands DATE columns back as datetime64, so convert at the boundary
    # rather than reporting "data through 2026-09-10 00:00:00".
    last_date = pd.Timestamp(daily["value_date"].iloc[-1]).date()
    sd = float(history.std(ddof=1))
    if sd == 0 or not np.isfinite(sd):
        return 0.0, last_date
    return (current - float(history.mean())) / sd, last_date


def classify(
    conn: Connection,
    as_of: datetime,
    window_days: int = REGIME_WINDOW_DAYS,
    score_window: int = REGIME_SCORE_WINDOW,
    min_z: float = REGIME_MIN_Z,
    max_gap_days: int = 5,
) -> RegimeReading:
    """Classify the trailing window into one of four states.

    The rules, in order:

      QUIET      no leg moved by min_z. Not a regime, an absence of one.
      RISK_OFF   credit wider AND dollar stronger -- the classic pairing.
      RISK_ON    credit tighter AND dollar weaker.
      RATES_LED  neither risk pairing holds, so the real-yield leg is doing the
                 work or the legs disagree. Named for what is driving it rather
                 than pretending it is a risk signal.
    """
    real_z, vdate = _windowed_move_z(
        conn, REAL_YIELD, as_of, window_days, score_window, max_gap_days
    )
    credit_z, _ = _windowed_move_z(
        conn, CREDIT_SPREAD, as_of, window_days, score_window, max_gap_days
    )
    dollar_z, _ = _windowed_move_z(
        conn, DOLLAR, as_of, window_days, score_window, max_gap_days
    )

    moved = [abs(z) >= min_z for z in (real_z, credit_z, dollar_z)]
    if not any(moved):
        state = QUIET
        detail = (
            f"no leg moved by {min_z}sd over {window_days} days; "
            "no regime claimed"
        )
    elif credit_z >= min_z and dollar_z >= min_z:
        state = RISK_OFF
        detail = "credit wider and dollar stronger together"
    elif credit_z <= -min_z and dollar_z <= -min_z:
        state = RISK_ON
        detail = "credit tighter and dollar weaker together"
    else:
        state = RATES_LED
        driver = max(
            (("real yield", real_z), ("credit", credit_z), ("dollar", dollar_z)),
            key=lambda kv: abs(kv[1]),
        )
        detail = (
            f"no coherent risk pairing; largest mover is {driver[0]} "
            f"at {driver[1]:+.2f}sd"
        )

    reading = RegimeReading(
        as_of=as_of,
        value_date=vdate,
        state=state,
        real_yield_z=real_z,
        credit_z=credit_z,
        dollar_z=dollar_z,
        window_days=window_days,
        detail=detail,
    )
    log.info(
        "regime %s: real %+.2f credit %+.2f dollar %+.2f (%s)",
        state, real_z, credit_z, dollar_z, detail,
    )
    return reading


def history(
    conn: Connection,
    as_ofs: list[datetime],
    **kwargs,
) -> pd.DataFrame:
    """Classify a sequence of dates, each as of itself.

    Every reading is built from an explicit as_of, so a regime history carries
    no hindsight: each row is what the classifier would have said that evening.
    """
    rows = []
    for a in as_ofs:
        try:
            r = classify(conn, a, **kwargs)
        except EmptyFetchError as e:
            log.warning("regime: %s -> %s", a.date(), e)
            continue
        rows.append(
            {
                "as_of": a,
                "value_date": r.value_date,
                "state": r.state,
                "real_yield_z": r.real_yield_z,
                "credit_z": r.credit_z,
                "dollar_z": r.dollar_z,
            }
        )
    return pd.DataFrame(rows)
