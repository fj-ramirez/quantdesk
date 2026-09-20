"""Derived series: values computed from other stored series (spec 6, phase 3).

A derived observation is stored in `observations` like any other, so everything
downstream -- get(), the change board, later PCA -- treats it identically. What
makes it different is where its as_of comes from.

**A derived value's as_of is the LATEST as_of among its inputs.** You could not
have computed it before its last ingredient arrived. Taking the earliest, or the
value date, would claim knowledge that did not exist, and taking a fresh
timestamp would destroy the point-in-time property for every derived series at
once.

**Its as_of_basis is the WEAKEST basis among its inputs.** A spread built from
one true vintage and one archive_floor observation is only as trustworthy as the
archive_floor leg; calling the result a source_vintage would launder the
weakness away.

**Inputs are joined on value_date, inner.** A date where any input is missing
produces no derived value. Nothing is forward-filled to fill the hole, and the
count of dropped dates is logged (spec 0.5, 7).

Re-running a derivation after an input is revised is correct and expected: the
new max(as_of) makes a new vintage, so the derived series accumulates its own
revision history alongside the inputs'.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pandas as pd

from .errors import DataIntegrityError, EmptyFetchError, UnknownSeriesError
from .logging import get_logger
from .models import Observation

# T79: was `import duckdb`, with a DuckDB connection type in every signature below.
# `store.db` is still the only module that knows the engine (its docstring always said
# so); `Connection` is the alias it exports, so the next swap is one line there.
from .store.db import Connection
from .store.query import get

log = get_logger("derive")

# Ordered weakest-first. The derived basis is the weakest input basis present.
BASIS_STRENGTH = ["archive_floor", "derived_lag", "source_vintage"]


@dataclass(frozen=True)
class Derivation:
    """One derived series: its inputs and the function over them."""

    target: str
    inputs: tuple[str, ...]
    fn: Callable[..., pd.Series]
    description: str
    # Units every input must share, as a sanity check against silently
    # subtracting a percent from an index.
    expect_input_unit: str | None = None
    # Observations the fn looks back over, for a rolling derivation. When set,
    # a row's as_of becomes the running maximum of input as_ofs across the
    # window rather than just its own: a rolling statistic only became
    # computable once the last observation it depends on had arrived, and a
    # late revision anywhere in the window moves that moment forward.
    window: int | None = None


def _weakest_basis(frame: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    """Per-row weakest as_of_basis across the input basis columns."""
    rank = {b: i for i, b in enumerate(BASIS_STRENGTH)}
    ranks = pd.DataFrame(
        {c: frame[c].map(lambda b: rank.get(b, 0)) for c in columns}
    )
    return ranks.min(axis=1).map(lambda i: BASIS_STRENGTH[int(i)])


def compute(
    conn: Connection,
    derivation: Derivation,
    start: date,
    end: date,
    as_of: datetime,
    source_batch: str,
) -> list[Observation]:
    """Evaluate one derivation over a date range, as the world looked at as_of."""
    frames = {}
    for series_id in derivation.inputs:
        df = get(conn, series_id, start, end, as_of=as_of)
        if df.empty:
            raise EmptyFetchError(
                f"{derivation.target}: input {series_id} has no observations in "
                f"{start}..{end} as of {as_of.isoformat()}. A derived series is "
                "not computed from a partial input set."
            )
        frames[series_id] = df[["value_date", "value", "as_of", "as_of_basis"]]

    merged = None
    for series_id, df in frames.items():
        renamed = df.rename(
            columns={
                "value": f"v__{series_id}",
                "as_of": f"a__{series_id}",
                "as_of_basis": f"b__{series_id}",
            }
        )
        merged = renamed if merged is None else merged.merge(
            renamed, on="value_date", how="inner"
        )

    total_dates = {len(f) for f in frames.values()}
    if merged is None or merged.empty:
        raise EmptyFetchError(
            f"{derivation.target}: inputs {list(derivation.inputs)} share no "
            "value_date in range. They are joined, never aligned by filling."
        )
    dropped = max(total_dates) - len(merged)
    if dropped:
        # Loud by design: these are dates where one leg was missing. The spread
        # simply does not exist on them (spec 7, holiday calendars differ).
        log.warning(
            "%s: %d date(s) dropped where an input was missing; nothing filled",
            derivation.target, dropped,
        )

    values = derivation.fn(
        *[merged[f"v__{s}"].astype(float) for s in derivation.inputs]
    )
    if not isinstance(values, pd.Series):
        raise DataIntegrityError(
            f"{derivation.target}: derivation fn returned "
            f"{type(values).__name__}, expected a pandas Series"
        )

    as_of_cols = [f"a__{s}" for s in derivation.inputs]
    basis_cols = [f"b__{s}" for s in derivation.inputs]
    # The latest input as_of: the moment the derived value became computable.
    row_as_of = merged[as_of_cols].max(axis=1)
    row_basis = _weakest_basis(merged, basis_cols)

    if derivation.window:
        # A rolling value depends on its whole window, so it was not computable
        # until the last of those observations arrived, and it is only as strong
        # as the weakest basis in the window.
        #
        # pandas cannot roll over datetimes, so the max is taken on the integer
        # representation and reinterpreted afterwards.
        #
        # The round trip goes through the ORIGINAL dtype rather than assuming
        # nanoseconds: pandas 3 stores datetime64 as microseconds by default, so
        # a to_datetime(..., unit="ns") reconstruction lands in January 1970.
        utc = pd.to_datetime(row_as_of, utc=True).astype("datetime64[ns, UTC]")
        rolled = (
            utc.astype("int64")
            .rolling(derivation.window, min_periods=1)
            .max()
            .astype("int64")
        )
        row_as_of = pd.to_datetime(rolled, unit="ns", utc=True)
        rank = {b: i for i, b in enumerate(BASIS_STRENGTH)}
        row_basis = (
            row_basis.map(rank)
            .rolling(derivation.window, min_periods=1)
            .min()
            .map(lambda i: BASIS_STRENGTH[int(i)])
        )

    out: list[Observation] = []
    skipped = 0
    # DuckDB hands DATE columns back as datetime64, so every value_date arriving
    # from a frame must be converted before it becomes an Observation.
    value_dates = pd.to_datetime(merged["value_date"]).dt.date
    for i, value_date in enumerate(value_dates):
        value = float(values.iloc[i])
        if pd.isna(value):
            skipped += 1
            continue
        out.append(
            Observation(
                series_id=derivation.target,
                value_date=value_date,
                # Normalised to UTC: the driver may return these in the local
                # zone, and the same instant under two labels reads as a
                # difference when it is not one.
                as_of=pd.Timestamp(row_as_of.iloc[i]).tz_convert(UTC).to_pydatetime(),
                value=value,
                source_batch=source_batch,
                as_of_basis=row_basis.iloc[i],
            )
        )
    if skipped:
        log.warning(
            "%s: %d row(s) produced a non-finite value and were not stored",
            derivation.target, skipped,
        )
    log.info(
        "%s: %d observations from %s", derivation.target, len(out),
        list(derivation.inputs),
    )
    return out


def check_units(conn: Connection, derivation: Derivation) -> None:
    """Refuse to difference series that are not in the same unit."""
    if derivation.expect_input_unit is None:
        return
    rows = conn.execute(
        "SELECT series_id, unit FROM series_metadata WHERE series_id IN "
        f"({','.join('?' * len(derivation.inputs))})",
        list(derivation.inputs),
    ).fetchall()
    found = dict(rows)
    missing = [s for s in derivation.inputs if s not in found]
    if missing:
        raise UnknownSeriesError(
            f"{derivation.target}: input(s) not registered: {missing}"
        )
    wrong = {s: u for s, u in found.items() if u != derivation.expect_input_unit}
    if wrong:
        raise DataIntegrityError(
            f"{derivation.target}: expects inputs in "
            f"{derivation.expect_input_unit!r} but got {wrong}. Differencing "
            "series in different units produces a number with no meaning."
        )


# Trailing window for positioning percentiles: three years of weekly reports
# (spec 2.2). Named rather than inlined, because changing it changes every
# positioning reading in the tool.
COT_PERCENTILE_WEEKS = 156


def rolling_percentile(values: pd.Series, window: int) -> pd.Series:
    """Where each value sits within its own trailing window, as 0-100.

    INCLUSIVE of the current observation, unlike the change board's z-score
    which must exclude it. The two answer different questions: the board asks
    "was today's move unusual relative to normal", so today cannot help define
    normal; this asks "where does the current position sit in its recent range",
    which is a statement about the range as it now stands.
    """
    return values.rolling(window, min_periods=window).apply(
        lambda w: 100.0 * (w <= w.iloc[-1]).sum() / len(w), raw=False
    )


# --- the derivations (spec 1.3, 2.2) ----------------------------------------

DERIVATIONS: tuple[Derivation, ...] = (
    Derivation(
        target="spread.5s30s",
        inputs=("ust.30y.nominal", "ust.5y.nominal"),
        fn=lambda long, short: long - short,
        description="30y minus 5y nominal yield. No FRED equivalent exists, "
                    "unlike 2s10s (T10Y2Y), so it is computed here.",
        expect_input_unit="pct",
    ),
    Derivation(
        target="credit.hy_ig.diff",
        inputs=("credit.hy.oas", "credit.ig.oas"),
        fn=lambda hy, ig: hy - ig,
        description="HY OAS minus IG OAS. Spec 2.2 tracks this separately from "
                    "HY alone: HY widening on its own is a different signal "
                    "from a parallel widening of both.",
        expect_input_unit="pct",
    ),
    Derivation(
        target="vol.vix9d_ratio",
        inputs=("vol.vix9d", "vol.vix"),
        fn=lambda short, spot: short / spot,
        description="VIX9D / VIX. Above 1 is backwardation at the front, a "
                    "strong regime marker (spec 2.2).",
        expect_input_unit="index",
    ),
    Derivation(
        target="vol.vix3m_ratio",
        inputs=("vol.vix3m", "vol.vix"),
        fn=lambda three_month, spot: three_month / spot,
        description="VIX3M / VIX. Below 1 is backwardation of the term "
                    "structure (spec 2.2).",
        expect_input_unit="index",
    ),
)

COT_PERCENTILES: tuple[Derivation, ...] = tuple(
    Derivation(
        target=f"pos.cot.{key}.pctile",
        inputs=(f"pos.cot.{key}",),
        fn=lambda net: rolling_percentile(net, COT_PERCENTILE_WEEKS),
        description=(
            f"pos.cot.{key} as a percentile of its own trailing "
            f"{COT_PERCENTILE_WEEKS}-week range. Spec 2.2: raw contract counts "
            "are not comparable across time as open interest grows, so the "
            "percentile is the readable figure and the count is the raw input."
        ),
        expect_input_unit="contracts",
        window=COT_PERCENTILE_WEEKS,
    )
    for key in ("ust10y", "es", "dxy", "crude", "gold")
)

DERIVATIONS = (*DERIVATIONS, *COT_PERCENTILES)

BY_TARGET = {d.target: d for d in DERIVATIONS}


def get_derivation(target: str) -> Derivation:
    try:
        return BY_TARGET[target]
    except KeyError:
        raise UnknownSeriesError(
            f"{target!r} is not a known derivation. Known: {sorted(BY_TARGET)}"
        ) from None
