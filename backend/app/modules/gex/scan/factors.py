"""Factor structure of a candidate set -- pure module (T93).

Answers the question the decision engine could not: **how many bets is this book actually
making?** `app.modules.gex.scan.decisions` reasons about one symbol at a time, so a day on
which seventeen correlated names all print the same setup produces seventeen confident rows
that are one trade wearing seventeen tickers. Nothing in the path noticed, because nothing in
the path ever compared two candidates to each other.

**Pure, on the same contract as `app.modules.gex.gex.engine`, `app.modules.gex.scan.regime`
and `app.modules.gex.scan.decisions`**: no HTTP, no database, no filesystem, no logging, no
clock. `app.modules.gex.api.decisions` is the only caller that does I/O, and it already holds
every symbol's bars (`RegimeBuild.bars`), so measuring this costs no new fetch.

Design decisions (plans/decision-inputs/03-factor-cap.md)
--------------------------------------------------------

* **Correlate returns, never prices.** Two names in an uptrend have price correlation near 1
  whether or not they move together day to day; it is the single easiest way to produce a
  result that looks authoritative and measures nothing. :func:`return_frame` differences
  first, and everything downstream consumes that.

* **Rolling correlation, not PCA.** A pairwise correlation matrix is interpretable, cheap and
  directly answers "are these the same trade". A principal-components decomposition answers a
  more ambitious question -- how many latent factors span the universe -- and buys precision
  this desk cannot yet act on. :func:`effective_bets` therefore uses the equal-weight
  portfolio identity below rather than an eigendecomposition, and PCA stays a named follow-on.

* **The cap is a constraint on the emitted set, not a re-ranking.** :func:`cap_candidates`
  walks in the order it is given and marks each candidate that duplicates one already
  accepted. Greedy and order-dependent, and deliberately so: "XLE was suppressed because it is
  0.91-correlated with XOP, which scored higher" is a sentence a human can check. A portfolio
  optimizer would produce a better book and an unexplainable one, and a desk that has to trust
  its own output needs the explanation more than the optimum.

* **Nothing is ever silently dropped.** A suppressed candidate keeps its place in the result
  carrying *why* and *what it duplicates*. A set that quietly shrank is worse than one that did
  not shrink, because the reason is unrecoverable at the point of reading it.

* **Side is part of the question, and this is the decision that is not in the plan file.**
  Two names correlated +0.95 are the same trade only when they are traded the *same way*.
  Long XLE against short XOP at that correlation is close to a hedge; suppressing one leg of
  it as a "duplicate" would be exactly wrong. So the comparison is on the **side-adjusted**
  correlation, `corr * sign(a) * sign(b)` (see :func:`side_sign`), and only a positive result
  above the threshold counts as duplication. Two longs in correlated names duplicate; two
  shorts duplicate; a long and a short do not.

* **Two opportunities on one symbol are never duplicates of each other.** A symbol sitting
  between its two walls legitimately carries a short-at-the-call-wall *and* a
  long-at-the-put-wall suggestion -- the same range read from both ends, per
  `app.modules.gex.scan.decisions`' own module docstring. They would correlate 1.0 with
  themselves, so :func:`cap_candidates` compares across symbols only.

* **Insufficient overlap is `NaN`, and `NaN` never suppresses.** A candidate whose history
  does not overlap another's by :data:`MIN_OVERLAP` returns has an *undefined* correlation,
  not a high one. Suppressing on missing data would silently thin the book on exactly the
  names the desk knows least about.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

__all__ = [
    "CORR_THRESHOLD",
    "CORR_WINDOW",
    "MIN_OVERLAP",
    "FactorSummary",
    "Suppression",
    "cap_candidates",
    "correlation_matrix",
    "effective_bets",
    "mean_correlation",
    "return_frame",
    "side_sign",
    "summarize",
]

#: Trailing sessions of returns the correlation is measured over. A quarter: long enough to be
#: more than noise, short enough to track a relationship that is genuinely changing. Named so
#: callers reference the constant rather than repeating `60`.
CORR_WINDOW = 60

#: Side-adjusted correlation above which a candidate is treated as duplicating a higher-ranked
#: one. An opinion about how much concentration is acceptable, not a property of the data --
#: which is why it is a named constant the API can override per request rather than a literal
#: buried in :func:`cap_candidates`. Expect to revise it after a week of looking at the output.
CORR_THRESHOLD = 0.80

#: Overlapping return observations below which a pair's correlation is reported as `NaN`.
#: A correlation over a handful of shared days is arithmetic, not evidence.
MIN_OVERLAP = 30


@dataclass(frozen=True, slots=True)
class Suppression:
    """Why one candidate was suppressed, and by which other one.

    `correlation` is the **side-adjusted** figure that crossed the threshold, not the raw
    pairwise correlation -- it is the number the decision was actually made on. For two
    same-side candidates the two are identical; for opposite sides the raw correlation has the
    opposite sign, which is precisely why this field carries the adjusted one.
    """

    key: str
    symbol: str
    duplicates_key: str
    duplicates_symbol: str
    correlation: float

    @property
    def reason(self) -> str:
        return (
            f"{self.correlation:.2f} side-adjusted correlation with {self.duplicates_symbol} "
            f"({self.duplicates_key}), which ranked higher"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "symbol": self.symbol,
            "duplicates_key": self.duplicates_key,
            "duplicates_symbol": self.duplicates_symbol,
            "correlation": float(self.correlation),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FactorSummary:
    """What the emitted set looks like as a portfolio rather than as a list.

    `independent_bets` is `None` when it cannot be defined -- fewer than two candidates, or a
    correlation matrix too sparse to average (see :func:`effective_bets`). `None` means "not
    measurable", never "zero".
    """

    candidates: int
    accepted: int
    suppressed: int
    mean_correlation: float | None
    independent_bets: float | None
    window: int
    threshold: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "accepted": self.accepted,
            "suppressed": self.suppressed,
            "mean_correlation": _f(self.mean_correlation),
            "independent_bets": _f(self.independent_bets),
            "window": self.window,
            "threshold": self.threshold,
        }


def _f(value: float | None) -> float | None:
    """`None` for a missing or non-finite number, so a `NaN` never reaches JSON as `NaN`."""
    if value is None:
        return None
    number = float(value)
    return None if not np.isfinite(number) else number


def side_sign(side: str) -> int:
    """`+1` for a long, `-1` for a short, `0` for anything unrecognised.

    `0` rather than a raise: an unknown side makes every side-adjusted correlation involving
    that candidate `0`, so it can never duplicate anything and can never be duplicated. For a
    suppression mechanism, failing towards "keep the row" is the right direction -- the cost of
    an extra row is a line on a screen, and the cost of a wrongly removed one is a trade the
    desk never sees.
    """
    normalized = (side or "").strip().upper()
    if normalized == "LONG":
        return 1
    if normalized == "SHORT":
        return -1
    return 0


def return_frame(
    bars_by_symbol: dict[str, pd.DataFrame], *, window: int = CORR_WINDOW
) -> pd.DataFrame:
    """Daily simple returns for each symbol, aligned on date, trailing `window` rows.

    Simple returns rather than log: the two are indistinguishable for correlation at daily
    magnitudes, and the simple form is what a reader checking a number by hand will compute.

    Args:
        bars_by_symbol: Ascending-by-date daily bars per symbol, each with `date` and `close`.
            Symbols with no usable closes are dropped rather than carried as empty columns.
        window: Trailing return observations to keep.

    Returns:
        A frame indexed by date with one float column per symbol, `NaN` where a symbol has no
        observation on a date another symbol does. **Alignment is on the date, not on
        position**: two symbols with different histories must be compared on the days they
        share, and lining them up by row number would silently correlate Tuesday against
        Thursday.
    """
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")

    series: dict[str, pd.Series] = {}
    for symbol, bars in bars_by_symbol.items():
        if bars is None or bars.empty or "close" not in bars or "date" not in bars:
            continue
        closes = pd.Series(
            bars["close"].to_numpy(dtype=float), index=pd.Index(bars["date"]), name=symbol
        )
        closes = closes[~closes.index.duplicated(keep="last")].sort_index()
        with np.errstate(invalid="ignore", divide="ignore"):
            returns = closes.pct_change()
        returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
        if not returns.empty:
            series[symbol] = returns

    if not series:
        return pd.DataFrame()

    frame = pd.DataFrame(series).sort_index()
    return frame.tail(window)


def correlation_matrix(
    bars_by_symbol: dict[str, pd.DataFrame],
    *,
    window: int = CORR_WINDOW,
    min_overlap: int = MIN_OVERLAP,
) -> pd.DataFrame:
    """Pairwise return correlations over the trailing `window`, `NaN` below `min_overlap`.

    Pairwise-complete rather than listwise: dropping every date on which *any* symbol is
    missing would let one short-history name erase the window for all the others. The cost is
    that different cells rest on different numbers of observations, which is what `min_overlap`
    bounds and why it is enforced per pair rather than once for the frame.

    Returns:
        A symmetric frame indexed and columned by symbol, `1.0` on the diagonal for any symbol
        with at least `min_overlap` observations of its own. Empty when there is nothing to
        correlate.
    """
    frame = return_frame(bars_by_symbol, window=window)
    if frame.empty:
        return pd.DataFrame()

    corr = frame.corr(min_periods=min_overlap)
    # `DataFrame.corr` fills the diagonal with 1.0 even for a column that never met
    # `min_periods` against anything. A symbol we could not measure at all should read as
    # unknown throughout, including against itself, or it looks like a candidate that was
    # checked and cleared.
    for symbol in corr.columns:
        if int(frame[symbol].notna().sum()) < min_overlap:
            corr.loc[symbol, :] = np.nan
            corr.loc[:, symbol] = np.nan
    return corr


def effective_bets(corr: pd.DataFrame) -> float | None:
    """How many independent bets a set of `n` equally-weighted candidates really contains.

    From the equal-weight portfolio identity: `n` assets of equal variance with mean pairwise
    correlation `rho_bar` have portfolio variance `sigma^2/n * (1 + (n-1) * rho_bar)`, so the
    `n_eff` that would give the same variance with zero correlation is::

        n_eff = n / (1 + (n - 1) * rho_bar)

    All correlations 1 gives exactly 1 -- one bet, held `n` times, which is the situation this
    whole module exists to detect. All correlations 0 gives exactly `n`.

    Deliberately *not* an eigendecomposition. The participation ratio of the eigenvalues is the
    more general answer and it is the follow-on named in the plan; this identity needs only the
    off-diagonal mean, is checkable by hand, and answers the question actually being asked.

    Returns:
        The effective count, or `None` when it is not defined: fewer than two candidates, no
        measurable pair, or a denominator at zero. `None` is "not measurable", never zero.
    """
    if corr is None or corr.empty or len(corr.columns) < 2:
        return None

    values = corr.to_numpy(dtype=float)
    n = values.shape[0]
    off_diagonal = values[~np.eye(n, dtype=bool)]
    finite = off_diagonal[np.isfinite(off_diagonal)]
    if finite.size == 0:
        return None

    rho_bar = float(finite.mean())
    denominator = 1.0 + (n - 1) * rho_bar
    # For a genuine positive-semidefinite correlation matrix `rho_bar >= -1/(n-1)`, so this
    # cannot go negative -- but a pairwise-complete estimate over different overlaps is not
    # guaranteed PSD, so the guard is real rather than defensive decoration.
    if denominator <= 0.0:
        return None
    return n / denominator


def mean_correlation(corr: pd.DataFrame) -> float | None:
    """Mean of the finite off-diagonal correlations, or `None` when none are measurable."""
    if corr is None or corr.empty or len(corr.columns) < 2:
        return None
    values = corr.to_numpy(dtype=float)
    off_diagonal = values[~np.eye(values.shape[0], dtype=bool)]
    finite = off_diagonal[np.isfinite(off_diagonal)]
    return float(finite.mean()) if finite.size else None


def cap_candidates(
    candidates: list[tuple[str, str, str]],
    corr: pd.DataFrame,
    *,
    threshold: float = CORR_THRESHOLD,
) -> tuple[list[str], list[Suppression]]:
    """Walk `candidates` in the given order, marking each that duplicates an accepted one.

    Args:
        candidates: `(key, symbol, side)` in the order they should be considered -- highest
            conviction first. The caller owns the ordering; this function never reorders, per
            the module docstring's "constraint, not a re-ranking".
        corr: Pairwise correlations from :func:`correlation_matrix`.
        threshold: Side-adjusted correlation above which a candidate duplicates another.

    Returns:
        `(accepted_keys, suppressions)`. Every input key appears in exactly one of the two, so
        the caller can reconstruct the whole set and nothing goes missing.
    """
    accepted: list[str] = []
    accepted_rows: list[tuple[str, str, int]] = []  # (key, symbol, sign)
    suppressions: list[Suppression] = []

    for key, symbol, side in candidates:
        sign = side_sign(side)
        worst: tuple[float, str, str] | None = None

        for other_key, other_symbol, other_sign in accepted_rows:
            # Same underlying is never a duplicate of itself: a symbol between its two walls
            # legitimately carries both a fade-the-call-wall and a fade-the-put-wall row.
            if other_symbol == symbol:
                continue
            raw = _lookup(corr, symbol, other_symbol)
            if raw is None:
                continue
            adjusted = raw * sign * other_sign
            if adjusted > threshold and (worst is None or adjusted > worst[0]):
                worst = (adjusted, other_key, other_symbol)

        if worst is None:
            accepted.append(key)
            accepted_rows.append((key, symbol, sign))
        else:
            suppressions.append(
                Suppression(
                    key=key,
                    symbol=symbol,
                    duplicates_key=worst[1],
                    duplicates_symbol=worst[2],
                    correlation=worst[0],
                )
            )

    return accepted, suppressions


def _lookup(corr: pd.DataFrame, a: str, b: str) -> float | None:
    """The correlation between `a` and `b`, or `None` when it is absent or not finite."""
    if corr is None or corr.empty:
        return None
    if a not in corr.index or b not in corr.columns:
        return None
    value = corr.at[a, b]
    if value is None or not np.isfinite(value):
        return None
    return float(value)


def summarize(
    candidates: list[tuple[str, str, str]],
    corr: pd.DataFrame,
    *,
    window: int = CORR_WINDOW,
    threshold: float = CORR_THRESHOLD,
) -> tuple[list[str], list[Suppression], FactorSummary]:
    """:func:`cap_candidates` plus the :class:`FactorSummary` describing what it did.

    The summary's correlation figures are measured over the **distinct symbols among the
    candidates**, not over every column in `corr`: the question is how concentrated *this set*
    is, and averaging in a symbol nothing suggested would answer a different one.
    """
    accepted, suppressions = cap_candidates(candidates, corr, threshold=threshold)

    symbols = list(dict.fromkeys(symbol for _, symbol, _ in candidates))
    present = [s for s in symbols if not corr.empty and s in corr.columns]
    subset = corr.loc[present, present] if present else pd.DataFrame()

    return (
        accepted,
        suppressions,
        FactorSummary(
            candidates=len(candidates),
            accepted=len(accepted),
            suppressed=len(suppressions),
            mean_correlation=mean_correlation(subset),
            independent_bets=effective_bets(subset),
            window=window,
            threshold=threshold,
        ),
    )
