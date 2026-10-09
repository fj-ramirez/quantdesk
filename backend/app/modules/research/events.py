"""The M1 R evaluator -- pure module (T147, plans/charter-mt5/README.md decision 8).

Replays a list of trade intents against one-minute bars and scores each in **R**, multiples of
the planned risk ``|entry - stop|``. The price-action study (`pa_study.py`) decides *what* to
trade; this module only answers *what happened*, so it can be tested on hand-built bars.

**Pure**: no I/O, no clock, no logging. Input arrays are positional and ascending in time.

Rules, carried over from `app.modules.gex.scan.outcomes` (T61, T115) to M1 resolution, all
chosen so a backtest understates rather than flatters:

* An intent fills **at market at the open of its fill bar**, which is the first M1 bar after the
  signal bar closed. If that open is already at or beyond the stop, the intent is
  **skipped**. The risk it was sized for no longer exists.
* From the fill bar on, each bar is checked for the **stop before the target**. A bar that
  touches both counts as a stop.
* A bar that **opens beyond** the stop exits at its open (worse than the stop). A bar that opens
  beyond the target exits at its open (better). Gaps fill at the open both ways.
* **The fill bar never pays.** It can stop the trade but cannot reach the target.
* With no stop or target before the ``deadline`` bar, the trade exits at the **close of the
  last bar before the deadline** (a time exit).
* **Cost** is charged in price at entry: the bar's recorded spread times ``cost_mult``, plus
  ``commission``. It goes into R by dividing by the risk. One spread per round trip, because
  MT5 bars are bid prices, so a long buys at bid + spread and sells at bid.
* **One position at a time.** An intent whose fill bar comes before the previous trade's exit
  bar is skipped (``busy``). The study models one person executing by hand, not a portfolio
  of overlapping copies of the same signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

LONG = 1
SHORT = -1


@dataclass(frozen=True, slots=True)
class Intent:
    """One trade the study wants to take.

    Attributes:
        fill: M1 position of the bar whose open is the fill.
        side: ``LONG`` (1) or ``SHORT`` (-1).
        stop: Stop price.
        target: Target price, or ``None`` for a time-exit-only trade.
        deadline: M1 position by which the trade must be flat. It exits at the close of
            ``deadline - 1`` at the latest. Must be ``> fill``.
    """

    fill: int
    side: int
    stop: float
    target: float | None
    deadline: int


@dataclass(frozen=True, slots=True)
class Trade:
    fill: int
    exit: int
    side: int
    entry: float
    stop: float
    exit_price: float
    risk: float
    #: R before any cost.
    gross_r: float
    #: Cost in R at ``cost_mult`` = 1. The caller scales it for the doubled-cost gate, so one
    #: replay serves both.
    cost_r: float
    #: ``stop``, ``target``, ``time``, ``stop_gap`` or ``target_gap``.
    how: str


def _first(mask: np.ndarray) -> int:
    """Position of the first True, or -1."""
    if mask.size == 0:
        return -1
    i = int(mask.argmax())
    return i if mask[i] else -1


def replay(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    spread: np.ndarray,
    intents: list[Intent],
    *,
    commission: float = 0.0,
) -> tuple[list[Trade], dict[str, int]]:
    """Replay ``intents`` in order of ``fill`` and return the trades and the skip counts.

    ``spread`` is in price units, per bar. Skips are counted under ``busy``, ``gapped``
    (fill open at or through the stop), ``no_risk`` (stop equal to entry) and ``no_bars``.
    """
    n = len(open_)
    trades: list[Trade] = []
    skipped = {"busy": 0, "gapped": 0, "no_risk": 0, "no_bars": 0}
    free_from = 0
    for it in sorted(intents, key=lambda i: i.fill):
        if it.fill < free_from:
            skipped["busy"] += 1
            continue
        end = min(it.deadline, n)
        if it.fill >= end:
            skipped["no_bars"] += 1
            continue
        side = it.side
        entry = float(open_[it.fill])
        risk = (entry - it.stop) * side
        if risk == 0:
            skipped["no_risk"] += 1
            continue
        if risk < 0:
            skipped["gapped"] += 1
            continue

        lo = low[it.fill : end]
        hi = high[it.fill : end]
        op = open_[it.fill : end]
        if side == LONG:
            stop_hit = lo <= it.stop
            tgt_hit = hi >= it.target if it.target is not None else np.zeros(len(hi), bool)
        else:
            stop_hit = hi >= it.stop
            tgt_hit = lo <= it.target if it.target is not None else np.zeros(len(lo), bool)
        tgt_hit = tgt_hit.copy()
        tgt_hit[0] = False  # the fill bar never pays

        s, t = _first(stop_hit), _first(tgt_hit)
        if s >= 0 and (t < 0 or s <= t):  # stop first, also on the ambiguous bar
            k = s
            gapped = (op[k] - it.stop) * side < 0 and k > 0
            exit_price = float(op[k]) if gapped else it.stop
            how = "stop_gap" if gapped else "stop"
        elif t >= 0:
            k = t
            gapped = (op[k] - it.target) * side > 0
            exit_price = float(op[k]) if gapped else float(it.target)
            how = "target_gap" if gapped else "target"
        else:
            k = end - 1 - it.fill
            exit_price = float(close[end - 1])
            how = "time"

        exit_pos = it.fill + k
        cost = float(spread[it.fill]) + commission
        trades.append(
            Trade(
                fill=it.fill,
                exit=exit_pos,
                side=side,
                entry=entry,
                stop=it.stop,
                exit_price=exit_price,
                risk=risk,
                gross_r=(exit_price - entry) * side / risk,
                cost_r=cost / risk,
                how=how,
            )
        )
        free_from = exit_pos + 1
    return trades, skipped


def clustered_mean_se(r: np.ndarray, cluster: np.ndarray) -> tuple[float, float]:
    """Mean of ``r`` and its standard error clustered by ``cluster`` (e.g. the session date).

    Uses the cluster-robust (CR0) variance of a mean: residuals are summed within each cluster
    before squaring, so trades on the same day that win or lose together are not counted as
    independent evidence. Returns ``(nan, nan)`` for fewer than two clusters.
    """
    r = np.asarray(r, dtype=float)
    if r.size == 0:
        return float("nan"), float("nan")
    mean = float(r.mean())
    _, inv = np.unique(cluster, return_inverse=True)
    g = int(inv.max()) + 1
    if g < 2:
        return mean, float("nan")
    sums = np.bincount(inv, weights=r - mean, minlength=g)
    se = float(np.sqrt((sums**2).sum() * g / (g - 1))) / r.size
    return mean, se
