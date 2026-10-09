"""Basis between a desk symbol and the CFD it is traded through (T145, plans/charter-mt5/).

Pure: no I/O, no clock. The job in `app/modules/broker/jobs/basis.py` feeds it frames read
from `gex.daily_bars`, `gex.intraday_bars` and `broker.bars`, and stores what it returns.

What is measured
----------------

At every desk observation (a daily close, or a 5-minute bar's close) the CFD's price at the
same instant is taken from its M1 bars, and both are kept: ``offset = cfd - desk`` and
``ratio = cfd / desk``. Each :class:`Pair` names the one its levels are translated with.
Indices use the offset, because a futures CFD sits a fair-value premium in points above the
cash index. ETFs use the ratio, because SPY is about a tenth of the index and drifts with its
dividends.

The instant:

* a 5-minute desk bar stamped ``ts`` (its open) closes at ``ts + 5 min``, and the M1 CFD bar
  stamped ``ts + 4 min`` closes at the same moment;
* a daily close is 16:00 New York, which is the close of the CFD's 15:59 M1 bar. Candidate
  early-close days (July 2–3, the Friday after Thanksgiving, December 24) are **left out**
  rather than given a guessed closing time, because the desk's calendar has no early closes.
  That costs about four days a year.

Rolls
-----

The `.fs` CFDs track index futures, which roll quarterly, and MT5 reports no expiry for them.
The roll is therefore found in the basis itself, as a step in the log ratio. On five years of
daily closes (measured 2026-10-08) every step above 0.45 % fell on a roll Monday (the second
Monday of March, June, September or December): +0.49 % to +1.24 %, against a median daily
change of 0.02–0.03 %. Before 2022-09, near-zero rates made the step too small to see, and
too small to matter.

A step must also **persist**: the median of the ``k`` observations from the step on must
differ from the median of the ``k`` before it by most of the threshold. That rejects a bad
print that reverts the next day. The desk's SPX daily close on 2026-06-26 disagrees with SPY by
0.54 % and made a −0.73 % / +0.52 % pair, which is exactly such a print. The persistence test
means a roll is ``confirmed`` ``k - 1`` observations after it ``occurred``, the same
occurred/confirmed split as `scan/price_action.py`.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "PAIRS",
    "ROLL_PERSIST",
    "ROLL_THRESHOLD",
    "Pair",
    "Roll",
    "detect_rolls",
    "is_early_close_candidate",
    "measure",
    "translate",
]

#: A roll is a step of at least this much in log(cfd / desk), about 0.45 %.
ROLL_THRESHOLD = 0.0045
#: Observations on each side of a step whose medians must also differ (see module docstring).
ROLL_PERSIST = 3
#: The share of the threshold the persistent step must keep.
_PERSIST_SHARE = 0.8
#: Rolls are quarterly, so two steps within this many observations are one roll.
_MIN_GAP_OBS = 20


@dataclass(frozen=True, slots=True)
class Pair:
    desk: str
    cfd: str
    method: str  # "offset" or "ratio": how a desk level is translated
    proxy: bool = False  # the CFD tracks a different underlying (NAS100 is NDX, not QQQ)
    rolls: bool = True  # futures-based CFD, so expect quarterly rolls


PAIRS: tuple[Pair, ...] = (
    Pair("SPX", "S&P.fs", "offset"),
    Pair("SPY", "S&P.fs", "ratio"),
    Pair("QQQ", "NAS100.fs", "ratio", proxy=True),
    Pair("DIA", "DJ30.fs", "ratio"),
    Pair("GLD", "XAUUSD", "ratio", rolls=False),
)


@dataclass(frozen=True, slots=True)
class Roll:
    """A roll seen in one pair's basis. `occurred` is the first observation on the new
    contract; `confirmed` the observation at which the persistence test could first pass."""

    occurred: pd.Timestamp
    confirmed: pd.Timestamp
    step: float  # change in log(cfd / desk)


def is_early_close_candidate(day: dt.date) -> bool:
    """July 2–3, the Friday after Thanksgiving, December 24. A superset of NYSE's early
    closes on weekdays. Left out of the daily basis rather than given a guessed close."""
    if (day.month, day.day) in ((7, 2), (7, 3), (12, 24)):
        return True
    if day.month == 11 and day.weekday() == 4:
        thursdays = [d for d in range(1, 31) if dt.date(day.year, 11, d).weekday() == 3]
        return day.day == thursdays[3] + 1
    return False


def measure(desk: pd.Series, cfd: pd.Series) -> pd.DataFrame:
    """Join two price series on their instants. Both are indexed by tz-aware UTC timestamps.

    Returns columns `desk, cfd, offset, ratio`, indexed by instant, ascending. An instant
    missing from either side is absent: no fill, no interpolation.
    """
    df = pd.concat({"desk": desk, "cfd": cfd}, axis=1, join="inner").sort_index()
    df = df[(df["desk"] > 0) & (df["cfd"] > 0)]
    df["offset"] = df["cfd"] - df["desk"]
    df["ratio"] = df["cfd"] / df["desk"]
    return df


def detect_rolls(basis: pd.DataFrame, *, threshold: float = ROLL_THRESHOLD,
                 persist: int = ROLL_PERSIST) -> list[Roll]:
    """Steps in log(ratio) that are large and persist. See the module docstring."""
    if len(basis) < 2 * persist:
        return []
    lr = np.log(basis["ratio"].to_numpy(dtype=float))
    index = basis.index
    rolls: list[Roll] = []
    last = -math.inf
    for i in range(persist, len(lr) - persist + 1):
        step = lr[i] - lr[i - 1]
        if abs(step) <= threshold or i - last < _MIN_GAP_OBS:
            continue
        held = np.median(lr[i : i + persist]) - np.median(lr[i - persist : i])
        if abs(held) < _PERSIST_SHARE * threshold or np.sign(held) != np.sign(step):
            continue
        rolls.append(Roll(index[i], index[i + persist - 1], float(step)))
        last = i
    return rolls


def translate(level: float | None, pair: Pair, offset: float, ratio: float) -> float | None:
    """A desk level in the CFD's price, with the pair's method. `None` stays `None`."""
    if level is None or (isinstance(level, float) and math.isnan(level)):
        return None
    return level + offset if pair.method == "offset" else level * ratio
