"""Option pricing and Greeks — Black-Scholes-Merton / Black-76, vectorized over NumPy arrays.

Every number this application displays traces back to the ``gamma`` computed here, so the
conventions below are stated in full rather than assumed. PLAN.md §2 deliberately recomputes
Greeks locally instead of trusting vendor values, for two reasons: the gamma profile (T08)
needs gamma at *hypothetical* spot prices, which no vendor can supply, and Phase 5 recomputes
from streaming quotes. Vendor gamma is a cross-check only.

Pure functions. No I/O, no state, no logging. The only external read is the default risk-free
rate and dividend yield, resolved from :mod:`app.core.config` at call time when the caller passes
``None``.


The model: one implementation, two ways to describe the same thing
------------------------------------------------------------------

Black-76 on a forward and Black-Scholes-Merton with a continuous dividend yield are the *same
model*, not two models. Substituting ``F = S·exp((r−q)T)`` into Black-76 gives

    call = exp(−rT)·[F·Φ(d1) − K·Φ(d2)] = S·exp(−qT)·Φ(d1) − K·exp(−rT)·Φ(d2)

which is BSM verbatim. So there is exactly one core here, expressed on the forward, and the
SPX-versus-SPY/QQQ distinction reduces to *which inputs you have*:

* **SPY / QQQ** — an ETF paying dividends the option holder does not receive. Pass ``spot``
  and the ETF's continuous dividend yield ``q``. Textbook Black-Scholes-Merton.
* **SPX** — a price index; the option itself pays nothing, and the carry is the index
  dividend yield. Pass ``spot`` and the index yield ``q``; internally this builds
  ``forward = spot·exp((r−q)T)`` and prices Black-76 on it, as PLAN.md §2 requires. Use
  :func:`forward` explicitly if you want to see or override that number.

**Spot versus forward — which function takes which.** This is the single easiest thing to get
wrong, so it is enforced by naming:

* :func:`forward` maps spot → forward. It is the *only* function that takes ``r`` and ``q``
  and returns a price level.
* :func:`d1` and :func:`d2` take the **forward**. They are pure forward-space quantities:
  ``d1 = ln(F/K)/(σ√T) + σ√T/2``. Nothing else enters them. Passing a *spot* where a forward
  is expected shifts ``d1`` down by ``(r−q)T/(σ√T)`` — for SPX at ``r−q = 0.027``, that is
  0.035 on a 7-day 11 %-vol contract (invisible) but 0.35 on a two-year contract (material,
  and it biases gamma toward the wrong strikes).
* :func:`black76_price` takes the **forward**. Provided for the Phase 5 / T21 case where a
  forward is observed from put-call parity rather than assumed from a yield.
* **Every Greek** — :func:`price`, :func:`delta`, :func:`gamma`, :func:`vega`,
  :func:`vanna`, :func:`charm`, :func:`theta` — takes the **spot** and returns a
  **spot-space** derivative (∂/∂S, not ∂/∂F). That is what GEX needs: dollar gamma per 1 %
  move of the *index*, not of the forward. Passing a forward where a spot is expected inflates
  the underlying level by ``exp((r−q)T)`` and applies the carry twice; gamma comes back too
  small by roughly ``exp(−3(r−q)T)`` and every level shifts. There is no runtime check for
  this — the argument name is the contract.

Annualization
-------------

**Time is measured in calendar days / 365, never trading days / 252.** :func:`time_to_expiry`
divides the exact number of *seconds* between ``now`` and the contract's expiry instant by
``365 × 86400``. Weekends and holidays are counted like any other day. This is TASKS.md's
explicit choice and it must match whatever T10 compares against: a 252-day convention would
inflate every ``T`` by 1.448 and every gamma by roughly 1/√1.448 ≈ 0.83 near the money.

Units — every Greek, spelled out
--------------------------------

``S`` is one unit of underlying (one index point for SPX, one dollar for SPY/QQQ); all Greeks
are **per unit of underlying, for one long contract-unit** — i.e. the same basis as the
schema's vendor fields, *not* multiplied by ``multiplier``. T08 applies ``multiplier``.

===========  ==================================================  ==========================
Function     Meaning                                             Unit
===========  ==================================================  ==========================
``price``    theoretical value                                   currency per unit
``delta``    ∂V/∂S                                               per 1.00 move in S
``gamma``    ∂²V/∂S²                                             delta change per 1.00 in S
``vega``     ∂V/∂σ                                               per **1.00** of vol (= 100
                                                                 vol points; divide by 100
                                                                 for the per-point vega most
                                                                 vendors, incl. Cboe, quote)
``vanna``    ∂²V/∂S∂σ = ∂delta/∂σ = ∂vega/∂S                     delta change per 1.00 of vol
``charm``    −∂delta/∂T = delta decay as time passes             per **year** by default;
                                                                 ``per_day=True`` → per
                                                                 calendar day (÷365)
``theta``    −∂V/∂T                                              per **year** by default;
                                                                 ``per_day=True`` → per
                                                                 calendar day (÷365).
                                                                 Vendors quote per day.
===========  ==================================================  ==========================

``gamma``, ``vega`` and ``vanna`` do not take a ``right`` argument, because they do not depend
on it. That is deliberate structural enforcement of the next rule.

Signs
-----

**``gamma`` is returned unsigned** — non-negative for calls and puts alike, matching
``OptionContract.gamma`` in the schema. The dealer-positioning convention (+1 for calls, −1
for puts, PLAN.md §3) belongs to the GEX engine (T08) and must be applied there **exactly
once**. Nothing in this module applies it.

``delta`` carries the long-holder sign: positive for calls, negative for puts. ``charm`` and
``theta`` are signed by the derivative, so a long option normally has ``theta < 0``.

Degenerate and extreme inputs — the documented policy
-----------------------------------------------------

The live SPX chain is not well behaved, so these are policies, not accidents. Let
``v = σ√T``.

* **σ√T > 0 (the normal case).** Ordinary formulas. No masking, no clipping.
* **σ√T == 0** — that is ``σ == 0`` or ``T == 0``, neither of which can reach here in
  practice (the schema requires ``iv > 0``; :func:`time_to_expiry` floors ``T`` at one
  minute), so this is purely defensive. The option is deterministic, so ``price`` returns
  discounted intrinsic and ``delta`` returns the ±{0, 1} step (``exp(−qT)`` scaled). The
  pdf-driven Greeks — ``gamma``, ``vega``, ``vanna``, and the pdf term of ``charm`` and
  ``theta`` — are returned as **0.0**. Their true limit is a Dirac spike at the strike with
  no finite value; returning 0 keeps T08's sums finite instead of poisoning them with ``inf``.
* **σ < 0 or T < 0, or any NaN input.** Returns ``NaN``, deliberately: there is no sensible
  limit and a silent substitution would hide a provider bug. NaN propagates.
* **Spot or strike ≤ 0.** Not supported (the schema forbids both). Results are ``NaN`` or
  ``inf``; no exception is raised, because these functions run over million-element arrays
  where raising helps nobody.
* **Extreme implied volatility.** Vendor IVs on the live SPX chain span 0.054 to 7.97 (up to
  ~800 % annualized); the high values are genuine inversion artifacts on deep-ITM contracts,
  not a units bug. Nothing here caps or rejects them — that policy call is T08's. What this
  module guarantees is that they stay *finite*: at σ = 7.97 the large ``0.5·σ√T`` term pushes
  ``d1`` positive and ``d2`` very negative, ``exp(−d1²/2)`` underflows smoothly toward 0, and
  gamma decays to a tiny positive number. No NaN, no Inf, no overflow. ``d1`` is computed as
  ``ln(F/K)/v + v/2`` rather than ``(ln(F/K) + v²/2)/v`` precisely so that squaring a large
  ``v`` never enters the expression.
* **Deep OTM / ITM.** ``d1`` and ``d2`` run to ±∞ and ``exp(−d1²/2)`` underflows to exactly
  0.0. That is the correct answer and is left alone; underflow is not an error here.
* **0DTE.** A first-class case for SPX, not an edge case. As ``T`` approaches the one-minute
  floor, gamma near the money grows very large (it is genuinely large — a 1-minute ATM option
  really does have that much convexity) and collapses to 0.0 a few points away, smoothly and
  without NaN. T08 decides whether such a spike belongs in a displayed profile.

Vectorization and shapes
------------------------

Every Greek broadcasts under ordinary NumPy rules; there are no Python-level loops over
contracts and no ``scipy.stats`` object calls (``scipy.special.ndtr`` and a direct ``exp`` are
used instead). T08's gamma profile should broadcast rather than loop::

    spot_grid = np.linspace(0.9 * spot, 1.1 * spot, 201)[:, None]   # (201, 1)
    g = gamma(spot_grid, strike[None, :], t[None, :], iv[None, :])  # (201, n_contracts)

Keep the per-contract arrays two-dimensional as ``(1, n)`` rather than pre-broadcasting them
to the full grid: ``σ√T`` and the discount factors then stay ``(1, n)`` and are computed once,
and ``ln(F/K)`` is assembled as ``ln(S) − ln(K) + (r−q)T`` so that only 201 + n logarithms are
taken instead of 201·n. On the full SPX chain (28,650 contracts × a 201-point grid ≈ 5.76 M
evaluations) that is the difference between a fraction of a second and several.
"""

