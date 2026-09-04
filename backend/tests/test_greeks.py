"""Tests for app.gex.greeks.

Every number the dashboard shows traces back to ``gamma``, so this suite is written to be
readable *without trusting the implementation*: the reference values below are computed from
the textbook formulas spelled out in :func:`bs_reference` (scalar Python, ``math.erf`` for the
normal CDF — no shared code with the module under test), and the headline case is additionally
pinned to literals with the arithmetic worked out in the comment above it.

The suite covers the acceptance bar from TASKS.md T07 — reference values, put-call parity,
gamma symmetry between call and put, vectorized-vs-scalar equality, gamma → 0 as T grows — plus
the numerical hazards that are real on the live SPX chain: vendor IVs up to 7.97, 0DTE at the
one-minute floor, deep-OTM underflow, and the AM/PM settlement split across a DST boundary.
"""

from __future__ import annotations

import datetime as dt
import math
import time
import warnings
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.config import settings
from app.gex import greeks as g
from app.models.chain import Right, Settlement

NY = ZoneInfo("America/New_York")

# --------------------------------------------------------------------------------------
# Independent scalar reference implementation
# --------------------------------------------------------------------------------------


def norm_cdf(x: float) -> float:
    """Standard normal CDF: N(x) = 0.5 * (1 + erf(x / sqrt(2)))."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """Standard normal pdf: n(x) = exp(-x^2 / 2) / sqrt(2*pi)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_reference(s: float, k: float, t: float, r: float, q: float, sigma: float) -> dict:
    """Textbook Black-Scholes-Merton with a continuous dividend yield, written out in full.

        d1    = (ln(S/K) + (r - q + sigma^2 / 2) * T) / (sigma * sqrt(T))
        d2    = d1 - sigma * sqrt(T)

        call  = S * e^(-qT) * N(d1)  -  K * e^(-rT) * N(d2)
        put   = K * e^(-rT) * N(-d2) -  S * e^(-qT) * N(-d1)

        delta_call =  e^(-qT) * N(d1)
        delta_put  = -e^(-qT) * N(-d1)

        gamma = e^(-qT) * n(d1) / (S * sigma * sqrt(T))        [same for call and put]
        vega  = S * e^(-qT) * n(d1) * sqrt(T)                  [per 1.00 of vol]
        vanna = -e^(-qT) * n(d1) * d2 / sigma                  [same for call and put]

        theta_call = -S*e^(-qT)*n(d1)*sigma/(2*sqrt(T)) - r*K*e^(-rT)*N(d2)  + q*S*e^(-qT)*N(d1)
        theta_put  = -S*e^(-qT)*n(d1)*sigma/(2*sqrt(T)) + r*K*e^(-rT)*N(-d2) - q*S*e^(-qT)*N(-d1)

        charm_call =  q*e^(-qT)*N(d1)  - e^(-qT)*n(d1)*[ (r-q)/(sigma*sqrt(T)) - d2/(2T) ]
        charm_put  = -q*e^(-qT)*N(-d1) - e^(-qT)*n(d1)*[ (r-q)/(sigma*sqrt(T)) - d2/(2T) ]

    Time is in years measured as calendar days / 365 (never trading days / 252). Theta and
    charm here are ``-d/dT``, per year. Sigma is a decimal fraction, so vega is per 1.00 of
    volatility (100 vol points), not per point.
    """
    v = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / v
    d2 = d1 - v
    dq = math.exp(-q * t)
    dr = math.exp(-r * t)
    n_d1 = norm_pdf(d1)
    drift = (r - q) / v - d2 / (2.0 * t)
    return {
        "d1": d1,
        "d2": d2,
        "call": s * dq * norm_cdf(d1) - k * dr * norm_cdf(d2),
        "put": k * dr * norm_cdf(-d2) - s * dq * norm_cdf(-d1),
        "delta_call": dq * norm_cdf(d1),
        "delta_put": -dq * norm_cdf(-d1),
        "gamma": dq * n_d1 / (s * sigma * math.sqrt(t)),
        "vega": s * dq * n_d1 * math.sqrt(t),
        "vanna": -dq * n_d1 * d2 / sigma,
        "theta_call": (
            -s * dq * n_d1 * sigma / (2.0 * math.sqrt(t))
            - r * k * dr * norm_cdf(d2)
            + q * s * dq * norm_cdf(d1)
        ),
        "theta_put": (
            -s * dq * n_d1 * sigma / (2.0 * math.sqrt(t))
            + r * k * dr * norm_cdf(-d2)
            - q * s * dq * norm_cdf(-d1)
        ),
        "charm_call": q * dq * norm_cdf(d1) - dq * n_d1 * drift,
        "charm_put": -q * dq * norm_cdf(-d1) - dq * n_d1 * drift,
    }


