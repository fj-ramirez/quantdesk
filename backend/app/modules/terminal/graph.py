"""The transmission graph (spec 4).

An explicit directed graph, stored as data rather than implied in code: which
series is expected to move which, with what sign, at what lag, and what the data
currently says about each of those claims.

WHY expected_sign CAN BE ZERO
-----------------------------
Spec 4 is emphatic about the equity/rates edge: "The equity/rates correlation
flipping sign is itself a regime signal, and a tool that assumes a fixed sign
will mislead precisely when it matters most." So zero is a first-class value
here, meaning "theory does not give a stable prior for this edge" -- not
"unknown" and not "no relationship". An edge with expected_sign = 0 can never
raise a sign conflict, because there is no sign to conflict with; what it
reports instead is where its correlation sits in its own history, which is the
regime signal itself.

WHAT sign_conflict ACTUALLY MEANS
---------------------------------
Only that the empirical beta disagrees with the prior AND is statistically
distinguishable from zero. A beta of the wrong sign that is indistinguishable
from noise is not a conflict, it is an absence of evidence, and flagging it
would fill the output with noise on exactly the quiet days when the graph has
least to say.

LAGS ARE IN TRADING DAYS
------------------------
typical_lag_days shifts the source series by that many ALIGNED OBSERVATIONS, not
calendar days. The two series are joined on dates they both traded first, so a
lag of 1 means "the previous day both markets were open", which is the
relationship anyone would actually trade. Using calendar days would silently
change the meaning of a lag across weekends and holidays.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from .errors import DataIntegrityError, EmptyFetchError, UnknownSeriesError
from .logging import get_logger

# T79: was `import duckdb`, with a DuckDB connection type in every signature below.
# `store.db` is still the only module that knows the engine (its docstring always said
# so); `Connection` is the alias it exports, so the next swap is one line there.
from .store.db import Connection
from .store.query import get, get_metadata

log = get_logger("graph")

# Trailing observations each beta and correlation is estimated over. Spec 7:
# "Always report the window and the percentile alongside the point estimate."
BETA_WINDOW = 250

# History of the rolling correlation that the percentile is measured against.
# Three years of trading days.
CORR_HISTORY_WINDOW = 756

# |t| above which a beta is treated as distinguishable from zero. Named because
# it decides which edges appear in the daily brief (spec 5) and which sign
# disagreements count as conflicts.
SIGNIFICANCE_T = 2.0

# Refuse to estimate an edge from fewer aligned observations than this.
MIN_EDGE_OBSERVATIONS = 60

# Correlation percentiles outside this band are worth surfacing: a relationship
# at the extreme of its own range is either unusually reliable or has broken.
CORR_EXTREME_LOW = 10.0
CORR_EXTREME_HIGH = 90.0


@dataclass(frozen=True)
class EdgeDefinition:
    """A claim about transmission, before any data is consulted."""

    from_series: str
    to_series: str
    expected_sign: int
    typical_lag_days: int
    chain: str
    note: str

    def __post_init__(self) -> None:
        if self.expected_sign not in (-1, 0, 1):
            raise DataIntegrityError(
                f"{self.from_series}->{self.to_series}: expected_sign must be "
                f"-1, 0 or +1, got {self.expected_sign}"
            )
        if self.typical_lag_days < 0:
            raise DataIntegrityError(
                f"{self.from_series}->{self.to_series}: a negative lag would "
                "make the target predict the source; reverse the edge instead"
            )


@dataclass
class EdgeStat:
    """What the data currently says about one edge."""

    definition: EdgeDefinition
    as_of: datetime
    value_date: date
    beta: float
    beta_window: int
    beta_t_stat: float
    r_squared: float
    corr: float
    corr_percentile: float
    corr_history_n: int
    n_obs: int

    @property
    def significant(self) -> bool:
        return bool(abs(self.beta_t_stat) >= SIGNIFICANCE_T)

    @property
    def sign_conflict(self) -> bool:
        """The prior and the data disagree, and the data is not just noise."""
        if self.definition.expected_sign == 0:
            return False
        if not self.significant:
            return False
        return int(np.sign(self.beta)) != self.definition.expected_sign

    @property
    def corr_extreme(self) -> str:
        if not np.isfinite(self.corr_percentile):
            return ""
        if self.corr_percentile <= CORR_EXTREME_LOW:
            return "low"
        if self.corr_percentile >= CORR_EXTREME_HIGH:
            return "high"
        return ""


# --- the seeded graph (spec 4) ----------------------------------------------
#
# Several of the chains spec 4 names cannot be computed from free data: the
# implied policy path has no history, and gold, the Russell 2000 and MSCI EM
# have no adapter. Those edges are still DEFINED, because spec 4 wants the graph
# stored as data and a graph that silently contains only the convenient edges
# hides its own shape. They are reported as uncomputable with the reason.

EDGES: tuple[EdgeDefinition, ...] = (
    # Chain: policy path -> 2y -> real yield -> equity duration
    EdgeDefinition(
        "policy.ff.meeting_1", "ust.2y.nominal", +1, 0, "policy",
        "The front of the policy path should lead the 2y almost mechanically. "
        "Uncomputable until the path accumulates history: CME publishes no "
        "settlement archive (adapters/cme.py).",
    ),
    EdgeDefinition(
        "ust.2y.nominal", "ust.10y.real", +1, 0, "policy",
        "Policy expectations transmit into the real curve.",
    ),
    EdgeDefinition(
        "ust.10y.real", "eq.ndx", -1, 0, "policy",
        "Real yields discount long-duration cash flows. eq.ndx stands in for "
        "the equity duration factor spec 4 names; it is the most duration-"
        "sensitive index in this universe, not a constructed factor.",
    ),
    # Chain: policy -> rate differentials -> dollar -> commodities -> EM
    EdgeDefinition(
        "ust.2y.nominal", "fx.usd.broad", +1, 0, "dollar",
        "PROXY: a rate DIFFERENTIAL drives the dollar, and this is the US leg "
        "only. It will understate the relationship whenever foreign front-end "
        "rates move with US rates, which is often.",
    ),
    EdgeDefinition(
        "fx.usd.broad", "cmdty.wti", -1, 0, "dollar",
        "Dollar-denominated commodities cheapen for non-dollar buyers as the "
        "dollar weakens.",
    ),
    EdgeDefinition(
        "cmdty.wti", "eq.msci_em", +1, 1, "dollar",
        "Uncomputable: MSCI EM is not available on FRED and needs the prices "
        "adapter (phase 4).",
    ),
    # Chain: real yield -> gold
    EdgeDefinition(
        "ust.10y.real", "cmdty.gold", -1, 0, "gold",
        "Gold pays no coupon, so a higher real yield raises the cost of "
        "holding it. Uncomputable: both FRED LBMA series are retired.",
    ),
    # Chain: credit -> equity risk premium -> small caps
    EdgeDefinition(
        "credit.hy.oas", "eq.spx", -1, 0, "credit",
        "Wider high-yield spreads and a higher equity risk premium are two "
        "views of the same repricing. eq.spx stands in for the ERP itself.",
    ),
    EdgeDefinition(
        "credit.hy.oas", "eq.rut", -1, 0, "credit",
        "Small caps carry more credit sensitivity than large. Uncomputable: "
        "the Russell 2000 is not on FRED (RU2000PR is retired).",
    ),
    # Chain: oil -> breakevens -> policy (the feedback edge)
    EdgeDefinition(
        "cmdty.wti", "be.5y", +1, 0, "feedback",
        "Energy passes into inflation compensation at the front of the curve.",
    ),
    EdgeDefinition(
        "be.5y", "policy.ff.meeting_1", +1, 1, "feedback",
        "The feedback edge that closes spec 4's loop. Uncomputable until the "
        "policy path has history.",
    ),
    EdgeDefinition(
        "be.5y", "ust.2y.nominal", +1, 1, "feedback",
        "SUBSTITUTE for the edge above while the policy path is empty: the 2y "
        "carries policy expectations, so inflation compensation feeding back "
        "into expected policy should show here first.",
    ),
    # The edge spec 4 singles out as the reason for the whole exercise.
    EdgeDefinition(
        "ust.10y.nominal", "eq.spx", 0, 0, "regime",
        "expected_sign is deliberately ZERO. This correlation flips with the "
        "regime -- negative when growth leads, positive when inflation does -- "
        "and spec 4 says a tool assuming a fixed sign misleads precisely when "
        "it matters. Read corr_percentile, not the sign.",
    ),
    EdgeDefinition(
        "vol.vix", "credit.hy.oas", +1, 0, "credit",
        "Equity volatility and credit spreads price the same risk aversion.",
    ),
    EdgeDefinition(
        "ust.10y.real", "eq.spx", 0, 0, "regime",
        "Also regime-dependent, and the cleaner read on the duration channel "
        "than the nominal edge, since it strips inflation compensation out.",
    ),
)

BY_PAIR = {(e.from_series, e.to_series): e for e in EDGES}


def check_definitions() -> None:
    """Guard against a duplicated or self-referential edge."""
    seen: set[tuple[str, str]] = set()
    for e in EDGES:
        key = (e.from_series, e.to_series)
        if key in seen:
            raise DataIntegrityError(f"duplicate edge {key}")
        if e.from_series == e.to_series:
            raise DataIntegrityError(f"self-edge {key}")
        seen.add(key)


def register(conn: Connection) -> int:
    """Write the edge definitions. Idempotent; definitions are descriptive."""
    check_definitions()
    for e in EDGES:
        conn.execute(
            """
            INSERT INTO edge_definitions
                (from_series, to_series, expected_sign, typical_lag_days, chain, note)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (from_series, to_series) DO UPDATE SET
                expected_sign = excluded.expected_sign,
                typical_lag_days = excluded.typical_lag_days,
                chain = excluded.chain,
                note = excluded.note
            """,
            [e.from_series, e.to_series, e.expected_sign, e.typical_lag_days,
             e.chain, e.note],
        )
    return len(EDGES)


# --- estimation --------------------------------------------------------------


def _changes(
    conn: Connection,
    series_id: str,
    as_of: datetime,
    lookback_days: int,
    max_gap_days: int,
) -> pd.Series:
    from .analytics.transforms import apply_transform

    meta = get_metadata(conn, series_id)
    if meta.empty:
        raise UnknownSeriesError(f"graph: {series_id} is not registered")
    row = meta.iloc[0]
    start = as_of.date() - timedelta(days=lookback_days)
    obs = get(conn, series_id, start, as_of.date(), as_of=as_of, warn_empty=False)
    if obs.empty:
        raise EmptyFetchError(
            f"graph: {series_id} has no observations as of {as_of.isoformat()}"
        )
    cs = apply_transform(
        obs, series_id, row["default_transform"], row["unit"], max_gap_days
    )
    usable = cs.usable()
    if usable.empty:
        raise EmptyFetchError(f"graph: {series_id} has no gap-clean changes")
    return pd.Series(
        usable["change"].to_numpy(),
        index=pd.to_datetime(usable["value_date"]).dt.date,
        name=series_id,
    )


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """beta, t-statistic, r-squared for y regressed on x with an intercept."""
    n = x.size
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    if sxx == 0:
        return float("nan"), float("nan"), float("nan")
    beta = float(((x - xm) * (y - ym)).sum() / sxx)
    resid = y - (ym + beta * (x - xm))
    dof = n - 2
    if dof <= 0:
        return beta, float("nan"), float("nan")
    resid_var = float((resid**2).sum()) / dof
    se = float(np.sqrt(resid_var / sxx)) if sxx > 0 else float("nan")
    t = beta / se if se and np.isfinite(se) and se > 0 else float("nan")
    syy = float(((y - ym) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / syy if syy > 0 else float("nan")
    return beta, t, r2


def estimate(
    conn: Connection,
    edge: EdgeDefinition,
    as_of: datetime,
    beta_window: int = BETA_WINDOW,
    corr_history: int = CORR_HISTORY_WINDOW,
    max_gap_days: int = 5,
    min_obs: int = MIN_EDGE_OBSERVATIONS,
) -> EdgeStat:
    """Estimate one edge as of a moment."""
    lookback = int((beta_window + corr_history) * 1.75)
    source = _changes(conn, edge.from_series, as_of, lookback, max_gap_days)
    target = _changes(conn, edge.to_series, as_of, lookback, max_gap_days)

    joined = pd.concat([source, target], axis=1, join="inner").dropna()
    if joined.empty:
        raise EmptyFetchError(
            f"graph: {edge.from_series} and {edge.to_series} share no dates. "
            "They are joined on days both traded, never filled (spec 7)."
        )

    # The lag is applied AFTER alignment, so it counts days both markets were
    # open rather than calendar days.
    if edge.typical_lag_days:
        joined[edge.from_series] = joined[edge.from_series].shift(
            edge.typical_lag_days
        )
        joined = joined.dropna()

    if len(joined) < min_obs:
        raise EmptyFetchError(
            f"graph: {edge.from_series}->{edge.to_series} has {len(joined)} "
            f"aligned observations, needs {min_obs}. A beta from a handful of "
            "points is a coincidence with a standard error."
        )

    window = joined.tail(beta_window)
    x = window[edge.from_series].to_numpy(dtype=float)
    y = window[edge.to_series].to_numpy(dtype=float)
    beta, t_stat, r2 = _ols(x, y)
    corr = float(np.corrcoef(x, y)[0, 1]) if x.size > 1 else float("nan")

    # Where this correlation sits in its own history. Spec 7: a fixed-window
    # correlation is an average over regimes, so the point estimate alone is
    # not interpretable.
    rolling = (
        joined[edge.from_series]
        .rolling(beta_window)
        .corr(joined[edge.to_series])
        .dropna()
    )
    history = rolling.tail(corr_history)
    if len(history) >= 2 and np.isfinite(corr):
        percentile = 100.0 * float((history.to_numpy() <= corr).sum()) / len(history)
    else:
        percentile = float("nan")

    return EdgeStat(
        definition=edge,
        as_of=as_of,
        value_date=window.index[-1],
        beta=beta,
        beta_window=beta_window,
        beta_t_stat=t_stat,
        r_squared=r2,
        corr=corr,
        corr_percentile=percentile,
        corr_history_n=len(history),
        n_obs=len(window),
    )


def estimate_all(
    conn: Connection,
    as_of: datetime,
    **kwargs,
) -> tuple[list[EdgeStat], dict[tuple[str, str], str]]:
    """Estimate every defined edge. Returns the stats and the ones that could
    not be computed, with reasons."""
    check_definitions()
    stats: list[EdgeStat] = []
    skipped: dict[tuple[str, str], str] = {}
    for e in EDGES:
        try:
            stats.append(estimate(conn, e, as_of, **kwargs))
        except (EmptyFetchError, UnknownSeriesError) as err:
            skipped[(e.from_series, e.to_series)] = str(err)
            log.info("graph: %s->%s not computed: %s", e.from_series, e.to_series, err)
    if not stats:
        raise EmptyFetchError(
            "graph: no edge could be estimated. Check that the universe has "
            "been ingested and derived."
        )
    return stats, skipped


def persist(
    conn: Connection, stats: list[EdgeStat], source_batch: str
) -> int:
    """Store a run's estimates. Each run adds rows keyed by as_of rather than
    overwriting, so the history of a relationship survives."""
    for s in stats:
        conn.execute(
            """
            INSERT INTO edge_stats (
                from_series, to_series, as_of, value_date, beta, beta_window,
                beta_t_stat, r_squared, corr, corr_percentile, corr_history_n,
                sign_conflict, significant, n_obs, source_batch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (from_series, to_series, as_of) DO NOTHING
            """,
            [
                s.definition.from_series, s.definition.to_series, s.as_of,
                s.value_date, s.beta, s.beta_window, s.beta_t_stat, s.r_squared,
                s.corr, s.corr_percentile, s.corr_history_n, s.sign_conflict,
                s.significant, s.n_obs, source_batch,
            ],
        )
    return len(stats)