from __future__ import annotations

import datetime as dt
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import ndtr

from app.core.config import settings
from app.modules.gex.models.chain import Settlement

__all__ = [
    "AM_EXPIRY_TIME",
    "DAYS_PER_YEAR",
    "MIN_TIME_TO_EXPIRY",
    "MIN_TIME_TO_EXPIRY_SECONDS",
    "PM_EXPIRY_TIME",
    "SECONDS_PER_YEAR",
    "black76_price",
    "call_put_sign",
    "charm",
    "d1",
    "d2",
    "delta",
    "expiry_datetime",
    "forward",
    "gamma",
    "is_expired",
    "price",
    "theta",
    "time_to_expiry",
    "vanna",
    "vega",
]

#: Calendar-day annualization. See the module docstring: 365, never 252.
DAYS_PER_YEAR = 365.0
SECONDS_PER_YEAR = DAYS_PER_YEAR * 86400.0

#: Floor on time to expiry, in seconds. A 0DTE contract quoted at 15:59:30 must not divide by
#: zero; one minute keeps ``T`` positive while staying far below the resolution at which any
#: displayed level is meaningful.
MIN_TIME_TO_EXPIRY_SECONDS = 60.0
MIN_TIME_TO_EXPIRY = MIN_TIME_TO_EXPIRY_SECONDS / SECONDS_PER_YEAR