# (spot, strike, T, r, q, sigma) — an ATM one-year case, an SPX-like short-dated case with a
# dividend yield, a deep-ITM low-vol case, and a deep-OTM high-vol case.
CASES = [
    (100.0, 100.0, 1.0, 0.05, 0.0, 0.20),
    (6800.0, 6850.0, 14.0 / 365.0, 0.04, 0.013, 0.1064),
    (6800.0, 5000.0, 0.75, 0.04, 0.013, 0.35),
    (450.0, 600.0, 30.0 / 365.0, 0.04, 0.012, 0.90),
]


# --------------------------------------------------------------------------------------
# Hand-computed reference values
# --------------------------------------------------------------------------------------


def test_headline_case_matches_hand_computed_literals():
    """S=100, K=100, T=1, r=0.05, q=0, sigma=0.20 — worked out by hand.

    d1     = (ln(1) + (0.05 - 0 + 0.5*0.04) * 1) / (0.20 * 1) = 0.07 / 0.20 = 0.35
    d2     = 0.35 - 0.20                                                    = 0.15
    N(0.35)                                                     = 0.63683065117562
    N(0.15)                                                     = 0.55961769237024
    e^(-0.05)                                                   = 0.95122942450071
    n(0.35) = e^(-0.06125) / sqrt(2*pi)                         = 0.37524034691694

    call   = 100 * 0.63683065117562 - 100 * 0.95122942450071 * 0.55961769237024
           = 63.68306511756  - 53.23248154538  = 10.45058357218
    put    = call - S + K*e^(-rT) = 10.45058357218 - 100 + 95.12294245007
           =  5.57352602226
    gamma  = 0.37524034691694 / (100 * 0.20 * 1)                = 0.01876201734585
    vega   = 100 * 0.37524034691694 * 1                         = 37.52403469169
    """
    s, k, t, r, q, sigma = 100.0, 100.0, 1.0, 0.05, 0.0, 0.20
    fwd = g.forward(s, t, r=r, q=q)

    assert float(g.d1(fwd, k, t, sigma)) == pytest.approx(0.35, abs=1e-12)
    assert float(g.d2(fwd, k, t, sigma)) == pytest.approx(0.15, abs=1e-12)

    assert float(g.price(s, k, t, sigma, "C", r=r, q=q)) == pytest.approx(10.45058357218, abs=1e-9)
    assert float(g.price(s, k, t, sigma, "P", r=r, q=q)) == pytest.approx(5.57352602226, abs=1e-9)
    assert float(g.gamma(s, k, t, sigma, r=r, q=q)) == pytest.approx(0.01876201734585, abs=1e-13)
    assert float(g.vega(s, k, t, sigma, r=r, q=q)) == pytest.approx(37.52403469169, abs=1e-9)


@pytest.mark.parametrize("case", CASES)
def test_all_greeks_match_the_scalar_reference(case):
    s, k, t, r, q, sigma = case
    ref = bs_reference(s, k, t, r, q, sigma)
    kw = {"r": r, "q": q}
    fwd = g.forward(s, t, **kw)

    assert float(g.d1(fwd, k, t, sigma)) == pytest.approx(ref["d1"], rel=1e-12)
    assert float(g.d2(fwd, k, t, sigma)) == pytest.approx(ref["d2"], rel=1e-12)
    assert float(g.price(s, k, t, sigma, "C", **kw)) == pytest.approx(ref["call"], rel=1e-11)
    assert float(g.price(s, k, t, sigma, "P", **kw)) == pytest.approx(ref["put"], rel=1e-11)
    assert float(g.delta(s, k, t, sigma, "C", **kw)) == pytest.approx(ref["delta_call"], rel=1e-11)
    assert float(g.delta(s, k, t, sigma, "P", **kw)) == pytest.approx(ref["delta_put"], rel=1e-11)
    assert float(g.gamma(s, k, t, sigma, **kw)) == pytest.approx(ref["gamma"], rel=1e-11)
    assert float(g.vega(s, k, t, sigma, **kw)) == pytest.approx(ref["vega"], rel=1e-11)
    assert float(g.vanna(s, k, t, sigma, **kw)) == pytest.approx(ref["vanna"], rel=1e-11)
    assert float(g.charm(s, k, t, sigma, "C", **kw)) == pytest.approx(ref["charm_call"], rel=1e-11)
    assert float(g.charm(s, k, t, sigma, "P", **kw)) == pytest.approx(ref["charm_put"], rel=1e-11)
    assert float(g.theta(s, k, t, sigma, "C", **kw)) == pytest.approx(ref["theta_call"], rel=1e-11)
    assert float(g.theta(s, k, t, sigma, "P", **kw)) == pytest.approx(ref["theta_put"], rel=1e-11)


