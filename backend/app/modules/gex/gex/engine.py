"""GEX engine — dealer gamma exposure from a normalized option chain.

Pure functions over a :class:`~app.modules.gex.models.chain.ChainSnapshot` (or the ``DataFrame`` built
from one by :func:`to_frame`). **No I/O**: no HTTP, no database, no filesystem, no logging.
Everything here is deterministic given a snapshot, so it is equally usable from the capture
job (T09), the read API (T11) and a backtest loop (T25).

This module is the product. Every number the dashboard shows is computed here.


The core quantity
-----------------

Per PLAN.md §3 item 1, for one contract::

    contract_gex = dealer_sign · gamma · open_interest · multiplier · spot² · 0.01

* ``gamma`` — spot gamma, ∂²V/∂S², **unsigned**, from :mod:`app.modules.gex.gex.greeks` recomputed at
  ``spot`` with the contract's own IV. Vendor gamma is available as a cross-check via
  ``use_vendor_gamma=True`` and is never the default (PLAN.md §2).
* ``dealer_sign`` — **+1 for calls, −1 for puts**, the standard "dealers are long calls and
  short puts" convention. It is applied **exactly once, here**. ``greeks.gamma()`` returns an
  unsigned number for both rights by design, and ``OptionContract.gamma`` stores the vendor's
  unsigned number; neither carries this sign.
* ``open_interest`` — contracts. ``None`` means *unknown* and the contract is **excluded**;
  ``0`` means genuinely zero and the contract is *included* and contributes exactly 0. See
  docs/schema.md; collapsing the two would silently invent open interest.
* ``multiplier`` — read **per contract** from ``OptionContract.multiplier``, never hardcoded
  to 100, so an adjusted contract with a non-standard multiplier cannot silently misprice.
* ``spot² · 0.01`` — converts "delta per 1.00 of index" into "dollars of delta per **1 %**
  move of the index". A 1 % move is ``0.01·S`` index points, and the delta change over that
  move is ``gamma·0.01·S`` per unit, worth ``gamma·0.01·S·S`` dollars per unit.

**Units: US dollars of dealer delta acquired per +1 % move in the underlying.** Positive net
GEX means dealers buy weakness and sell strength (a pinning, vol-suppressing regime); negative
net GEX means they chase the move.

Sign summary, because a doubled sign is the classic way to get this wrong:

============================  ===========================================================
``greeks.gamma()``            unsigned, ≥ 0 for calls and puts alike
``OptionContract.gamma``      unsigned, as the vendor reports it
``contract_gex()``            **signed** (+ calls, − puts) — the sign enters exactly here
``StrikeGex.call_gex``        ≥ 0 always
``StrikeGex.put_gex``         ≤ 0 always
``StrikeGex.net_gex``         ``call_gex + put_gex``
``*.abs_gex``                 Σ|contract gex|, sign-blind gamma concentration
============================  ===========================================================


Expiry, settlement and the "as of" instant
------------------------------------------

Time to expiry is measured from ``snapshot.captured_at`` — the effective time of the *data*,
not the wall clock — and settlement fixes the expiry *time*: AM-settled contracts (root
``SPX``) expire at 09:30 New York, PM-settled ones (``SPXW``, ``SPY``, ``QQQ``) at 16:00. On
one calendar date an AM and a PM contract therefore have times to expiry differing by 6.5
hours, which matters enormously for a 0DTE gamma. ``settlement`` is read per contract; it is
never inferred from the date. SPX and SPXW merge into a single ``SPX`` underlying and sum into
the same strike bucket (PLAN.md §3 item 2) — intended, and correct, since dealers are short
gamma against both series.

**Expired contracts are dropped.** ``greeks.time_to_expiry`` floors T at one minute, so an
already-expired contract would otherwise look like a fresh one-minute option and inject a
spurious, enormous ATM gamma spike. :func:`to_frame` calls ``greeks.is_expired`` and records
the mask; every aggregate here honours it. On a 14:58 NY snapshot the whole AM-settled series
expiring that morning is dead, while the PM series of the same date is very much alive.


Implied volatility policy — the decision this module had to make
-----------------------------------------------------------------

The live SPX chain carries per-contract IVs from 0.054 to about **8.3 (830 %)**. These are
genuine vendor inversion artifacts, not a units bug: they cluster on 0DTE wings quoted
0.00 bid / 0.05 ask (a one-tick ask on a worthless option inverts to an unbounded vol) and on
deep-ITM contracts whose entire value is intrinsic. A further ~1,900 contracts report Cboe's
``iv: 0.0`` sentinel, which the schema maps to ``None``.

Recomputed gamma at such a vol is not large — a huge σ√T *flattens* gamma rather than spiking
it — but it is **wrong in a specific, directional way**: it smears a broad, non-decaying
gamma contribution across the entire ±10 % profile grid for a contract that in truth has
essentially none. With open interest in the tens of thousands on some of those 0DTE wings,
that is a real, if modest, bias in the wings of the gamma profile, exactly where the flip
point is often found.

**Policy (default): exclude, account, and reconcile.**

* Contracts whose IV falls outside ``[iv_min, iv_max]`` = ``[0.01, 3.0]`` are excluded.
  3.0 (300 %) is generous — SPX realized/implied vol does not exceed it outside a crash, and
  0DTE wings legitimately reach 150–200 % — so the cut removes artifacts, not market data.
* **Nothing is filtered silently.** :class:`GexDiagnostics`, returned on every
  :class:`GexResult`, reports how many contracts were excluded, their open interest, and
  ``extreme_iv_gex_excluded`` — the exact signed dollar GEX the excluded contracts *would*
  have contributed. ``net_gex + extreme_iv_gex_excluded == net_gex_iv_unfiltered``, also
  reported, so T10 can reconcile against a vendor figure in one subtraction instead of
  re-running the engine.
* **It is configurable**, per call, via :class:`IvPolicy`: ``EXCLUDE`` (default), ``CLAMP``
  (winsorize IV into the band and keep the contract) or ``KEEP`` (use the vendor's IV
  verbatim, no cut at all). ``KEEP`` is the right setting when comparing against a vendor
  known not to filter.

Why exclusion rather than clamping or repair: clamping to 3.0 substitutes an equally
fictitious vol and keeps a fictitious contribution, merely a smaller one; repairing the IV by
interpolating the expiry's smile is a modelling decision with its own failure modes that does
not belong in the aggregation layer. Excluding says exactly what is true — "this contract's
vol is not measurable, so it is not priced" — and the diagnostics make the omission
auditable. ``CLAMP`` is offered for anyone who prefers the other trade-off.

**Contracts with no IV at all can never be repriced and are always excluded**, under every
policy, since gamma is not computable without a vol. That hole is quantified too:
``missing_iv_gex_vendor`` reports what those contracts would contribute using the *vendor's*
gamma, which is the only estimate available for them.


Filters
-------

:class:`ExpiryFilter` selects which expiries enter an aggregate; an explicit iterable of
``datetime.date`` is also accepted anywhere a filter is.

============  ===================================================================
``ALL``       every expiry
``ZERO_DTE``  expiry date == the snapshot's New York date
``EX_ZERO_DTE``  the complement of ``ZERO_DTE``
``THIS_WEEK``  expiry in the same ISO week (Mon–Sun) as the snapshot's NY date
``MONTHLY_ONLY``  standard monthlies: the third Friday of the month, **or** any
              AM-settled contract
============  ===================================================================

The ``MONTHLY_ONLY`` union is not redundant. On the live chain every AM-settled SPX expiry is
a third Friday except ``2027-06-17`` (a Thursday) — the AM series *is* the monthly series by
definition, so keying on settlement catches it, while the third-Friday rule catches SPXW and
SPY/QQQ monthlies, which are PM-settled. For SPY and QQQ, where everything is PM, the rule
reduces to plain third-Friday.

Filtering happens *before* aggregation and does not interact with the eligibility rules
above: a contract is included iff it survives the expiry filter **and** is not expired **and**
has known open interest **and** has a usable IV.


Performance
-----------

``compute_all`` on a full 28,650-contract SPX chain, including the 201-point gamma profile
(≈5.8 M gamma evaluations), runs well inside the 2 s budget. The profile keeps per-contract
arrays shaped ``(1, n)`` and lets the ``(M, 1)`` spot grid broadcast, as
:mod:`app.modules.gex.gex.greeks` documents; pre-flattening to ``(M, N)`` measures about 2× slower. The
grid sum is a single ``(M, n) @ (n,)`` matmul rather than a Python loop over grid points.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from app.modules.gex.gex import greeks
from app.modules.gex.models.chain import ChainSnapshot, Right, Settlement

__all__ = [
    "DEFAULT_IV_POLICY",
    "DEFAULT_PROFILE_STEP",
    "DEFAULT_PROFILE_WIDTH",
    "DEFAULT_TOP_N",
    "FRAME_COLUMNS",
    "PCT_MOVE",
    "ExpiryFilter",
    "ExpiryGex",
    "GammaProfile",
    "GexDiagnostics",
    "GexResult",
    "IvPolicy",
    "IvPolicyMode",
    "KeyLevels",
    "ProfilePoint",
    "SnapshotMeta",
    "StrikeGex",
    "abs_gex",
    "by_expiry",
    "by_strike",
    "compute_all",
    "contract_gex",
    "expiry_mask",
    "flip_point",
    "gamma_profile",
    "include_mask",
    "key_levels",
    "to_frame",
]

#: The move size dollar gamma is quoted per: 1 % = 0.01. PLAN.md §3's ``spot² × 0.01``.
PCT_MOVE = 0.01

#: Gamma-profile grid: ±10 % of spot in 0.1 % steps → 201 points inclusive (TASKS.md T08).
DEFAULT_PROFILE_WIDTH = 0.10
DEFAULT_PROFILE_STEP = 0.001

#: How many strikes :func:`key_levels` reports on each side.
DEFAULT_TOP_N = 5

#: Columns :func:`to_frame` guarantees. Anything downstream may rely on these names.
#:
#: ``bid``/``ask`` were added by T39 and are the only columns here that no GEX aggregate
#: reads. They exist because :mod:`app.modules.gex.gex.report`'s premium-selling screen needs a mid
#: price per contract, and the alternative -- a second pass over ``snapshot.contracts``
#: alongside the frame -- would have given the report a different contract population than
#: every other figure in it. They were already on ``OptionContract`` and in Parquet; only
#: the frame was missing them. Purely additive: nothing in this module consumes them.
FRAME_COLUMNS = (
    "occ_symbol",
    "root",
    "expiry",
    "settlement",
    "strike",
    "right",
    "sign",
    "open_interest",
    "iv",
    "vendor_gamma",
    "multiplier",
    "volume",
    "bid",
    "ask",
    "t",
    "dte",
    "expired",
    "zero_dte",
    "this_week",
    "monthly",
)


# --------------------------------------------------------------------------------------
# Enums and policy
# --------------------------------------------------------------------------------------


class ExpiryFilter(StrEnum):
    """Which expiries enter an aggregate. See the module docstring for exact definitions."""

    ALL = "ALL"
    ZERO_DTE = "ZERO_DTE"
    THIS_WEEK = "THIS_WEEK"
    MONTHLY_ONLY = "MONTHLY_ONLY"
    EX_ZERO_DTE = "EX_ZERO_DTE"


class IvPolicyMode(StrEnum):
    """What to do with an implied vol outside the plausible band.

    ``EXCLUDE`` drops the contract (default); ``CLAMP`` winsorizes its IV into the band and
    keeps it; ``KEEP`` uses the vendor's IV verbatim and applies no band at all. Under every
    mode a contract with **no** IV is excluded — gamma is not computable without a vol.
    """

    EXCLUDE = "EXCLUDE"
    CLAMP = "CLAMP"
    KEEP = "KEEP"


@dataclass(frozen=True, slots=True)
class IvPolicy:
    """Implied-volatility admission policy. See the module docstring for the reasoning.

    Args:
        mode: :class:`IvPolicyMode`. Default ``EXCLUDE``.
        iv_min: Lower bound, decimal fraction. 0.01 = 1 % annualized; anything below is a
            vendor artifact rather than a quote.
        iv_max: Upper bound, decimal fraction. 3.0 = 300 %, above the highest vol SPX
            legitimately prints (0DTE wings reach ~1.5–2.0) and below the 4–8 range where the
            inversion artifacts live.
    """

    mode: IvPolicyMode = IvPolicyMode.EXCLUDE
    iv_min: float = 0.01
    iv_max: float = 3.0

    def __post_init__(self) -> None:
        if not 0.0 < self.iv_min < self.iv_max:
            raise ValueError(f"need 0 < iv_min < iv_max, got {self.iv_min} / {self.iv_max}")
        # Normalize a plain string so IvPolicy(mode="KEEP") behaves like the enum member.
        object.__setattr__(self, "mode", IvPolicyMode(self.mode))


#: The policy every public entry point uses unless told otherwise.
DEFAULT_IV_POLICY = IvPolicy()


# --------------------------------------------------------------------------------------
# Result records
# --------------------------------------------------------------------------------------


def _f(x: Any) -> float | None:
    """NumPy/None → JSON-safe float. NaN and infinities become ``None``, never a fake 0."""
    if x is None:
        return None
    value = float(x)
    return value if np.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class StrikeGex:
    """Dollar GEX at one strike, summed across every expiry the filter admitted.

    ``call_gex`` is ≥ 0, ``put_gex`` ≤ 0, ``net_gex`` is their sum, and ``abs_gex`` is the
    sign-blind total. ``contracts`` and ``open_interest`` are carried for auditing a level.
    """

    strike: float
    call_gex: float
    put_gex: float
    net_gex: float
    abs_gex: float
    contracts: int = 0
    open_interest: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strike": _f(self.strike),
            "call_gex": _f(self.call_gex),
            "put_gex": _f(self.put_gex),
            "net_gex": _f(self.net_gex),
            "abs_gex": _f(self.abs_gex),
            "contracts": int(self.contracts),
            "open_interest": int(self.open_interest),
        }


@dataclass(frozen=True, slots=True)
class ExpiryGex:
    """Dollar GEX for one expiry date, summed across strikes and both roots.

    ``dte`` is calendar days from the snapshot's New York date to the expiry date, so a 0DTE
    expiry is 0 and yesterday's is negative (and already dropped as expired).
    """

    expiry: dt.date
    dte: int
    call_gex: float
    put_gex: float
    net_gex: float
    abs_gex: float
    contracts: int = 0
    open_interest: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "expiry": self.expiry.isoformat(),
            "dte": int(self.dte),
            "call_gex": _f(self.call_gex),
            "put_gex": _f(self.put_gex),
            "net_gex": _f(self.net_gex),
            "abs_gex": _f(self.abs_gex),
            "contracts": int(self.contracts),
            "open_interest": int(self.open_interest),
        }


@dataclass(frozen=True, slots=True)
class ProfilePoint:
    """One point of the gamma profile: total dealer GEX *if* spot were ``spot``.

    ``spot`` is a hypothetical index level from the grid, not the snapshot's actual spot.
    """

    spot: float
    total_gex: float

    def to_dict(self) -> dict[str, Any]:
        return {"spot": _f(self.spot), "total_gex": _f(self.total_gex)}


@dataclass(frozen=True, slots=True)
class KeyLevels:
    """The headline numbers: net exposure, walls, and the gamma flip.

    **The walls are defined on net GEX**, which is the convention SpotGamma and comparable
    vendors publish and therefore the one T10 validates against: ``call_wall`` is the strike
    with the largest positive net GEX and ``put_wall`` the strike with the most negative.
    ``max_net_strike`` / ``min_net_strike`` are the same two numbers under unambiguous names,
    kept because "wall" is a market term and these are not.

    The per-side reading is available too, as ``max_call_gex_strike`` (largest call-side
    dollar gamma) and ``max_put_gex_strike`` (most negative put-side). It is genuinely useful
    for asking "where is the call open interest" — but it is *not* the wall, because the two
    sides routinely peak on the **same** strike. On the live SPX chain the round-number 8000
    strike carries the most gamma on both sides at once and owns both per-side extrema, which
    reports "call wall 8000, put wall 8000" and brackets nothing. The net-based definition on
    the same chain returns 7800 above spot and 7500 below, which is what a wall is for.

    Every level is ``None`` when the filter admitted no contracts at all (a legitimate case:
    ``ZERO_DTE`` on a day with no expiry). ``flip_point`` is ``None`` when the profile has no
    sign change inside the grid — see :func:`flip_point`.
    """

    net_gex: float
    call_gex: float
    put_gex: float
    abs_gex: float
    call_wall: float | None
    call_wall_gex: float | None
    put_wall: float | None
    put_wall_gex: float | None
    max_abs_strike: float | None
    max_abs_gex: float | None
    max_net_strike: float | None
    min_net_strike: float | None
    flip_point: float | None
    spot: float | None
    computed_at: dt.datetime | None
    max_call_gex_strike: float | None = None
    max_call_gex: float | None = None
    max_put_gex_strike: float | None = None
    max_put_gex: float | None = None
    top_positive: tuple[StrikeGex, ...] = ()
    top_negative: tuple[StrikeGex, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "net_gex": _f(self.net_gex),
            "call_gex": _f(self.call_gex),
            "put_gex": _f(self.put_gex),
            "abs_gex": _f(self.abs_gex),
            "call_wall": _f(self.call_wall),
            "call_wall_gex": _f(self.call_wall_gex),
            "put_wall": _f(self.put_wall),
            "put_wall_gex": _f(self.put_wall_gex),
            "max_abs_strike": _f(self.max_abs_strike),
            "max_abs_gex": _f(self.max_abs_gex),
            "max_net_strike": _f(self.max_net_strike),
            "min_net_strike": _f(self.min_net_strike),
            "max_call_gex_strike": _f(self.max_call_gex_strike),
            "max_call_gex": _f(self.max_call_gex),
            "max_put_gex_strike": _f(self.max_put_gex_strike),
            "max_put_gex": _f(self.max_put_gex),
            "flip_point": _f(self.flip_point),
            "spot": _f(self.spot),
            "computed_at": None if self.computed_at is None else self.computed_at.isoformat(),
            "top_positive": [s.to_dict() for s in self.top_positive],
            "top_negative": [s.to_dict() for s in self.top_negative],
        }


@dataclass(frozen=True, slots=True)
class GexDiagnostics:
    """What was left out, and what leaving it out cost — the audit trail for every aggregate.

    Counts are over the contracts the **expiry filter admitted**, so they describe exactly the
    population behind the numbers in the same :class:`GexResult`. Every ``*_gex_*`` figure is
    signed dollar GEX at the snapshot's spot, on the same scale as ``net_gex``.

    Attributes:
        contracts: Rows the expiry filter admitted.
        included: Rows that actually contributed (not expired, OI known, IV usable).
        expired: Dropped because their expiry instant is at or before ``captured_at``.
        missing_open_interest: Dropped because ``open_interest is None`` ("unknown"). Note
            that ``open_interest == 0`` is *not* here — it is genuine and contributes 0.
        zero_open_interest: Included, contributing exactly 0. Informational.
        missing_iv: Dropped because the vendor reported no IV (Cboe's ``iv: 0.0`` sentinel).
            Never repriceable under any policy.
        missing_iv_gex_vendor: What those contracts would contribute using the *vendor's*
            gamma — the only estimate available for them. Not included in ``net_gex``.
        extreme_iv: Contracts whose IV fell outside the policy band.
        extreme_iv_open_interest: Their total open interest.
        extreme_iv_gex_excluded: Signed dollar GEX removed by the policy, i.e. what those
            contracts would have contributed under ``KEEP`` minus what they contribute now.
            0.0 under ``KEEP``.
        net_gex_iv_unfiltered: ``net_gex + extreme_iv_gex_excluded``. The figure to compare
            against a vendor that applies no IV filter.
        iv_min_observed / iv_max_observed: Range of IVs actually present, before the policy.
    """

    contracts: int
    included: int
    expired: int
    missing_open_interest: int
    zero_open_interest: int
    missing_iv: int
    missing_iv_gex_vendor: float
    extreme_iv: int
    extreme_iv_open_interest: int
    extreme_iv_gex_excluded: float
    net_gex_iv_unfiltered: float
    iv_min_observed: float | None
    iv_max_observed: float | None
    iv_policy_mode: IvPolicyMode = IvPolicyMode.EXCLUDE
    iv_policy_min: float = DEFAULT_IV_POLICY.iv_min
    iv_policy_max: float = DEFAULT_IV_POLICY.iv_max
    use_vendor_gamma: bool = False
    extreme_iv_examples: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "contracts": int(self.contracts),
            "included": int(self.included),
            "expired": int(self.expired),
            "missing_open_interest": int(self.missing_open_interest),
            "zero_open_interest": int(self.zero_open_interest),
            "missing_iv": int(self.missing_iv),
            "missing_iv_gex_vendor": _f(self.missing_iv_gex_vendor),
            "extreme_iv": int(self.extreme_iv),
            "extreme_iv_open_interest": int(self.extreme_iv_open_interest),
            "extreme_iv_gex_excluded": _f(self.extreme_iv_gex_excluded),
            "net_gex_iv_unfiltered": _f(self.net_gex_iv_unfiltered),
            "iv_min_observed": _f(self.iv_min_observed),
            "iv_max_observed": _f(self.iv_max_observed),
            "iv_policy_mode": str(self.iv_policy_mode),
            "iv_policy_min": _f(self.iv_policy_min),
            "iv_policy_max": _f(self.iv_policy_max),
            "use_vendor_gamma": bool(self.use_vendor_gamma),
            "extreme_iv_examples": list(self.extreme_iv_examples),
        }


@dataclass(frozen=True, slots=True)
class SnapshotMeta:
    """Provenance echoed onto every result so a consumer never needs a second fetch.

    Deliberately *without* ``id`` and ``is_eod``: those are storage concerns owned by T09/T11,
    and this module does no I/O. T11 merges them in.
    """

    underlying: str
    spot: float
    captured_at: dt.datetime
    source: str
    delayed_minutes: int
    contract_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": str(self.underlying),
            "spot": _f(self.spot),
            "captured_at": self.captured_at.isoformat(),
            "source": self.source,
            "delayed_minutes": int(self.delayed_minutes),
            "contract_count": int(self.contract_count),
        }


@dataclass(frozen=True, slots=True)
class GammaProfile:
    """Total dealer GEX as a function of a hypothetical spot.

    Unpacks as the ``(spot_grid, total_gex)`` pair TASKS.md T08 specifies::

        spot_grid, total_gex = gamma_profile(df, spot)

    while still carrying ``spot`` (the snapshot's actual level), which :func:`flip_point`
    needs in order to pick the crossing *nearest* to the market.
    """

    spot: float
    spot_grid: np.ndarray
    total_gex: np.ndarray

    def __iter__(self):
        yield self.spot_grid
        yield self.total_gex

    def __len__(self) -> int:
        return int(self.spot_grid.size)

    def points(self) -> tuple[ProfilePoint, ...]:
        return tuple(
            ProfilePoint(spot=float(s), total_gex=float(g))
            for s, g in zip(self.spot_grid, self.total_gex, strict=True)
        )


@dataclass(frozen=True, slots=True)
class GexResult:
    """Everything :func:`compute_all` produces for one snapshot and one expiry filter.

    Field names follow ``frontend/src/api/types.ts`` so that T11 is a pass-through rather than
    a translation layer. :meth:`to_dict` is plain JSON — floats, ints, strings, ISO dates,
    and ``None`` for anything genuinely absent.
    """

    underlying: str
    filter: str
    spot: float
    snapshot: SnapshotMeta
    levels: KeyLevels
    by_strike: tuple[StrikeGex, ...]
    by_expiry: tuple[ExpiryGex, ...]
    profile: tuple[ProfilePoint, ...]
    diagnostics: GexDiagnostics
    expiries: tuple[dt.date, ...] = field(default=())

    @property
    def net_gex(self) -> float:
        """Convenience alias for ``levels.net_gex``."""
        return self.levels.net_gex

    @property
    def flip_point(self) -> float | None:
        """Convenience alias for ``levels.flip_point``."""
        return self.levels.flip_point

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": str(self.underlying),
            "filter": str(self.filter),
            "spot": _f(self.spot),
            "snapshot": self.snapshot.to_dict(),
            "levels": self.levels.to_dict(),
            "by_strike": [s.to_dict() for s in self.by_strike],
            "by_expiry": [e.to_dict() for e in self.by_expiry],
            "profile": [p.to_dict() for p in self.profile],
            "diagnostics": self.diagnostics.to_dict(),
            "expiries": [e.isoformat() for e in self.expiries],
        }


# --------------------------------------------------------------------------------------
# Frame construction
# --------------------------------------------------------------------------------------


def _third_friday(day: dt.date) -> dt.date:
    """The third Friday of ``day``'s month — the standard US monthly expiration date."""
    first = day.replace(day=1)
    return first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)


def to_frame(snapshot: ChainSnapshot, *, now: dt.datetime | None = None) -> pd.DataFrame:
    """Flatten a :class:`~app.modules.gex.models.chain.ChainSnapshot` into the engine's working frame.

    Everything time-dependent is resolved once, here, against a single "as of" instant, so
    the rest of the module is a pure function of the frame:

    * ``t`` — time to expiry in years, per contract, honouring AM (09:30 NY) versus PM
      (16:00 NY) settlement. Floored at one minute by :func:`greeks.time_to_expiry`.
    * ``expired`` — ``greeks.is_expired``. **Must** be consulted before ``t``, whose floor
      makes a dead contract look like a live one-minute option with enormous ATM gamma.
    * ``dte`` — calendar days from the "as of" New York date to the expiry date.
    * ``zero_dte`` / ``this_week`` / ``monthly`` — precomputed filter membership, so
      :func:`by_strike` and friends need no clock of their own.
    * ``sign`` — the dealer sign, +1 for calls and −1 for puts.

    ``open_interest``, ``iv``, ``vendor_gamma``, ``volume``, ``bid`` and ``ask`` are float
    columns carrying ``NaN`` where the vendor reported ``None``. That is deliberate: pandas
    has no nullable int that survives a NumPy round trip cleanly, and ``NaN`` keeps the
    "unknown" state distinguishable from a real ``0`` (which stays ``0.0``).

    Args:
        snapshot: The chain to flatten. May be empty.
        now: Override the "as of" instant. Defaults to ``snapshot.captured_at``, which is the
            effective time of the data and is what every level should be computed against.
            Must be tz-aware.

    Returns:
        A ``DataFrame`` with :data:`FRAME_COLUMNS`, one row per contract, in snapshot order.
    """
    as_of = snapshot.captured_at if now is None else now
    ny_date = as_of.astimezone(greeks._NY).date()  # same package; one timezone source

    rows = [
        (
            c.occ_symbol,
            c.root,
            c.expiry,
            str(c.settlement),
            float(c.strike),
            str(c.right),
            1.0 if c.right is Right.CALL else -1.0,
            np.nan if c.open_interest is None else float(c.open_interest),
            np.nan if c.iv is None else float(c.iv),
            np.nan if c.gamma is None else float(c.gamma),
            float(c.multiplier),
            np.nan if c.volume is None else float(c.volume),
            np.nan if c.bid is None else float(c.bid),
            np.nan if c.ask is None else float(c.ask),
        )
        for c in snapshot.contracts
    ]
    df = pd.DataFrame(
        rows,
        columns=[
            "occ_symbol",
            "root",
            "expiry",
            "settlement",
            "strike",
            "right",
            "sign",
            "open_interest",
            "iv",
            "vendor_gamma",
            "multiplier",
            "volume",
            "bid",
            "ask",
        ],
    )

    if df.empty:
        for col in ("t", "dte", "expired", "zero_dte", "this_week", "monthly"):
            df[col] = pd.Series(dtype="float64" if col in ("t",) else "object")
        df["expired"] = df["expired"].astype(bool)
        df["dte"] = df["dte"].astype("int64")
        for col in ("zero_dte", "this_week", "monthly"):
            df[col] = df[col].astype(bool)
        return df[list(FRAME_COLUMNS)]

    expiry = df["expiry"].to_numpy(dtype=object)
    settlement = df["settlement"].to_numpy(dtype=object)
    df["t"] = np.asarray(greeks.time_to_expiry(as_of, expiry, settlement), dtype=float)
    df["expired"] = np.asarray(greeks.is_expired(as_of, expiry, settlement), dtype=bool)

    # One pass over the ~50-120 distinct expiry dates rather than per contract.
    calendar = {
        e: (
            (e - ny_date).days,
            e == ny_date,
            e.isocalendar()[:2] == ny_date.isocalendar()[:2],
            e == _third_friday(e),
        )
        for e in df["expiry"].unique()
    }
    df["dte"] = df["expiry"].map(lambda e: calendar[e][0]).astype("int64")
    df["zero_dte"] = df["expiry"].map(lambda e: calendar[e][1]).astype(bool)
    df["this_week"] = df["expiry"].map(lambda e: calendar[e][2]).astype(bool)
    # The AM-settled series *is* the monthly series; see the module docstring for the live
    # SPX expiry (2027-06-17) that a pure third-Friday rule misses.
    df["monthly"] = (
        df["expiry"].map(lambda e: calendar[e][3]) | (df["settlement"] == str(Settlement.AM))
    ).astype(bool)

    return df[list(FRAME_COLUMNS)]


# --------------------------------------------------------------------------------------
# Filtering
# --------------------------------------------------------------------------------------


def expiry_mask(df: pd.DataFrame, filters: Any = ExpiryFilter.ALL) -> np.ndarray:
    """Boolean mask of the rows an expiry filter admits.

    Args:
        df: A frame from :func:`to_frame`.
        filters: An :class:`ExpiryFilter` (or its string name), or any iterable of
            ``datetime.date`` naming the expiries to keep explicitly. An empty iterable
            selects nothing, which is different from ``ALL``.

    Raises:
        ValueError: on an unrecognized filter name or a non-date in the explicit list.
    """
    n = len(df)
    if isinstance(filters, str):
        try:
            filters = ExpiryFilter(filters)
        except ValueError:
            raise ValueError(
                f"unknown expiry filter {filters!r}; expected one of "
                f"{[f.value for f in ExpiryFilter]} or an iterable of dates"
            ) from None

    if isinstance(filters, ExpiryFilter):
        if n == 0:
            return np.zeros(0, dtype=bool)
        if filters is ExpiryFilter.ALL:
            return np.ones(n, dtype=bool)
        if filters is ExpiryFilter.ZERO_DTE:
            return df["zero_dte"].to_numpy(dtype=bool)
        if filters is ExpiryFilter.EX_ZERO_DTE:
            return ~df["zero_dte"].to_numpy(dtype=bool)
        if filters is ExpiryFilter.THIS_WEEK:
            return df["this_week"].to_numpy(dtype=bool)
        return df["monthly"].to_numpy(dtype=bool)  # MONTHLY_ONLY

    if not isinstance(filters, Iterable):
        # ValueError, not TypeError: a filter arrives from an API query string (T11), so a
        # bad one is user input on the same footing as a misspelled enum name, and callers
        # should need exactly one `except ValueError`.
        raise ValueError(  # noqa: TRY004
            f"expiry filter must be an ExpiryFilter or an iterable of dates: {filters!r}"
        )

    wanted = set()
    for value in filters:
        if isinstance(value, dt.datetime):
            wanted.add(value.date())
        elif isinstance(value, dt.date):
            wanted.add(value)
        else:
            raise ValueError(  # noqa: TRY004 — see above: filters are user input
                f"explicit expiry filter must contain dates, got {value!r}"
            )
    if n == 0:
        return np.zeros(0, dtype=bool)
    return df["expiry"].isin(wanted).to_numpy(dtype=bool)


def _filter_label(filters: Any) -> str:
    if isinstance(filters, ExpiryFilter):
        return filters.value
    if isinstance(filters, str):
        return ExpiryFilter(filters).value
    dates = sorted({v.date() if isinstance(v, dt.datetime) else v for v in filters})
    return "EXPIRIES:" + ",".join(d.isoformat() for d in dates)


# --------------------------------------------------------------------------------------
# Eligibility and per-contract GEX
# --------------------------------------------------------------------------------------


def _effective_iv(df: pd.DataFrame, policy: IvPolicy) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(iv_effective, has_iv, extreme)`` under ``policy``.

    ``extreme`` marks contracts whose *reported* IV is outside the band; under ``CLAMP`` they
    stay in with a winsorized vol, under ``EXCLUDE`` they are dropped, under ``KEEP`` the flag
    is informational only.
    """
    iv = df["iv"].to_numpy(dtype=float)
    has_iv = ~np.isnan(iv)
    if policy.mode is IvPolicyMode.KEEP:
        return iv, has_iv, np.zeros(iv.shape, dtype=bool)
    extreme = has_iv & ((iv < policy.iv_min) | (iv > policy.iv_max))
    if policy.mode is IvPolicyMode.CLAMP:
        return np.clip(iv, policy.iv_min, policy.iv_max), has_iv, extreme
    return iv, has_iv, extreme


def include_mask(
    df: pd.DataFrame,
    *,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    use_vendor_gamma: bool = False,
) -> np.ndarray:
    """Boolean mask of contracts that may contribute to an aggregate.

    A contract is included iff **all** of:

    1. it has not expired as of the snapshot instant (``greeks.is_expired``);
    2. its ``open_interest`` is known — ``None`` is excluded, ``0`` is kept and contributes 0;
    3. it has an implied volatility, and that volatility satisfies ``iv_policy``;
    4. under ``use_vendor_gamma``, the vendor also reported a gamma.

    Rule 3 applies even in vendor-gamma mode, so that the cross-check compares the *same*
    population as the recomputed figure rather than a different one.
    """
    if df.empty:
        return np.zeros(0, dtype=bool)
    _iv_eff, has_iv, extreme = _effective_iv(df, iv_policy)
    ok = (
        ~df["expired"].to_numpy(dtype=bool)
        & ~np.isnan(df["open_interest"].to_numpy(dtype=float))
        & has_iv
    )
    if iv_policy.mode is IvPolicyMode.EXCLUDE:
        ok &= ~extreme
    if use_vendor_gamma:
        ok &= ~np.isnan(df["vendor_gamma"].to_numpy(dtype=float))
    return ok


def _notional(df: pd.DataFrame, spot: float, *, signed: bool) -> np.ndarray:
    """``sign · OI · multiplier · spot² · 0.01`` — everything but gamma. NaN OI → 0."""
    oi = np.nan_to_num(df["open_interest"].to_numpy(dtype=float), nan=0.0)
    sign = df["sign"].to_numpy(dtype=float) if signed else 1.0
    return sign * oi * df["multiplier"].to_numpy(dtype=float) * (spot * spot) * PCT_MOVE


def contract_gex(
    df: pd.DataFrame,
    spot: float,
    use_vendor_gamma: bool = False,
    *,
    signed: bool = True,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    r: float | None = None,
    q: float | None = None,
) -> pd.Series:
    """Signed dollar gamma exposure per contract, in dollars per 1 % move of the underlying.

    ``dealer_sign · gamma · open_interest · multiplier · spot² · 0.01`` (PLAN.md §3 item 1).
    The dealer sign — **+1 calls, −1 puts** — is applied here and **only** here.

    Excluded contracts (see :func:`include_mask`) return exactly ``0.0``, so the series sums
    correctly without any further masking; call :func:`include_mask` yourself to tell an
    excluded contract from one that genuinely has no gamma or no open interest.

    Args:
        df: Frame from :func:`to_frame`.
        spot: Underlying level to price at. Normally ``snapshot.spot``; the gamma profile
            passes hypothetical levels instead.
        use_vendor_gamma: Use ``OptionContract.gamma`` as reported instead of recomputing.
            A cross-check only (PLAN.md §2) — vendor gamma cannot be repriced at a
            hypothetical spot, is rounded to four decimals, and is often ``0.0`` on wings.
        signed: ``False`` returns ``|gex|`` per contract — the "absolute" variant of
            PLAN.md §3 item 1. See :func:`abs_gex`.
        iv_policy: Implied-vol admission policy; see the module docstring.
        r: Risk-free rate; ``None`` → config ``RISK_FREE_RATE``.
        q: Dividend yield; ``None`` → config ``DIVIDEND_YIELD``.

    Returns:
        ``pd.Series`` named ``"gex"``, float, aligned to ``df.index``.
    """
    if df.empty:
        return pd.Series(np.zeros(0, dtype=float), index=df.index, name="gex")

    ok = include_mask(df, iv_policy=iv_policy, use_vendor_gamma=use_vendor_gamma)
    if use_vendor_gamma:
        g = np.nan_to_num(df["vendor_gamma"].to_numpy(dtype=float), nan=0.0)
    else:
        iv_eff, _has_iv, _extreme = _effective_iv(df, iv_policy)
        # A placeholder vol on ineligible rows keeps NaN out of the arithmetic entirely;
        # those rows are zeroed by `ok` regardless of what value goes in.
        iv_eff = np.where(np.isnan(iv_eff), 0.2, iv_eff)
        g = np.asarray(
            greeks.gamma(
                spot,
                df["strike"].to_numpy(dtype=float),
                df["t"].to_numpy(dtype=float),
                iv_eff,
                r=r,
                q=q,
            ),
            dtype=float,
        )
        g = np.nan_to_num(g, nan=0.0, posinf=0.0, neginf=0.0)

    out = np.where(ok, g * _notional(df, spot, signed=signed), 0.0)
    if not signed:
        out = np.abs(out)
    return pd.Series(out, index=df.index, name="gex")


def abs_gex(
    df: pd.DataFrame,
    spot: float,
    use_vendor_gamma: bool = False,
    *,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    r: float | None = None,
    q: float | None = None,
) -> pd.Series:
    """Unsigned dollar gamma per contract — the "absolute" variant of PLAN.md §3 item 1.

    Identical to :func:`contract_gex` with ``signed=False``. Summed, it measures how much
    gamma is concentrated somewhere regardless of which side of the book it sits on, which is
    what "max absolute gamma strike" (PLAN.md §3 item 3) ranks on.
    """
    return contract_gex(
        df, spot, use_vendor_gamma, signed=False, iv_policy=iv_policy, r=r, q=q
    ).rename("abs_gex")


# --------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------


def _prepare(
    df: pd.DataFrame,
    filters: Any,
    spot: float | None,
    *,
    iv_policy: IvPolicy,
    use_vendor_gamma: bool,
    r: float | None,
    q: float | None,
) -> pd.DataFrame:
    """Apply the expiry filter and the eligibility mask, attaching ``gex``/``abs_gex``."""
    selected = df.loc[expiry_mask(df, filters)]
    if "gex" in selected.columns:
        working = selected.copy()
        if "abs_gex" not in working.columns:
            working["abs_gex"] = working["gex"].abs()
        return working
    if spot is None:
        raise ValueError("spot is required when the frame does not already carry a 'gex' column")
    working = selected.copy()
    working["gex"] = contract_gex(
        selected, spot, use_vendor_gamma, iv_policy=iv_policy, r=r, q=q
    )
    working["abs_gex"] = working["gex"].abs()
    included = include_mask(selected, iv_policy=iv_policy, use_vendor_gamma=use_vendor_gamma)
    return working.loc[included]


def _bucket(group: pd.DataFrame) -> tuple[float, float, float, float, int, int]:
    gex = group["gex"].to_numpy(dtype=float)
    calls = float(gex[gex > 0].sum())
    puts = float(gex[gex < 0].sum())
    return (
        calls,
        puts,
        float(gex.sum()),
        float(group["abs_gex"].to_numpy(dtype=float).sum()),
        len(group),
        int(np.nan_to_num(group["open_interest"].to_numpy(dtype=float), nan=0.0).sum()),
    )


def by_strike(
    df: pd.DataFrame,
    filters: Any = ExpiryFilter.ALL,
    *,
    spot: float | None = None,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    use_vendor_gamma: bool = False,
    r: float | None = None,
    q: float | None = None,
) -> pd.DataFrame:
    """Per-strike dollar GEX, summed over every admitted expiry and **both SPX roots**.

    SPX and SPXW at the same strike land in the same bucket by design (PLAN.md §3 item 2):
    dealers are short gamma against both series, and the strike is where the hedging pressure
    lands regardless of which root carries it.

    ``call_gex`` splits out the positive (call) contributions and ``put_gex`` the negative
    (put) ones, so a chart can draw them as opposing bars; ``net_gex`` is their sum.

    Args:
        df: Frame from :func:`to_frame`. If it already carries a ``gex`` column (because the
            caller ran :func:`contract_gex` themselves) that column is used as-is and ``spot``
            is not needed.
        filters: Expiry filter — see :func:`expiry_mask`.
        spot: Underlying level, required unless ``df`` already has ``gex``.
        iv_policy, use_vendor_gamma, r, q: Passed through to :func:`contract_gex`.

    Returns:
        ``DataFrame`` indexed 0..n with columns ``strike, call_gex, put_gex, net_gex,
        abs_gex, contracts, open_interest``, ascending by strike. Empty (with those columns)
        when nothing was admitted.
    """
    working = _prepare(
        df, filters, spot, iv_policy=iv_policy, use_vendor_gamma=use_vendor_gamma, r=r, q=q
    )
    columns = ["strike", "call_gex", "put_gex", "net_gex", "abs_gex", "contracts", "open_interest"]
    if working.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in columns})

    records = [
        (float(strike), *_bucket(group)) for strike, group in working.groupby("strike", sort=True)
    ]
    return pd.DataFrame(records, columns=columns)


def by_expiry(
    df: pd.DataFrame,
    filters: Any = ExpiryFilter.ALL,
    *,
    spot: float | None = None,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    use_vendor_gamma: bool = False,
    r: float | None = None,
    q: float | None = None,
) -> pd.DataFrame:
    """Per-expiry dollar GEX, summed over strikes and both roots.

    Same conventions and arguments as :func:`by_strike`. ``dte`` is calendar days from the
    snapshot's New York date; rows are ascending by expiry.

    Note that one calendar date can carry both an AM-settled SPX series and a PM-settled SPXW
    series with materially different times to expiry. They share a row here, because the row
    is keyed on the *date* a consumer would name; the underlying gamma was computed against
    each contract's own settlement instant before the sum.
    """
    working = _prepare(
        df, filters, spot, iv_policy=iv_policy, use_vendor_gamma=use_vendor_gamma, r=r, q=q
    )
    columns = [
        "expiry",
        "dte",
        "call_gex",
        "put_gex",
        "net_gex",
        "abs_gex",
        "contracts",
        "open_interest",
    ]
    if working.empty:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in columns})

    records = [
        (expiry, int(group["dte"].iloc[0]), *_bucket(group))
        for expiry, group in working.groupby("expiry", sort=True)
    ]
    return pd.DataFrame(records, columns=columns)


# --------------------------------------------------------------------------------------
# Gamma profile and flip point
# --------------------------------------------------------------------------------------


def gamma_profile(
    df: pd.DataFrame,
    spot: float,
    *,
    filters: Any = ExpiryFilter.ALL,
    grid: np.ndarray | Sequence[float] | None = None,
    width: float = DEFAULT_PROFILE_WIDTH,
    step: float = DEFAULT_PROFILE_STEP,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    r: float | None = None,
    q: float | None = None,
) -> GammaProfile:
    """Total dealer GEX as a function of a hypothetical spot (PLAN.md §3 item 4).

    For every level ``S`` on the grid, **every contract's gamma is recomputed at ``S`` with
    its own implied volatility and its own time to expiry**, and the signed dollar gammas are
    summed. The ``S²`` scaling uses the *hypothetical* level too, since the question the curve
    answers is "what would dealer gamma be if the index were here", not "what is today's
    gamma re-weighted". Time is held fixed at the snapshot instant: this is a spot slice, not
    a forward simulation.

    Vendor gamma is deliberately not an option here — a vendor's gamma is a number at *its*
    spot and cannot be repriced. This is one of the two reasons PLAN.md §2 recomputes Greeks.

    Args:
        df: Frame from :func:`to_frame`.
        spot: The snapshot's actual spot; anchors the grid and is carried on the result.
        filters: Expiry filter applied before summing.
        grid: Explicit hypothetical spot levels. Overrides ``width``/``step``.
        width: Half-width of the default grid as a fraction of spot (0.10 → ±10 %).
        step: Grid spacing as a fraction of spot (0.001 → 0.1 %). Default grid: 201 points.
        iv_policy, r, q: As in :func:`contract_gex`.

    Returns:
        :class:`GammaProfile`, which unpacks as ``(spot_grid, total_gex)``.
    """
    if grid is None:
        if step <= 0 or width <= 0:
            raise ValueError(f"width and step must be positive, got {width} / {step}")
        half = round(width / step)
        grid_arr = spot * (1.0 + np.arange(-half, half + 1, dtype=float) * step)
    else:
        grid_arr = np.asarray(grid, dtype=float)
        if grid_arr.ndim != 1:
            raise ValueError("grid must be one-dimensional")

    selected = df.loc[expiry_mask(df, filters)]
    ok = include_mask(selected, iv_policy=iv_policy)
    eligible = selected.loc[ok]
    if eligible.empty or grid_arr.size == 0:
        return GammaProfile(
            spot=float(spot),
            spot_grid=grid_arr,
            total_gex=np.zeros(grid_arr.shape, dtype=float),
        )

    iv_eff, _has_iv, _extreme = _effective_iv(eligible, iv_policy)
    # (M, 1) grid against (1, n) contracts: greeks.gamma then takes M + n logarithms instead
    # of M*n, and sigma*sqrt(t) is computed once. Pre-broadcasting measures ~2x slower.
    spots = grid_arr[:, None]
    g = np.asarray(
        greeks.gamma(
            spots,
            eligible["strike"].to_numpy(dtype=float)[None, :],
            eligible["t"].to_numpy(dtype=float)[None, :],
            iv_eff[None, :],
            r=r,
            q=q,
        ),
        dtype=float,
    )
    np.nan_to_num(g, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

    weights = (
        eligible["sign"].to_numpy(dtype=float)
        * np.nan_to_num(eligible["open_interest"].to_numpy(dtype=float), nan=0.0)
        * eligible["multiplier"].to_numpy(dtype=float)
    )
    total = (g @ weights) * grid_arr * grid_arr * PCT_MOVE
    return GammaProfile(spot=float(spot), spot_grid=grid_arr, total_gex=total)


def flip_point(
    profile: GammaProfile | tuple[Any, Any], spot: float | None = None
) -> float | None:
    """The gamma flip (zero-gamma level): where total dealer GEX changes sign.

    Crossings are located by **linear interpolation** between adjacent grid points, and the
    one nearest the current spot is returned — the market crosses the closest level first, and
    a deep-wing crossing is not the level anyone trades against.

    A crossing means the profile actually **changes sign**, which is not the same as touching
    zero. Exact zeros are common and mostly meaningless: an empty selection gives an all-zero
    profile, and a 0DTE-only profile underflows to exactly 0.0 in both ±10 % wings because
    every one-day gamma has died by then. Treating those as flips reported a level in the wing
    (or the spot itself) where there is none, so a zero counts only when the nearest non-zero
    values on either side of it have opposite signs; a zero run that qualifies contributes its
    own grid points, since each of them genuinely is a zero-gamma level.

    Returns:
        The interpolated index level, or ``None`` when the profile never changes sign inside
        the grid. **That is a real outcome, not a failure**: an SPX chain that is positive-
        gamma across the whole ±10 % range genuinely has no flip in range, and the honest
        answer is "none here" rather than a clamped grid edge that would look like a level.
        ``None`` also comes back for a degenerate profile (fewer than two points, all NaN, or
        identically zero).

    Args:
        profile: A :class:`GammaProfile`, or a bare ``(spot_grid, total_gex)`` pair.
        spot: Reference level for "nearest". Defaults to ``profile.spot``; required if a bare
            pair is passed, otherwise the midpoint of the grid is used.
    """
    if isinstance(profile, GammaProfile):
        grid, total = profile.spot_grid, profile.total_gex
        reference = profile.spot if spot is None else spot
    else:
        grid_raw, total_raw = profile
        grid, total = np.asarray(grid_raw, dtype=float), np.asarray(total_raw, dtype=float)
        reference = float(np.median(grid)) if spot is None and grid.size else spot

    grid = np.asarray(grid, dtype=float)
    total = np.asarray(total, dtype=float)
    if grid.size < 2 or grid.size != total.size or not np.isfinite(total).any():
        return None

    # Walk the non-zero, finite samples only. Zeros are skipped here and reinstated below just
    # where a sign change brackets them, so an all-zero profile and a wing that merely
    # underflows to zero both yield no crossing at all.
    live = np.flatnonzero(np.isfinite(total) & (total != 0.0))
    if live.size < 2:
        return None

    values = total[live]
    changes = np.flatnonzero(values[:-1] * values[1:] < 0.0)
    if changes.size == 0:
        return None

    crossings: list[float] = []
    for c in changes:
        lo, hi = int(live[c]), int(live[c + 1])
        # A run of exact zeros between the two signs: every one of them is a genuine
        # zero-gamma level, so offer them all and let "nearest to spot" choose. A gap that
        # holds only NaN offers nothing, and the two live samples are interpolated across it.
        gap = grid[lo + 1 : hi][total[lo + 1 : hi] == 0.0]
        if gap.size:
            crossings.extend(float(x) for x in gap)
        else:
            y0, y1 = total[lo], total[hi]
            crossings.append(float(grid[lo] + (grid[hi] - grid[lo]) * (-y0) / (y1 - y0)))

    if reference is None:
        reference = float(np.median(grid))
    return float(min(crossings, key=lambda x: abs(x - reference)))


# --------------------------------------------------------------------------------------
# Key levels
# --------------------------------------------------------------------------------------


def _row(record: pd.Series) -> StrikeGex:
    return StrikeGex(
        strike=float(record["strike"]),
        call_gex=float(record["call_gex"]),
        put_gex=float(record["put_gex"]),
        net_gex=float(record["net_gex"]),
        abs_gex=float(record["abs_gex"]),
        contracts=int(record.get("contracts", 0) or 0),
        open_interest=int(record.get("open_interest", 0) or 0),
    )


def key_levels(
    strike_gex: pd.DataFrame,
    *,
    spot: float | None = None,
    flip: float | None = None,
    computed_at: dt.datetime | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> KeyLevels:
    """Call wall, put wall, max-absolute strike and the top-N strikes on each side.

    Definitions (PLAN.md §3 item 3, whose parenthetical reads "call wall (max positive strike
    GEX), put wall (max negative)"), stated exactly because they are the numbers T10 compares
    against vendors:

    * **call wall** — the strike with the largest positive **net** GEX. Ties break to the
      lower strike. Identical to ``max_net_strike``.
    * **put wall** — the strike with the most negative **net** GEX. Identical to
      ``min_net_strike``.
    * **max absolute strike** — the strike with the largest Σ|contract gex|, regardless of
      side; the single strike with the most gamma pinned to it.
    * **top positive / negative** — the ``top_n`` strikes ranked by *net* GEX, descending and
      ascending respectively. Entries with the wrong sign are not padded in: if only three
      strikes have positive net GEX, ``top_positive`` has three entries.

    The walls are net-based on purpose, and this is the one definition in the module that was
    got wrong once and is therefore spelled out. Defining them per side — ``argmax(call_gex)``
    and ``argmin(put_gex)`` — collapses both walls onto a single strike whenever one strike
    carries the most gamma on *both* sides, which large round-number strikes routinely do: the
    live SPX chain puts both at 8000 with spot at 7719, bracketing nothing. Net GEX cannot
    have that failure, because a strike cannot simultaneously be the most positive and the
    most negative unless it is the only strike. The per-side extrema are still reported, as
    ``max_call_gex_strike`` / ``max_put_gex_strike``, under names that do not claim to be a
    tradeable level.

    Args:
        strike_gex: Output of :func:`by_strike`.
        spot: Snapshot spot, echoed onto the result.
        flip: Flip point from :func:`flip_point`, echoed onto the result.
        computed_at: Computation timestamp, echoed onto the result.
        top_n: How many strikes to rank on each side.
    """
    if strike_gex.empty:
        return KeyLevels(
            net_gex=0.0,
            call_gex=0.0,
            put_gex=0.0,
            abs_gex=0.0,
            call_wall=None,
            call_wall_gex=None,
            put_wall=None,
            put_wall_gex=None,
            max_abs_strike=None,
            max_abs_gex=None,
            max_net_strike=None,
            min_net_strike=None,
            flip_point=flip,
            spot=None if spot is None else float(spot),
            computed_at=computed_at,
        )

    ordered = strike_gex.sort_values("strike", kind="stable").reset_index(drop=True)
    call = ordered["call_gex"].to_numpy(dtype=float)
    put = ordered["put_gex"].to_numpy(dtype=float)
    net = ordered["net_gex"].to_numpy(dtype=float)
    absolute = ordered["abs_gex"].to_numpy(dtype=float)
    strikes = ordered["strike"].to_numpy(dtype=float)

    # Walls on the net series; per-side extrema kept separately. See the docstring for why
    # these are not the same thing and why the per-side pair must not be called a wall.
    i_hi, i_lo, i_abs = int(np.argmax(net)), int(np.argmin(net)), int(np.argmax(absolute))
    i_call, i_put = int(np.argmax(call)), int(np.argmin(put))
    ranked = ordered.sort_values("net_gex", ascending=False, kind="stable")
    positive = ranked[ranked["net_gex"] > 0].head(top_n)
    negative = ranked[ranked["net_gex"] < 0].tail(top_n).iloc[::-1]

    return KeyLevels(
        net_gex=float(net.sum()),
        call_gex=float(call.sum()),
        put_gex=float(put.sum()),
        abs_gex=float(absolute.sum()),
        call_wall=float(strikes[i_hi]),
        call_wall_gex=float(net[i_hi]),
        put_wall=float(strikes[i_lo]),
        put_wall_gex=float(net[i_lo]),
        max_abs_strike=float(strikes[i_abs]),
        max_abs_gex=float(absolute[i_abs]),
        max_net_strike=float(strikes[i_hi]),
        min_net_strike=float(strikes[i_lo]),
        max_call_gex_strike=float(strikes[i_call]),
        max_call_gex=float(call[i_call]),
        max_put_gex_strike=float(strikes[i_put]),
        max_put_gex=float(put[i_put]),
        flip_point=flip,
        spot=None if spot is None else float(spot),
        computed_at=computed_at,
        top_positive=tuple(_row(r) for _, r in positive.iterrows()),
        top_negative=tuple(_row(r) for _, r in negative.iterrows()),
    )


# --------------------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------------------


def _diagnostics(
    selected: pd.DataFrame,
    spot: float,
    *,
    iv_policy: IvPolicy,
    use_vendor_gamma: bool,
    net_gex: float,
    r: float | None,
    q: float | None,
    examples: int = 8,
) -> GexDiagnostics:
    """Quantify everything the eligibility rules dropped, in contracts and in dollars."""
    if selected.empty:
        return GexDiagnostics(
            contracts=0,
            included=0,
            expired=0,
            missing_open_interest=0,
            zero_open_interest=0,
            missing_iv=0,
            missing_iv_gex_vendor=0.0,
            extreme_iv=0,
            extreme_iv_open_interest=0,
            extreme_iv_gex_excluded=0.0,
            net_gex_iv_unfiltered=net_gex,
            iv_min_observed=None,
            iv_max_observed=None,
            iv_policy_mode=iv_policy.mode,
            iv_policy_min=iv_policy.iv_min,
            iv_policy_max=iv_policy.iv_max,
            use_vendor_gamma=use_vendor_gamma,
        )

    iv = selected["iv"].to_numpy(dtype=float)
    oi = selected["open_interest"].to_numpy(dtype=float)
    expired = selected["expired"].to_numpy(dtype=bool)
    _iv_eff, has_iv, extreme = _effective_iv(selected, iv_policy)
    alive = ~expired & ~np.isnan(oi)

    # What the policy removed, in dollars: the same population priced with the vendor's IV
    # verbatim, minus what it contributes now. Under KEEP the two are identical and this is 0.
    unfiltered_policy = IvPolicy(mode=IvPolicyMode.KEEP)
    gex_active = contract_gex(
        selected, spot, use_vendor_gamma, iv_policy=iv_policy, r=r, q=q
    ).to_numpy(dtype=float)
    gex_keep = contract_gex(
        selected, spot, use_vendor_gamma, iv_policy=unfiltered_policy, r=r, q=q
    ).to_numpy(dtype=float)
    removed = float((gex_keep - gex_active).sum())

    # The IV-less contracts can never be repriced; the vendor's own gamma is the only handle
    # on how much exposure they represent.
    no_iv = alive & ~has_iv
    vendor_gamma = np.nan_to_num(selected["vendor_gamma"].to_numpy(dtype=float), nan=0.0)
    missing_iv_dollars = float(
        (
            np.where(no_iv, vendor_gamma, 0.0) * _notional(selected, spot, signed=True)
        ).sum()
    )

    extreme_alive = extreme & alive
    symbols = selected.loc[extreme_alive, "occ_symbol"].head(examples).tolist()

    return GexDiagnostics(
        contracts=len(selected),
        included=int(
            include_mask(selected, iv_policy=iv_policy, use_vendor_gamma=use_vendor_gamma).sum()
        ),
        expired=int(expired.sum()),
        missing_open_interest=int(np.isnan(oi).sum()),
        zero_open_interest=int((oi == 0).sum()),
        missing_iv=int((alive & ~has_iv).sum()),
        missing_iv_gex_vendor=missing_iv_dollars,
        extreme_iv=int(extreme_alive.sum()),
        extreme_iv_open_interest=int(np.nan_to_num(oi[extreme_alive], nan=0.0).sum()),
        extreme_iv_gex_excluded=removed,
        net_gex_iv_unfiltered=net_gex + removed,
        iv_min_observed=None if not has_iv.any() else float(np.nanmin(iv)),
        iv_max_observed=None if not has_iv.any() else float(np.nanmax(iv)),
        iv_policy_mode=iv_policy.mode,
        iv_policy_min=iv_policy.iv_min,
        iv_policy_max=iv_policy.iv_max,
        use_vendor_gamma=use_vendor_gamma,
        extreme_iv_examples=tuple(symbols),
    )


# --------------------------------------------------------------------------------------
# Top level
# --------------------------------------------------------------------------------------


def compute_all(
    snapshot: ChainSnapshot,
    filters: Any = ExpiryFilter.ALL,
    *,
    spot: float | None = None,
    frame: pd.DataFrame | None = None,
    iv_policy: IvPolicy = DEFAULT_IV_POLICY,
    use_vendor_gamma: bool = False,
    r: float | None = None,
    q: float | None = None,
    profile_width: float = DEFAULT_PROFILE_WIDTH,
    profile_step: float = DEFAULT_PROFILE_STEP,
    profile_grid: np.ndarray | Sequence[float] | None = None,
    top_n: int = DEFAULT_TOP_N,
    computed_at: dt.datetime | None = None,
    now: dt.datetime | None = None,
) -> GexResult:
    """Compute every GEX figure for one snapshot under one expiry filter.

    This is the module's front door and the input to T09 (persistence) and T11 (the read API).
    It runs the whole pipeline: flatten → filter → per-contract GEX → per-strike and
    per-expiry aggregates → ±10 % gamma profile → flip point → key levels → diagnostics.

    Args:
        snapshot: The chain to analyse.
        filters: One :class:`ExpiryFilter` (or its name), or an explicit iterable of expiry
            dates. Named ``filters`` to match TASKS.md T08; it selects one filter, not
            several. Call once per filter — pass the same ``frame`` each time to skip the
            flattening work.
        spot: Override the underlying level. Defaults to ``snapshot.spot``.
        frame: A frame already built by :func:`to_frame` for this snapshot, reused instead of
            rebuilt. The caller is responsible for it matching the snapshot.
        iv_policy: Implied-vol admission policy; see the module docstring.
        use_vendor_gamma: Cross-check mode — aggregate the vendor's gamma instead of the
            recomputed one. The gamma *profile* always recomputes, since vendor gamma cannot
            be evaluated at a hypothetical spot.
        r: Risk-free rate; ``None`` → config ``RISK_FREE_RATE``.
        q: Dividend yield; ``None`` → config ``DIVIDEND_YIELD``.
        profile_width, profile_step, profile_grid: Gamma-profile grid; see
            :func:`gamma_profile`.
        top_n: Strikes ranked on each side by :func:`key_levels`.
        computed_at: Stamped onto ``levels.computed_at``. Defaults to ``snapshot.captured_at``
            — the *data's* effective time — so a recomputation of an old snapshot is
            reproducible rather than stamped with today's clock.
        now: Override the "as of" instant used for time to expiry. Defaults to
            ``snapshot.captured_at``. Only meaningful when ``frame`` is not supplied.

    Returns:
        A :class:`GexResult`; ``.to_dict()`` is JSON-serializable.
    """
    df = to_frame(snapshot, now=now) if frame is None else frame
    level = float(snapshot.spot if spot is None else spot)
    stamp = snapshot.captured_at if computed_at is None else computed_at

    selected = df.loc[expiry_mask(df, filters)]
    strikes = by_strike(
        selected,
        ExpiryFilter.ALL,
        spot=level,
        iv_policy=iv_policy,
        use_vendor_gamma=use_vendor_gamma,
        r=r,
        q=q,
    )
    expiries = by_expiry(
        selected,
        ExpiryFilter.ALL,
        spot=level,
        iv_policy=iv_policy,
        use_vendor_gamma=use_vendor_gamma,
        r=r,
        q=q,
    )
    profile = gamma_profile(
        selected,
        level,
        width=profile_width,
        step=profile_step,
        grid=profile_grid,
        iv_policy=iv_policy,
        r=r,
        q=q,
    )
    levels = key_levels(
        strikes,
        spot=level,
        flip=flip_point(profile),
        computed_at=stamp,
        top_n=top_n,
    )
    diagnostics = _diagnostics(
        selected,
        level,
        iv_policy=iv_policy,
        use_vendor_gamma=use_vendor_gamma,
        net_gex=levels.net_gex,
        r=r,
        q=q,
    )

    return GexResult(
        underlying=str(snapshot.underlying),
        filter=_filter_label(filters),
        spot=level,
        snapshot=SnapshotMeta(
            underlying=str(snapshot.underlying),
            spot=float(snapshot.spot),
            captured_at=snapshot.captured_at,
            source=snapshot.source,
            delayed_minutes=int(snapshot.delayed_minutes),
            contract_count=len(snapshot.contracts),
        ),
        levels=levels,
        by_strike=tuple(_row(r_) for _, r_ in strikes.iterrows()),
        by_expiry=tuple(
            ExpiryGex(
                expiry=record["expiry"],
                dte=int(record["dte"]),
                call_gex=float(record["call_gex"]),
                put_gex=float(record["put_gex"]),
                net_gex=float(record["net_gex"]),
                abs_gex=float(record["abs_gex"]),
                contracts=int(record["contracts"]),
                open_interest=int(record["open_interest"]),
            )
            for _, record in expiries.iterrows()
        ),
        profile=profile.points(),
        diagnostics=diagnostics,
        expiries=tuple(expiries["expiry"].tolist()),
    )