#: Expiry wall-clock times in ``America/New_York``. AM-settled contracts (root ``SPX``) settle
#: against the SET opening print; PM-settled contracts (``SPXW``, ``SPY``, ``QQQ``) against the
#: close. See docs/schema.md — the rule is root-derived, never date-derived.
AM_EXPIRY_TIME = dt.time(9, 30)
PM_EXPIRY_TIME = dt.time(16, 0)

_NY = ZoneInfo("America/New_York")

#: 1/sqrt(2*pi). Inlined so the standard normal pdf is one ``exp`` and two multiplies, rather
#: than a ``scipy.stats.norm.pdf`` call that allocates a frozen distribution per invocation.
_INV_SQRT_2PI = 0.3989422804014327


# --------------------------------------------------------------------------------------
# Time to expiry
# --------------------------------------------------------------------------------------


def _as_date(value: Any) -> dt.date:
    """Coerce a date-like value to :class:`datetime.date`, rejecting anything ambiguous."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, np.datetime64):
        return value.astype("datetime64[D]").astype(dt.date)
    raise TypeError(f"expiry must be a date, got {type(value).__name__}: {value!r}")


def expiry_datetime(
    expiry: dt.date, settlement: Settlement | str, *, tz: ZoneInfo = _NY
) -> dt.datetime:
    """Return the exact tz-aware instant at which a contract expires.

    The instant is built as a *wall-clock* time in ``America/New_York`` and then carries
    whatever UTC offset applies on that date. This is what makes the module DST-correct: a
    contract expiring after a DST transition that ``now`` precedes is one hour closer (or
    further) in real time than a naive day-count suggests, and 09:30/16:00 New York are never
    inside a transition gap, so the wall-clock time is always unambiguous.

    Args:
        expiry: The expiry date (``OptionContract.expiry``).
        settlement: ``Settlement.AM`` → 09:30 NY, ``Settlement.PM`` → 16:00 NY. Derived from
            the root by the schema; do not infer it from the date.
        tz: Exchange timezone. Only override in tests.

    Raises:
        ValueError: on a settlement value that is neither AM nor PM.
    """
    style = Settlement(settlement)
    at = AM_EXPIRY_TIME if style is Settlement.AM else PM_EXPIRY_TIME
    return dt.datetime.combine(_as_date(expiry), at, tzinfo=tz)


def _require_aware(now: dt.datetime) -> dt.datetime:
    if not isinstance(now, dt.datetime):
        raise TypeError(f"now must be a datetime, got {type(now).__name__}")
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError(
            "now must be timezone-aware; ChainSnapshot.captured_at already is (UTC). A naive "
            "datetime here would be silently wrong by the New York UTC offset."
        )
    return now.astimezone(dt.UTC)


def _seconds_to_expiry(
    now: dt.datetime, expiry: Any, settlement: Any, tz: ZoneInfo
) -> float | np.ndarray:
    """Signed seconds from ``now`` to expiry; negative when the contract has already expired."""
    now_utc = _require_aware(now)

    # `Settlement` is a StrEnum, so the str check covers both it and a bare "AM"/"PM".
    if isinstance(expiry, dt.date | np.datetime64) and isinstance(settlement, str):
        return (expiry_datetime(expiry, settlement, tz=tz) - now_utc).total_seconds()

    exp_arr = np.asarray(expiry, dtype=object)
    set_arr = np.asarray(settlement, dtype=object)
    exp_b, set_b = np.broadcast_arrays(exp_arr, set_arr)

    # A full SPX chain is ~28.6k contracts but only ~112 distinct (expiry, settlement) pairs,
    # and zoneinfo conversion is Python-level, so the offsets are computed once per pair and
    # the per-contract step is a dict lookup.
    cache: dict[tuple[Any, Any], float] = {}
    out = np.empty(exp_b.shape, dtype=float)
    flat = out.reshape(-1)
    for i, key in enumerate(zip(exp_b.reshape(-1), set_b.reshape(-1), strict=True)):
        secs = cache.get(key)
        if secs is None:
            cache[key] = secs = (expiry_datetime(key[0], key[1], tz=tz) - now_utc).total_seconds()
        flat[i] = secs
    return out


def time_to_expiry(
    now: dt.datetime,
    expiry: Any,
    settlement: Any,
    *,
    floor: float = MIN_TIME_TO_EXPIRY,
    tz: ZoneInfo = _NY,
) -> float | np.ndarray:
    """Time to expiry in **years**, calendar seconds / (365 × 86400).

    Settlement fixes the expiry *time*: AM-settled contracts expire at 09:30 America/New_York
    and PM-settled ones at 16:00, so on the same date an AM contract has exactly 6.5 fewer
    hours of life than a PM contract. Both instants are resolved through ``zoneinfo``, so a
    DST transition between ``now`` and the expiry shifts the result by the real hour rather
    than being lost to a naive day-count.

    Args:
        now: Tz-aware "as of" instant — ``ChainSnapshot.captured_at``, which is the effective
            time of the data, not the wall clock. Naive datetimes are rejected.
        expiry: A ``date``, or an array/sequence of dates. Broadcasts against ``settlement``.
        settlement: A :class:`~app.modules.gex.models.chain.Settlement` (or its ``"AM"``/``"PM"`` string),
            or an array of them.
        floor: Minimum returned value, in years. Defaults to one minute
            (:data:`MIN_TIME_TO_EXPIRY`).
        tz: Exchange timezone. Only override in tests.

    Returns:
        A ``float`` for scalar input, otherwise an ``ndarray`` of the broadcast shape.

    Caution:
        **An already-expired contract comes back at exactly ``floor``, not at zero or a
        negative number.** Clamping keeps the array shape and keeps every downstream formula
        finite, but it means a stale contract looks to :func:`gamma` like a fresh one-minute
        option — which has enormous gamma near the money and would put a spurious spike into
        an aggregate. T08 must filter expired contracts itself; :func:`is_expired` returns the
        mask for exactly that.
    """
    secs = _seconds_to_expiry(now, expiry, settlement, tz)
    if isinstance(secs, float):
        return max(secs / SECONDS_PER_YEAR, floor)
    return np.maximum(secs / SECONDS_PER_YEAR, floor)


def is_expired(
    now: dt.datetime, expiry: Any, settlement: Any, *, tz: ZoneInfo = _NY
) -> bool | np.ndarray:
    """Whether the contract's expiry instant is at or before ``now``.

    The companion to :func:`time_to_expiry`'s floor: use this to drop dead contracts before
    aggregating, rather than trusting a clamped ``T`` to look harmless.
    """
    secs = _seconds_to_expiry(now, expiry, settlement, tz)
    if isinstance(secs, float):
        return secs <= 0.0
    return secs <= 0.0


# --------------------------------------------------------------------------------------
# Input helpers
# --------------------------------------------------------------------------------------


def call_put_sign(right: Any) -> np.ndarray | np.float64:
    """Map an option right to ω: ``+1.0`` for calls, ``−1.0`` for puts.

    Accepts :class:`~app.modules.gex.models.chain.Right` members, ``"C"``/``"P"`` (any case, and any word
    starting with those letters, so ``"CALL"`` and ``"put"`` work), or a numeric ±1 that is
    passed through. Arrays of any of these are accepted and validated elementwise.

    This ω is the *option right*, not the dealer sign. It appears in ``price``, ``delta``,
    ``charm`` and ``theta`` because those genuinely differ between calls and puts. It has
    nothing to do with the +1/−1 dealer-positioning convention, which is T08's and is applied
    once, there.

    Raises:
        ValueError: on any value that is not a recognizable call or put.
    """
    arr = np.asarray(right)
    if arr.dtype.kind in "biuf":
        sign = arr.astype(float)
        if not np.all((sign == 1.0) | (sign == -1.0)):
            raise ValueError("numeric `right` must be exactly +1 (call) or -1 (put)")
        return sign
    # ``astype("U1")`` truncates to the first character, so Right.CALL, "C" and "call" all
    # collapse to "C" without a per-element Python branch.
    first = np.char.upper(arr.astype("U1"))
    is_call = first == "C"
    is_put = first == "P"
    if not np.all(is_call | is_put):
        bad = np.asarray(right)[~(is_call | is_put)]
        raise ValueError(f"`right` must be a call or a put; got {np.unique(bad).tolist()}")
    return np.where(is_call, 1.0, -1.0)


def _rate(r: float | np.ndarray | None) -> float | np.ndarray:
    """Risk-free rate, defaulting to ``RISK_FREE_RATE`` from config. Never fetched anywhere."""
    return settings.RISK_FREE_RATE if r is None else r


def _yield(q: float | np.ndarray | None) -> float | np.ndarray:
    """Continuous dividend yield, defaulting to ``DIVIDEND_YIELD`` from config."""
    return settings.DIVIDEND_YIELD if q is None else q


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    """Standard normal pdf. ``x = ±inf`` underflows to 0.0, which is the correct limit."""
    return _INV_SQRT_2PI * np.exp(-0.5 * x * x)


def _d1_d2_from_log(log_fk: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``d1``/``d2`` from ``ln(F/K)`` and ``v = σ√T``, applying the degenerate-input policy.

    ``d1 = ln(F/K)/v + v/2`` rather than ``(ln(F/K) + v²/2)/v``: algebraically identical, but
    it never squares ``v``, which matters at the 800 % vendor IVs on deep-ITM SPX contracts.
    """
    positive = v > 0  # NaN compares False, so NaN falls through to the careful branch
    if positive.all():
        first = log_fk / v + 0.5 * v
        return first, first - v

    # Cold path. `v == 0` is the deterministic limit (d1 = ±inf, so Φ becomes a step and the
    # pdf becomes 0); `v < 0` or NaN has no limit at all and yields NaN.
    v_safe = np.where(positive, v, 1.0)
    regular = log_fk / v_safe + 0.5 * v_safe
    step = np.where(
        np.isnan(log_fk),
        np.nan,
        np.where(log_fk > 0, np.inf, np.where(log_fk < 0, -np.inf, 0.0)),
    )
    degenerate = np.where(v == 0, step, np.nan)
    first = np.where(positive, regular, degenerate)
    return first, np.where(positive, first - v_safe, first)