def test_black76_on_the_forward_equals_bsm_on_the_spot():
    """The two model names in PLAN.md §2 are one model. F = S*e^((r-q)T) makes them identical."""
    s, k, t, r, q, sigma = 6800.0, 6900.0, 0.3, 0.04, 0.013, 0.18
    fwd = g.forward(s, t, r=r, q=q)
    for right in ("C", "P"):
        assert float(g.black76_price(fwd, k, t, sigma, right, r=r)) == pytest.approx(
            float(g.price(s, k, t, sigma, right, r=r, q=q)), rel=1e-12
        )


def test_per_day_conversion_is_exactly_divide_by_365():
    """Calendar-day annualization, not 252 trading days."""
    args = (6800.0, 6800.0, 0.25, 0.15, "C")
    assert float(g.theta(*args, per_day=True)) == pytest.approx(
        float(g.theta(*args)) / 365.0, rel=1e-15
    )
    assert float(g.charm(*args, per_day=True)) == pytest.approx(
        float(g.charm(*args)) / 365.0, rel=1e-15
    )


# --------------------------------------------------------------------------------------
# Parity, symmetry, and derivative consistency
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_put_call_parity(case):
    """C - P = S*e^(-qT) - K*e^(-rT), and the delta form C' - P' = e^(-qT)."""
    s, k, t, r, q, sigma = case
    call = float(g.price(s, k, t, sigma, "C", r=r, q=q))
    put = float(g.price(s, k, t, sigma, "P", r=r, q=q))
    expected = s * math.exp(-q * t) - k * math.exp(-r * t)
    assert call - put == pytest.approx(expected, rel=1e-12, abs=1e-12)

    dc = float(g.delta(s, k, t, sigma, "C", r=r, q=q))
    dp = float(g.delta(s, k, t, sigma, "P", r=r, q=q))
    assert dc - dp == pytest.approx(math.exp(-q * t), rel=1e-12)


@pytest.mark.parametrize("case", CASES)
def test_gamma_is_identical_for_call_and_put_and_never_negative(case):
    """Gamma symmetry, proved through delta because gamma itself has no `right` argument.

    ``gamma`` deliberately takes no right: it is the same number for a call and a put at the
    same strike, and the module returns it unsigned so that T08 applies the dealer sign
    (+1 calls, -1 puts) exactly once. Here the call and put deltas are differentiated
    numerically and both must land on the single ``gamma`` value.
    """
    s, k, t, r, q, sigma = case
    h = s * 1e-5
    kw = {"r": r, "q": q}
    value = float(g.gamma(s, k, t, sigma, **kw))
    assert value >= 0.0

    for right in ("C", "P"):
        numeric = (
            float(g.delta(s + h, k, t, sigma, right, **kw))
            - float(g.delta(s - h, k, t, sigma, right, **kw))
        ) / (2.0 * h)
        assert numeric == pytest.approx(value, rel=1e-5)


@pytest.mark.parametrize("case", CASES)
def test_gamma_vega_vanna_are_right_independent_by_construction(case):
    """Passing a right to them is impossible; assert the underlying maths agrees anyway."""
    s, k, t, r, q, sigma = case
    h = 1e-6
    kw = {"r": r, "q": q}
    # vanna = d(delta)/d(sigma), identical for call and put.
    value = float(g.vanna(s, k, t, sigma, **kw))
    for right in ("C", "P"):
        numeric = (
            float(g.delta(s, k, t, sigma + h, right, **kw))
            - float(g.delta(s, k, t, sigma - h, right, **kw))
        ) / (2.0 * h)
        assert numeric == pytest.approx(value, rel=1e-5)

    # vega = d(price)/d(sigma); same for both rights by parity.
    vega_value = float(g.vega(s, k, t, sigma, **kw))
    for right in ("C", "P"):
        numeric = (
            float(g.price(s, k, t, sigma + h, right, **kw))
            - float(g.price(s, k, t, sigma - h, right, **kw))
        ) / (2.0 * h)
        assert numeric == pytest.approx(vega_value, rel=1e-5)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("right", ["C", "P"])
def test_charm_and_theta_match_numeric_time_derivatives(case, right):
    """Both are ``-d/dT``: the value falls as the clock runs forward."""
    s, k, t, r, q, sigma = case
    h = t * 1e-5
    kw = {"r": r, "q": q}

    numeric_charm = -(
        float(g.delta(s, k, t + h, sigma, right, **kw))
        - float(g.delta(s, k, t - h, sigma, right, **kw))
    ) / (2.0 * h)
    assert float(g.charm(s, k, t, sigma, right, **kw)) == pytest.approx(numeric_charm, rel=1e-4)

    numeric_theta = -(
        float(g.price(s, k, t + h, sigma, right, **kw))
        - float(g.price(s, k, t - h, sigma, right, **kw))
    ) / (2.0 * h)
    assert float(g.theta(s, k, t, sigma, right, **kw)) == pytest.approx(numeric_theta, rel=1e-4)


def test_long_dated_options_have_theta_below_zero():
    """Sanity on the sign convention a reader is most likely to trip over."""
    assert float(g.theta(6800.0, 6800.0, 0.5, 0.15, "C")) < 0.0
    assert float(g.theta(6800.0, 6800.0, 0.5, 0.15, "P")) < 0.0


# --------------------------------------------------------------------------------------
# Vectorization
# --------------------------------------------------------------------------------------


def test_vectorized_equals_scalar_elementwise():
    rng = np.random.default_rng(20260904)
    n = 500
    spot = 6800.0
    strike = rng.uniform(3000.0, 11000.0, n)
    t = rng.uniform(g.MIN_TIME_TO_EXPIRY, 2.0, n)
    sigma = rng.uniform(0.054, 7.97, n)
    right = np.where(rng.random(n) < 0.5, "C", "P")

    vec = {
        "gamma": g.gamma(spot, strike, t, sigma),
        "vega": g.vega(spot, strike, t, sigma),
        "vanna": g.vanna(spot, strike, t, sigma),
        "price": g.price(spot, strike, t, sigma, right),
        "delta": g.delta(spot, strike, t, sigma, right),
        "charm": g.charm(spot, strike, t, sigma, right),
        "theta": g.theta(spot, strike, t, sigma, right),
    }
    for arr in vec.values():
        assert arr.shape == (n,)

    for i in range(n):
        args = (spot, strike[i], t[i], sigma[i])
        assert float(g.gamma(*args)) == vec["gamma"][i]
        assert float(g.vega(*args)) == vec["vega"][i]
        assert float(g.vanna(*args)) == vec["vanna"][i]
        assert float(g.price(*args, right[i])) == vec["price"][i]
        assert float(g.delta(*args, right[i])) == vec["delta"][i]
        assert float(g.charm(*args, right[i])) == vec["charm"][i]
        assert float(g.theta(*args, right[i])) == vec["theta"][i]


def test_gamma_broadcasts_over_a_spot_grid():
    """The shape T08's gamma profile uses: (grid, 1) spot against (1, n) contract arrays."""
    strike = np.array([6700.0, 6800.0, 6900.0])[None, :]
    t = np.array([0.02, 0.08, 0.5])[None, :]
    sigma = np.array([0.12, 0.14, 0.16])[None, :]
    grid = np.linspace(6120.0, 7480.0, 201)[:, None]

    out = g.gamma(grid, strike, t, sigma)
    assert out.shape == (201, 3)
    assert np.isfinite(out).all()
    assert (out >= 0.0).all()
    # A column of the grid must equal the scalar call at that grid point.
    assert out[100, 1] == float(g.gamma(float(grid[100, 0]), 6800.0, 0.08, 0.14))


def test_right_accepts_enums_letters_words_and_signs():
    args = (6800.0, 6800.0, 0.1, 0.2)
    call = float(g.delta(*args, "C"))
    put = float(g.delta(*args, "P"))
    for value in (Right.CALL, "C", "c", "call", 1, 1.0):
        assert float(g.delta(*args, value)) == call
    for value in (Right.PUT, "P", "p", "put", -1, -1.0):
        assert float(g.delta(*args, value)) == put

    assert g.call_put_sign(np.array(["C", "P", "C"])).tolist() == [1.0, -1.0, 1.0]
    with pytest.raises(ValueError, match="call or a put"):
        g.call_put_sign("X")
    with pytest.raises(ValueError, match=r"\+1"):
        g.call_put_sign(0)


# --------------------------------------------------------------------------------------
# Limits and numerical hazards
# --------------------------------------------------------------------------------------


def test_gamma_goes_to_zero_as_time_to_expiry_grows():
    """Convexity spreads out over an ever-wider range of spot, so per-unit gamma decays."""
    years = np.array([0.25, 1.0, 5.0, 20.0, 100.0, 1000.0])
    values = g.gamma(6800.0, 6800.0, years, 0.15)
    assert np.all(np.diff(values) < 0.0)
    assert values[-1] < 1e-6
    assert np.isfinite(values).all()
    assert (values >= 0.0).all()