def _mask_degenerate(out: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Apply the ``σ√T == 0`` policy to a pdf-driven Greek: force 0.0 there, keep NaN elsewhere.

    Only ``v == 0`` needs rewriting. Where ``v > 0`` the value is already correct, and where
    ``v`` is negative or NaN ``d1`` is already NaN, so it must be left alone rather than
    swept to zero. The ``all()`` short-circuit keeps the normal path allocation-free.
    """
    if np.all(v > 0):
        return out
    return np.where(v == 0, 0.0, out)


# --------------------------------------------------------------------------------------
# Forward-space primitives (Black-76)
# --------------------------------------------------------------------------------------


def forward(
    spot: Any, t: Any, *, r: float | None = None, q: float | None = None
) -> np.ndarray | np.float64:
    """Forward price ``F = S · exp((r − q)·T)``.

    The bridge between the spot the market quotes and the forward Black-76 prices on. For SPX
    this is the whole of PLAN.md §2's "Black-76 on the forward": the index option pays no
    dividends itself, so the index dividend yield enters only through this carry.

    Args:
        spot: Underlying level, > 0.
        t: Time to expiry in years (:func:`time_to_expiry`).
        r: Risk-free rate, continuously compounded, annualized. ``None`` → config
            ``RISK_FREE_RATE``.
        q: Continuous dividend yield, annualized. ``None`` → config ``DIVIDEND_YIELD``.
    """
    rate, div = _rate(r), _yield(q)
    with np.errstate(over="ignore", invalid="ignore"):
        return np.asarray(spot, dtype=float) * np.exp((rate - div) * np.asarray(t, dtype=float))


def d1(fwd: Any, strike: Any, t: Any, sigma: Any) -> np.ndarray | np.float64:
    """``d1 = ln(F/K)/(σ√T) + σ√T/2``, on the **forward** — not the spot.

    Takes ``fwd``, not ``spot``: ``d1`` has no ``r`` or ``q`` in it once the forward is known,
    which is precisely why the forward formulation is the honest one. Use :func:`forward` to
    build the argument. Passing a spot here silently prices a zero-carry world; see the module
    docstring for how large that error gets.

    Degenerate inputs follow the module policy: ``σ√T == 0`` gives ±∞ (or 0 exactly at the
    money), ``σ < 0``/``T < 0``/NaN gives NaN.
    """
    fwd_a = np.asarray(fwd, dtype=float)
    k_a = np.asarray(strike, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        v = np.asarray(sigma, dtype=float) * np.sqrt(np.asarray(t, dtype=float))
        return _d1_d2_from_log(np.log(fwd_a / k_a), v)[0]


def d2(fwd: Any, strike: Any, t: Any, sigma: Any) -> np.ndarray | np.float64:
    """``d2 = d1 − σ√T``, on the **forward**. See :func:`d1`."""
    fwd_a = np.asarray(fwd, dtype=float)
    k_a = np.asarray(strike, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        v = np.asarray(sigma, dtype=float) * np.sqrt(np.asarray(t, dtype=float))
        return _d1_d2_from_log(np.log(fwd_a / k_a), v)[1]


def black76_price(
    fwd: Any, strike: Any, t: Any, sigma: Any, right: Any, *, r: float | None = None
) -> np.ndarray | np.float64:
    """Black-76 option value on an explicit **forward**, discounted at ``r``.

    ``value = exp(−rT) · ω · [F·Φ(ω·d1) − K·Φ(ω·d2)]``, ω = +1 call / −1 put.

    Identical to :func:`price` when ``fwd = forward(spot, t, r=r, q=q)`` — this variant exists
    for the case where the forward is *observed* (from put-call parity on the quoted chain, as
    T21's IV solve will want) rather than implied by a dividend-yield assumption.

    Returns a value per unit of underlying; multiply by ``multiplier`` for the cash price.
    """
    fwd_a = np.asarray(fwd, dtype=float)
    k_a = np.asarray(strike, dtype=float)
    t_a = np.asarray(t, dtype=float)
    omega = call_put_sign(right)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        v = np.asarray(sigma, dtype=float) * np.sqrt(t_a)
        first, second = _d1_d2_from_log(np.log(fwd_a / k_a), v)
        disc = np.exp(-_rate(r) * t_a)
        return disc * omega * (fwd_a * ndtr(omega * first) - k_a * ndtr(omega * second))


# --------------------------------------------------------------------------------------
# Spot-space Greeks (Black-Scholes-Merton == Black-76 on F = S·exp((r−q)T))
# --------------------------------------------------------------------------------------


def _spot_core(
    spot: Any, strike: Any, t: Any, sigma: Any, r: float | None, q: float | None
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Shared setup: ``(S, K, T, v, d1, d2, exp(−qT))`` with the carry already folded in.

    ``ln(F/K)`` is assembled as ``ln(S) − ln(K) + (r−q)T`` instead of ``ln(S·e^{(r−q)T}/K)``.
    Algebraically the same and slightly more accurate, but the point is shape: on a gamma
    profile ``S`` is ``(grid, 1)`` and ``K``/``T`` are ``(1, n)``, so this takes ``grid + n``
    logarithms instead of ``grid × n`` and never materializes the forward at all.
    """
    s_a = np.asarray(spot, dtype=float)
    k_a = np.asarray(strike, dtype=float)
    t_a = np.asarray(t, dtype=float)
    rate, div = _rate(r), _yield(q)
    v = np.asarray(sigma, dtype=float) * np.sqrt(t_a)
    log_fk = np.log(s_a) - np.log(k_a) + (rate - div) * t_a
    first, second = _d1_d2_from_log(log_fk, v)
    return s_a, k_a, t_a, v, first, second, np.exp(-div * t_a)


def price(
    spot: Any,
    strike: Any,
    t: Any,
    sigma: Any,
    right: Any,
    *,
    r: float | None = None,
    q: float | None = None,
) -> np.ndarray | np.float64:
    """Black-Scholes-Merton option value, **per unit of underlying**.

    ``value = ω · [S·e^{−qT}·Φ(ω·d1) − K·e^{−rT}·Φ(ω·d2)]``, ω = +1 call / −1 put.

    Takes **spot**, not forward (see :func:`black76_price` for the forward form). Multiply by
    ``OptionContract.multiplier`` for the cash price of one contract.

    Args:
        spot: Underlying level. Scalar or array; broadcasts against the rest.
        strike: Strike, in the same units as ``spot``.
        t: Time to expiry in years — calendar days / 365, from :func:`time_to_expiry`.
        sigma: Implied volatility as a **decimal fraction** (0.18 = 18 %), annualized, as the
            schema stores it. Never a percent.
        right: Call/put, per :func:`call_put_sign`.
        r: Risk-free rate; ``None`` → config ``RISK_FREE_RATE``.
        q: Dividend yield; ``None`` → config ``DIVIDEND_YIELD``.
    """
    omega = call_put_sign(right)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        s_a, k_a, t_a, _v, first, second, disc_q = _spot_core(spot, strike, t, sigma, r, q)
        disc_r = np.exp(-_rate(r) * t_a)
        return omega * (s_a * disc_q * ndtr(omega * first) - k_a * disc_r * ndtr(omega * second))


def delta(
    spot: Any,
    strike: Any,
    t: Any,
    sigma: Any,
    right: Any,
    *,
    r: float | None = None,
    q: float | None = None,
) -> np.ndarray | np.float64:
    """Spot delta ``∂V/∂S = ω·e^{−qT}·Φ(ω·d1)`` — change in value per **1.00** move in spot.

    Long-holder sign: positive for calls, negative for puts, matching ``OptionContract.delta``.
    Put-call parity holds exactly: ``delta_call − delta_put = e^{−qT}``.
    """
    omega = call_put_sign(right)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        _s, _k, _t, _v, first, _second, disc_q = _spot_core(spot, strike, t, sigma, r, q)
        return omega * disc_q * ndtr(omega * first)


def gamma(
    spot: Any,
    strike: Any,
    t: Any,
    sigma: Any,
    *,
    r: float | None = None,
    q: float | None = None,
) -> np.ndarray | np.float64:
    """Spot gamma ``∂²V/∂S² = e^{−qT}·φ(d1) / (S·σ√T)`` — delta change per **1.00** move in spot.

    **Returned unsigned**: identical and non-negative for a call and a put at the same strike,
    matching ``OptionContract.gamma`` and docs/schema.md. There is no ``right`` argument
    because gamma does not depend on one. The dealer sign (+1 calls, −1 puts, PLAN.md §3) is
    T08's and must be applied there exactly once — if you find yourself wanting a signed gamma
    from this function, the sign is being applied in the wrong place.

    This is the number the whole dashboard rests on. Per PLAN.md §3,
    ``contract_gex = gamma × open_interest × multiplier × spot² × 0.01``.

    Behavior at the limits, restated because T08 depends on it: as ``T`` approaches the
    one-minute floor gamma grows very large near the money and decays to exactly 0.0 away from
    it — never NaN. At the 800 % vendor IVs on the live SPX chain it decays smoothly to a tiny
    positive number. When ``σ√T == 0`` it returns 0.0 (the true limit is an unrepresentable
    Dirac spike); when ``σ`` or ``T`` is negative or NaN it returns NaN.

    Args:
        spot: Underlying level, or a whole grid of hypothetical levels for the gamma profile.
        strike: Strike, same units as ``spot``.
        t: Time to expiry in years (calendar / 365).
        sigma: Implied volatility as a decimal fraction.
        r: Risk-free rate; ``None`` → config ``RISK_FREE_RATE``.
        q: Dividend yield; ``None`` → config ``DIVIDEND_YIELD``.
    """
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        s_a, _k, _t, v, first, _second, disc_q = _spot_core(spot, strike, t, sigma, r, q)
        return _mask_degenerate(_norm_pdf(first) * disc_q / (s_a * v), v)


def vega(
    spot: Any,
    strike: Any,
    t: Any,
    sigma: Any,
    *,
    r: float | None = None,
    q: float | None = None,
) -> np.ndarray | np.float64:
    """Vega ``∂V/∂σ = S·e^{−qT}·φ(d1)·√T``, **per 1.00 of volatility** (= 100 vol points).

    Divide by 100 to compare against the per-vol-point vega vendors quote (Cboe included);
    ``OptionContract.vega`` is the vendor's number and is not directly comparable to this one.
    Identical for calls and puts, hence no ``right`` argument.
    """
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        s_a, _k, t_a, v, first, _second, disc_q = _spot_core(spot, strike, t, sigma, r, q)
        return _mask_degenerate(s_a * disc_q * _norm_pdf(first) * np.sqrt(t_a), v)


def vanna(
    spot: Any,
    strike: Any,
    t: Any,
    sigma: Any,
    *,
    r: float | None = None,
    q: float | None = None,
) -> np.ndarray | np.float64:
    """Vanna ``∂²V/∂S∂σ = −e^{−qT}·φ(d1)·d2/σ`` — delta change per **1.00** of volatility.

    Equivalently ``∂vega/∂S``. Identical for calls and puts (no ``right`` argument), and
    signed: negative above the forward, positive below. PLAN.md §3 item 5 lists vanna exposure
    as a phase-4 stretch; this is the per-unit building block for it.
    """
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        _s, _k, _t, v, first, second, disc_q = _spot_core(spot, strike, t, sigma, r, q)
        sigma_a = np.asarray(sigma, dtype=float)
        return _mask_degenerate(-disc_q * _norm_pdf(first) * second / sigma_a, v)


def charm(
    spot: Any,
    strike: Any,
    t: Any,
    sigma: Any,
    right: Any,
    *,
    r: float | None = None,
    q: float | None = None,
    per_day: bool = False,
) -> np.ndarray | np.float64:
    """Charm ``−∂delta/∂T`` — how a contract's delta decays as time passes.

    ``charm = ω·q·e^{−qT}·Φ(ω·d1) − e^{−qT}·φ(d1)·[(r−q)/(σ√T) − d2/(2T)]``, ω = +1 / −1.

    **Per year by default.** Pass ``per_day=True`` for the per-calendar-day figure (÷365),
    which is how charm is usually displayed. The sign convention here is "delta decay as the
    clock runs forward", i.e. ``−∂delta/∂T``: some vendors publish ``+∂delta/∂T`` instead, so
    a sign disagreement with an external source is a convention difference, not a bug.

    Like :func:`vanna`, this is the per-unit building block for PLAN.md §3 item 5.
    """
    omega = call_put_sign(right)
    rate, div = _rate(r), _yield(q)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        _s, _k, t_a, v, first, second, disc_q = _spot_core(spot, strike, t, sigma, r, q)
        drift = _mask_degenerate(
            disc_q * _norm_pdf(first) * ((rate - div) / v - second / (2.0 * t_a)), v
        )
        out = omega * div * disc_q * ndtr(omega * first) - drift
    return out / DAYS_PER_YEAR if per_day else out


def theta(
    spot: Any,
    strike: Any,
    t: Any,
    sigma: Any,
    right: Any,
    *,
    r: float | None = None,
    q: float | None = None,
    per_day: bool = False,
) -> np.ndarray | np.float64:
    """Theta ``−∂V/∂T`` — value decay as time passes. Normally negative for a long option.

    ``theta = −S·e^{−qT}·φ(d1)·σ/(2√T) − ω·r·K·e^{−rT}·Φ(ω·d2) + ω·q·S·e^{−qT}·Φ(ω·d1)``.

    **Per year by default.** Pass ``per_day=True`` for the per-calendar-day figure (÷365),
    which is the convention ``OptionContract.theta`` carries from the vendor — comparing the
    two without converting is a factor-of-365 error.
    """
    omega = call_put_sign(right)
    rate, div = _rate(r), _yield(q)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        s_a, k_a, t_a, v, first, second, disc_q = _spot_core(spot, strike, t, sigma, r, q)
        sigma_a = np.asarray(sigma, dtype=float)
        decay = _mask_degenerate(
            -s_a * disc_q * _norm_pdf(first) * sigma_a / (2.0 * np.sqrt(t_a)), v
        )
        out = (
            decay
            - omega * rate * k_a * np.exp(-rate * t_a) * ndtr(omega * second)
            + omega * div * s_a * disc_q * ndtr(omega * first)
        )
    return out / DAYS_PER_YEAR if per_day else out