def test_extreme_vendor_ivs_stay_finite():
    """The live SPX chain carries IVs from 0.054 to 7.97 (~800%), genuine inversion artifacts.

    They are not a units bug and this module does not reject them; whether to exclude such
    contracts is T08's policy call. What is asserted here is only that nothing blows up.
    """
    ivs = np.array([0.054, 0.1064, 0.5, 1.0, 3.0, 7.97, 8.3191, 50.0])
    strikes = np.array([100.0, 1000.0, 5000.0, 6800.0, 9000.0, 20000.0])
    iv_grid, strike_grid = np.meshgrid(ivs, strikes)
    tenors = np.array([g.MIN_TIME_TO_EXPIRY, 1.0 / 365.0, 0.02, 0.5, 5.0])

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # a RuntimeWarning escaping is itself a failure
        for t in tenors:
            gam = g.gamma(6800.0, strike_grid, t, iv_grid)
            assert np.isfinite(gam).all()
            assert (gam >= 0.0).all()
            for right in ("C", "P"):
                for fn in (g.price, g.delta, g.charm, g.theta):
                    out = fn(6800.0, strike_grid, t, iv_grid, right)
                    assert np.isfinite(out).all(), (fn.__name__, t, right)
            for fn in (g.vega, g.vanna):
                assert np.isfinite(fn(6800.0, strike_grid, t, iv_grid)).all()

    # Deep ITM at 800% vol: small but strictly positive, not underflowed to nothing useful.
    assert 0.0 < float(g.gamma(6800.0, 1000.0, 0.02, 7.97)) < 1e-3


def test_zero_dte_at_the_one_minute_floor():
    """0DTE is a first-class case for SPX. Gamma spikes at the money and vanishes away from it."""
    t = g.MIN_TIME_TO_EXPIRY
    assert t == pytest.approx(60.0 / (365.0 * 86400.0), rel=1e-15)

    atm = float(g.gamma(6800.0, 6800.0, t, 0.11))
    near = float(g.gamma(6800.0, 6805.0, t, 0.11))
    far = float(g.gamma(6800.0, 6000.0, t, 0.11))

    assert math.isfinite(atm) and atm > 0.0
    assert atm > near > far
    assert far == 0.0  # exact underflow of exp(-d1^2/2), not NaN
    assert math.isfinite(float(g.price(6800.0, 6000.0, t, 0.11, "C")))
    # Intrinsic dominates: a one-minute 800-point ITM call is worth ~its intrinsic value.
    assert float(g.price(6800.0, 6000.0, t, 0.11, "C")) == pytest.approx(800.0, abs=0.05)


def test_deep_out_of_the_money_underflows_to_zero_not_nan():
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        strikes = np.array([1.0, 10.0, 100.0, 50_000.0, 1e6, 1e9])
        gam = g.gamma(6800.0, strikes, 0.02, 0.11)
        assert np.isfinite(gam).all()
        assert (gam >= 0.0).all()
        assert gam[0] == 0.0
        assert gam[-1] == 0.0
        prices = g.price(6800.0, strikes, 0.02, 0.11, "C")
        assert np.isfinite(prices).all()
        assert prices[-1] == pytest.approx(0.0, abs=1e-9)


def test_degenerate_sigma_or_time_returns_intrinsic_and_zero_pdf_greeks():
    """Documented policy for sigma == 0 / T == 0: deterministic option, pdf Greeks 0.0."""
    for t, sigma in ((0.0, 0.2), (1.0, 0.0), (0.0, 0.0)):
        assert float(g.gamma(100.0, 100.0, t, sigma, r=0.0, q=0.0)) == 0.0
        assert float(g.vega(100.0, 100.0, t, sigma, r=0.0, q=0.0)) == 0.0
        assert float(g.vanna(100.0, 100.0, t, sigma, r=0.0, q=0.0)) == 0.0
        assert math.isfinite(float(g.charm(100.0, 100.0, t, sigma, "C", r=0.0, q=0.0)))
        assert math.isfinite(float(g.theta(100.0, 100.0, t, sigma, "C", r=0.0, q=0.0)))

    # Zero vol, one year, no carry: the option is worth its (undiscounted-forward) intrinsic.
    assert float(g.price(120.0, 100.0, 1.0, 0.0, "C", r=0.0, q=0.0)) == pytest.approx(20.0)
    assert float(g.price(80.0, 100.0, 1.0, 0.0, "C", r=0.0, q=0.0)) == 0.0
    assert float(g.price(80.0, 100.0, 1.0, 0.0, "P", r=0.0, q=0.0)) == pytest.approx(20.0)
    # Expired: intrinsic on the spot.
    assert float(g.price(120.0, 100.0, 0.0, 0.2, "C")) == pytest.approx(20.0)
    # Delta collapses to the step function, and parity still holds.
    dc = float(g.delta(120.0, 100.0, 0.0, 0.2, "C"))
    dp = float(g.delta(120.0, 100.0, 0.0, 0.2, "P"))
    assert dc == 1.0
    assert dp == 0.0


def test_negative_or_nan_inputs_propagate_nan():
    """No sensible limit exists, so NaN is returned rather than a silently substituted value."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert math.isnan(float(g.gamma(100.0, 100.0, -1.0, 0.2)))
        assert math.isnan(float(g.gamma(100.0, 100.0, 1.0, -0.2)))
        assert math.isnan(float(g.gamma(100.0, 100.0, 1.0, float("nan"))))
        assert math.isnan(float(g.price(100.0, 100.0, 1.0, float("nan"), "C")))
        # NaN in one element must not contaminate its neighbours, nor be swept to 0.0.
        out = g.gamma(100.0, np.array([100.0, 100.0]), np.array([1.0, 1.0]), np.array([0.2, -0.2]))
        assert out[0] > 0.0
        assert math.isnan(out[1])

        # The forward-space entry points must be just as quiet: sqrt(-T) has to happen inside
        # the errstate guard, not before it.
        assert math.isnan(float(g.d1(100.0, 100.0, -1.0, 0.2)))
        assert math.isnan(float(g.d2(100.0, 100.0, -1.0, 0.2)))
        assert math.isnan(float(g.black76_price(100.0, 100.0, -1.0, 0.2, "C")))
        assert math.isnan(float(g.d1(100.0, 100.0, 1.0, float("nan"))))


def test_no_runtime_warnings_on_a_realistic_chain():
    """A full sweep of plausible SPX inputs must not emit a single numpy warning."""
    rng = np.random.default_rng(7)
    n = 5000
    strike = rng.uniform(500.0, 15000.0, n)
    t = np.maximum(rng.exponential(0.3, n), g.MIN_TIME_TO_EXPIRY)
    sigma = rng.uniform(0.054, 7.97, n)
    right = np.where(rng.random(n) < 0.5, "C", "P")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert np.isfinite(g.gamma(6800.0, strike, t, sigma)).all()
        assert np.isfinite(g.price(6800.0, strike, t, sigma, right)).all()
        assert np.isfinite(g.delta(6800.0, strike, t, sigma, right)).all()
        assert np.isfinite(g.vega(6800.0, strike, t, sigma)).all()
        assert np.isfinite(g.vanna(6800.0, strike, t, sigma)).all()
        assert np.isfinite(g.charm(6800.0, strike, t, sigma, right)).all()
        assert np.isfinite(g.theta(6800.0, strike, t, sigma, right)).all()


# --------------------------------------------------------------------------------------
# Rate / yield configuration
# --------------------------------------------------------------------------------------


def test_rate_and_yield_default_from_config_and_are_overridable():
    assert settings.RISK_FREE_RATE == pytest.approx(0.04)
    assert settings.DIVIDEND_YIELD == pytest.approx(0.013)

    args = (6800.0, 6800.0, 0.5, 0.15)
    assert float(g.price(*args, "C")) == pytest.approx(
        float(g.price(*args, "C", r=settings.RISK_FREE_RATE, q=settings.DIVIDEND_YIELD)), rel=1e-15
    )
    # And the parameter actually moves the answer, i.e. it is not quietly ignored.
    assert float(g.price(*args, "C", r=0.10)) != float(g.price(*args, "C", r=0.04))


def test_forward_uses_the_carry():
    assert float(g.forward(6800.0, 1.0, r=0.04, q=0.013)) == pytest.approx(
        6800.0 * math.exp(0.027), rel=1e-14
    )
    assert float(g.forward(6800.0, 0.0, r=0.04, q=0.013)) == 6800.0


def test_d1_takes_the_forward_and_the_spot_shortcut_would_be_wrong():
    """Guard against the spot-for-forward mix-up the module docstring warns about."""
    s, k, t, r, q, sigma = 6800.0, 6800.0, 2.0, 0.04, 0.013, 0.20
    correct = float(g.d1(g.forward(s, t, r=r, q=q), k, t, sigma))
    wrong = float(g.d1(s, k, t, sigma))
    assert correct - wrong == pytest.approx((r - q) * t / (sigma * math.sqrt(t)), rel=1e-12)
    assert abs(correct - wrong) > 0.15  # material on a two-year contract


# --------------------------------------------------------------------------------------
# time_to_expiry: settlement and DST
# --------------------------------------------------------------------------------------


def test_am_settled_has_exactly_six_and_a_half_fewer_hours_than_pm():
    now = dt.datetime(2026, 9, 4, 10, 0, tzinfo=NY)
    expiry = dt.date(2026, 9, 18)
    am = g.time_to_expiry(now, expiry, Settlement.AM)
    pm = g.time_to_expiry(now, expiry, Settlement.PM)
    assert pm - am == pytest.approx(6.5 * 3600.0 / g.SECONDS_PER_YEAR, rel=1e-12)


def test_am_pm_gap_survives_a_dst_boundary():
    """US DST starts 2026-03-08, so `now` is EST (UTC-5) and the expiry is EDT (UTC-4).

    The 6.5-hour gap is unchanged (both instants sit on the same date, same offset), but the
    absolute time to expiry is one hour *shorter* than a naive same-offset day count gives.
    """
    now = dt.datetime(2026, 3, 5, 16, 0, tzinfo=NY)  # EST
    expiry = dt.date(2026, 3, 20)  # EDT
    assert now.utcoffset() == dt.timedelta(hours=-5)
    assert dt.datetime.combine(expiry, dt.time(16, 0), tzinfo=NY).utcoffset() == dt.timedelta(
        hours=-4
    )

    pm = g.time_to_expiry(now, expiry, Settlement.PM)
    am = g.time_to_expiry(now, expiry, Settlement.AM)
    assert pm - am == pytest.approx(6.5 * 3600.0 / g.SECONDS_PER_YEAR, rel=1e-12)

    # 14 days 23 hours, not 15 days: the hour lost to the spring-forward transition.
    assert pm == pytest.approx(1_292_400.0 / g.SECONDS_PER_YEAR, rel=1e-15)
    naive_same_offset = 15 * 86400.0 / g.SECONDS_PER_YEAR
    assert naive_same_offset - pm == pytest.approx(3600.0 / g.SECONDS_PER_YEAR, rel=1e-12)
    assert am == pytest.approx((1_292_400.0 - 6.5 * 3600.0) / g.SECONDS_PER_YEAR, rel=1e-15)


def test_autumn_dst_boundary_adds_an_hour():
    """DST ends 2026-11-01: an expiry after it is one hour *further* away than a naive count."""
    now = dt.datetime(2026, 10, 30, 16, 0, tzinfo=NY)  # EDT
    pm = g.time_to_expiry(now, dt.date(2026, 11, 20), Settlement.PM)
    naive_same_offset = 21 * 86400.0 / g.SECONDS_PER_YEAR
    assert pm - naive_same_offset == pytest.approx(3600.0 / g.SECONDS_PER_YEAR, rel=1e-12)


def test_annualization_is_calendar_days_over_365():
    """Not trading days / 252. A 365-day gap is exactly 1.0 years."""
    now = dt.datetime(2026, 9, 18, 16, 0, tzinfo=NY)
    # 2027-09-18 16:00 NY is also EDT, so the gap is exactly 365 calendar days.
    assert g.time_to_expiry(now, dt.date(2027, 9, 18), Settlement.PM) == pytest.approx(
        1.0, rel=1e-15
    )
    # Weekends are counted like any other day: Friday close to Monday close is 3/365.
    friday = dt.datetime(2026, 9, 18, 16, 0, tzinfo=NY)
    assert g.time_to_expiry(friday, dt.date(2026, 9, 21), Settlement.PM) == pytest.approx(
        3.0 / 365.0, rel=1e-12
    )


def test_zero_dte_is_floored_at_one_minute():
    """15:59 on a PM expiry day: one minute of life, floored, never zero and never negative."""
    now = dt.datetime(2026, 9, 4, 15, 59, tzinfo=NY)
    exact = g.time_to_expiry(now, dt.date(2026, 9, 4), Settlement.PM)
    assert exact == pytest.approx(g.MIN_TIME_TO_EXPIRY, rel=1e-12)

    # 15:59:30 is below the floor, so it clamps rather than halving.
    late = g.time_to_expiry(
        dt.datetime(2026, 9, 4, 15, 59, 30, tzinfo=NY), dt.date(2026, 9, 4), Settlement.PM
    )
    assert late == g.MIN_TIME_TO_EXPIRY
    assert float(g.gamma(6800.0, 6800.0, late, 0.11)) > 0.0

    # An AM-settled contract on the same date expired hours ago and is also floored — which is
    # why is_expired() exists and T08 must call it.
    stale = g.time_to_expiry(now, dt.date(2026, 9, 4), Settlement.AM)
    assert stale == g.MIN_TIME_TO_EXPIRY
    assert g.is_expired(now, dt.date(2026, 9, 4), Settlement.AM) is True
    assert g.is_expired(now, dt.date(2026, 9, 4), Settlement.PM) is False
    assert g.is_expired(now, dt.date(2026, 9, 5), Settlement.AM) is False


def test_time_to_expiry_vectorizes_and_matches_the_scalar_path():
    now = dt.datetime(2026, 9, 4, 12, 30, tzinfo=NY)
    expiries = [dt.date(2026, 9, 4), dt.date(2026, 9, 18), dt.date(2027, 6, 17)]
    settlements = [Settlement.PM, Settlement.AM, Settlement.AM]

    out = g.time_to_expiry(now, expiries, settlements)
    assert isinstance(out, np.ndarray)
    assert out.shape == (3,)
    for i, (e, s) in enumerate(zip(expiries, settlements, strict=True)):
        assert out[i] == g.time_to_expiry(now, e, s)

    # A scalar settlement broadcasts across an array of expiries.
    broadcast = g.time_to_expiry(now, expiries, Settlement.PM)
    assert broadcast.shape == (3,)
    assert broadcast[1] == g.time_to_expiry(now, expiries[1], Settlement.PM)

    expired = g.is_expired(now, expiries, settlements)
    assert expired.tolist() == [False, False, False]


def test_naive_now_is_rejected():
    naive = dt.datetime(2026, 9, 4, 12, 0)  # noqa: DTZ001 — the point of the test
    with pytest.raises(ValueError, match="timezone-aware"):
        g.time_to_expiry(naive, dt.date(2026, 9, 18), Settlement.PM)


def test_expiry_datetime_is_the_new_york_wall_clock():
    am = g.expiry_datetime(dt.date(2026, 9, 18), Settlement.AM)
    pm = g.expiry_datetime(dt.date(2026, 9, 18), Settlement.PM)
    assert (am.hour, am.minute) == (9, 30)
    assert (pm.hour, pm.minute) == (16, 0)
    assert pm - am == dt.timedelta(hours=6, minutes=30)
    assert am.tzinfo is not None


# --------------------------------------------------------------------------------------
# Performance — T08 has a 2 s budget for compute_all, of which this is the inner loop
# --------------------------------------------------------------------------------------


def test_full_chain_gamma_profile_is_fast():
    """28,650 contracts x a 201-point +/-10% grid = 5.76 M evaluations, as T08 will call it."""
    rng = np.random.default_rng(1)
    n = 28_650
    strike = rng.uniform(1000.0, 12_000.0, n)[None, :]
    t = np.maximum(rng.exponential(0.4, n), g.MIN_TIME_TO_EXPIRY)[None, :]
    sigma = rng.uniform(0.054, 7.97, n)[None, :]
    grid = np.linspace(0.9 * 6800.0, 1.1 * 6800.0, 201)[:, None]

    g.gamma(grid[:2], strike, t, sigma)  # warm up numpy's allocator
    start = time.perf_counter()
    out = g.gamma(grid, strike, t, sigma)
    elapsed = time.perf_counter() - start

    assert out.shape == (201, n)
    assert np.isfinite(out).all()
    # Generous headroom over the ~0.15 s measured locally; this guards a real regression
    # (e.g. someone reintroducing scipy.stats or a Python loop), not CI jitter.
    assert elapsed < 1.5, f"gamma over 5.76M evaluations took {elapsed:.3f}s"


def test_time_to_expiry_over_a_full_chain_is_fast():
    """~28.6k contracts but only ~112 distinct (expiry, settlement) pairs."""
    now = dt.datetime(2026, 9, 4, 16, 5, tzinfo=NY)
    base = dt.date(2026, 9, 4)
    expiries = [base + dt.timedelta(days=int(d)) for d in np.arange(56) * 7]
    rng = np.random.default_rng(3)
    idx = rng.integers(0, len(expiries), 28_650)
    exp_arr = [expiries[i] for i in idx]
    set_arr = np.where(rng.random(28_650) < 0.5, "AM", "PM")

    start = time.perf_counter()
    out = g.time_to_expiry(now, exp_arr, set_arr)
    elapsed = time.perf_counter() - start

    assert out.shape == (28_650,)
    assert (out >= g.MIN_TIME_TO_EXPIRY).all()
    assert elapsed < 1.0, f"time_to_expiry over 28,650 contracts took {elapsed:.3f}s"
