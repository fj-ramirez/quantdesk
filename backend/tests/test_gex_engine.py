"""Tests for app.modules.gex.gex.engine.

Written so that correctness is provable without trusting the implementation. The centre of
the suite is :func:`synthetic_chain`, a 20-contract chain whose every input is chosen by hand,
against which :func:`gex_reference` — scalar Python, its own Black-Scholes gamma, its own
seconds-to-expiry arithmetic, no shared code with the module under test — reproduces every
contract's dollar GEX exactly. Everything else (per-strike sums, key levels, filters) is then
checked against that same construction rather than against the engine's own output.

The SPX fixture test is a *plausibility* check and is labelled as one. TASKS.md T08 asks for
"net GEX positive and within an order of magnitude of 50 B USD"; that figure belongs to the
**full** 28,650-contract chain. ``tests/fixtures/cboe/spx.json`` is trimmed to 246 contracts
clustered around the money, where puts dominate, so its net GEX is legitimately negative
(≈ −8 B). Asserting the 50 B band on it would be asserting a falsehood. What the fixture can
prove is structural — internal consistency, filter containment, serializability, and the 2 s
performance bar — and that is what it is asked here.

Hazards specifically covered because they are the ways this module can be silently wrong:
the dealer sign applied twice, ``time_to_expiry``'s one-minute floor resurrecting an expired
contract as a 0DTE gamma spike, ``open_interest=None`` treated as zero, a hardcoded 100
multiplier, AM and PM contracts on one expiry date sharing a time to expiry, walls collapsing
onto one strike, and a gamma profile that merely touches zero being read as a flip.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd
import pytest

from app.core.config import settings
from app.modules.gex.gex import engine as E
from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
from app.modules.gex.providers.cboe import CboeProvider

NY = ZoneInfo("America/New_York")
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cboe"

#: The snapshot instant every synthetic chain is observed at: Friday 2026-09-04, 14:00 New
#: York. Chosen so the AM-settled series expiring that morning is already dead while the
#: PM-settled series of the same date is still alive — the case the floor in
#: ``greeks.time_to_expiry`` would otherwise hide.
AS_OF = dt.datetime(2026, 9, 4, 14, 0, tzinfo=NY)
SPOT = 5000.0

SECONDS_PER_YEAR = 365.0 * 86400.0


# --------------------------------------------------------------------------------------
# Independent reference implementation
# --------------------------------------------------------------------------------------


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def years_to_expiry(expiry: dt.date, settlement: str) -> float:
    """Calendar seconds from :data:`AS_OF` to the expiry instant, over 365 x 86400.

    Deliberately re-derived here from ``zoneinfo`` rather than imported: the AM/PM split is
    exactly the thing under test, so the test must not borrow the module's own answer.
    """
    at = dt.time(9, 30) if settlement == "AM" else dt.time(16, 0)
    instant = dt.datetime.combine(expiry, at, tzinfo=NY)
    return (instant - AS_OF).total_seconds() / SECONDS_PER_YEAR


def bs_gamma(spot: float, strike: float, t: float, sigma: float) -> float:
    """Black-Scholes-Merton spot gamma, unsigned: e^(-qT) n(d1) / (S sigma sqrt(T))."""
    r, q = settings.RISK_FREE_RATE, settings.DIVIDEND_YIELD
    v = sigma * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q) * t) / v + 0.5 * v
    return math.exp(-q * t) * norm_pdf(d1) / (spot * v)


def gex_reference(spec: dict[str, Any], spot: float = SPOT, *, iv_band: bool = True) -> float:
    """Dollar GEX for one hand-written contract spec, from first principles.

    ``sign * gamma * open_interest * multiplier * spot**2 * 0.01``, with every ineligibility
    rule applied explicitly so the expected value for an excluded contract is a literal 0.0
    rather than something the engine also computed. ``iv_band=False`` drops the default
    ``IvPolicy`` cut, for checking the ``CLAMP`` and ``KEEP`` modes.
    """
    t = years_to_expiry(spec["expiry"], spec["settlement"])
    if t <= 0.0:  # expired: excluded outright, never floored into a live one-minute option
        return 0.0
    if spec["oi"] is None or spec["iv"] is None:  # unknown OI / unpriceable vol
        return 0.0
    if iv_band and not 0.01 <= spec["iv"] <= 3.0:  # default IvPolicy band
        return 0.0
    sign = 1.0 if spec["right"] == "C" else -1.0
    gamma = bs_gamma(spot, spec["strike"], t, spec["iv"])
    return sign * gamma * spec["oi"] * spec["mult"] * spot * spot * 0.01


# --------------------------------------------------------------------------------------
# The synthetic 20-contract chain
# --------------------------------------------------------------------------------------

TODAY = dt.date(2026, 9, 4)  # Friday, the snapshot's New York date -> 0DTE and this-week
NEXT_FRI = dt.date(2026, 9, 11)  # next week, second Friday -> neither 0DTE nor monthly
MONTHLY = dt.date(2026, 9, 18)  # third Friday -> monthly for both roots
FAR_AM = dt.date(2027, 6, 17)  # a Thursday: monthly only because the SPX series is AM-settled


def _occ(root: str, expiry: dt.date, right: str, strike: float) -> str:
    return f"{root}{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


#: Every contract spelled out. ``settlement`` is redundant with ``root`` (the schema derives
#: it) and is repeated only so the reference implementation above never has to know the rule.
SPECS: tuple[dict[str, Any], ...] = (
    # --- 0DTE, PM-settled: alive at 14:00, two hours of life left.
    {"root": "SPXW", "expiry": TODAY, "settlement": "PM", "right": "C", "strike": 5000.0,
     "oi": 1000, "iv": 0.20, "mult": 100},
    {"root": "SPXW", "expiry": TODAY, "settlement": "PM", "right": "P", "strike": 5000.0,
     "oi": 2000, "iv": 0.20, "mult": 100},
    # --- 0DTE, AM-settled, same date: dead since 09:30. The huge open interest is the point:
    # if the one-minute floor were used instead of `is_expired`, these two would dominate
    # everything with a spurious ATM spike.
    {"root": "SPX", "expiry": TODAY, "settlement": "AM", "right": "C", "strike": 5000.0,
     "oi": 999_999, "iv": 0.20, "mult": 100},
    {"root": "SPX", "expiry": TODAY, "settlement": "AM", "right": "P", "strike": 5000.0,
     "oi": 999_999, "iv": 0.20, "mult": 100},
    # --- Next Friday: in ALL and EX_ZERO_DTE only.
    {"root": "SPXW", "expiry": NEXT_FRI, "settlement": "PM", "right": "C", "strike": 5100.0,
     "oi": 500, "iv": 0.18, "mult": 100},
    {"root": "SPXW", "expiry": NEXT_FRI, "settlement": "PM", "right": "P", "strike": 4900.0,
     "oi": 700, "iv": 0.19, "mult": 100},
    {"root": "SPXW", "expiry": NEXT_FRI, "settlement": "PM", "right": "C", "strike": 5000.0,
     "oi": 250, "iv": 0.18, "mult": 100},
    # --- Third Friday, both roots at strike 5000: the SPX/SPXW merge, with genuinely
    # different times to expiry (09:30 vs 16:00) feeding one strike bucket.
    {"root": "SPX", "expiry": MONTHLY, "settlement": "AM", "right": "C", "strike": 5000.0,
     "oi": 300, "iv": 0.17, "mult": 100},
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "C", "strike": 5000.0,
     "oi": 400, "iv": 0.17, "mult": 100},
    {"root": "SPX", "expiry": MONTHLY, "settlement": "AM", "right": "P", "strike": 5000.0,
     "oi": 250, "iv": 0.17, "mult": 100},
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "P", "strike": 5000.0,
     "oi": 350, "iv": 0.17, "mult": 100},
    # --- 2027-06-17 is a Thursday, so the third-Friday rule misses it; MONTHLY_ONLY must
    # still catch it because the SPX series is AM-settled.
    {"root": "SPX", "expiry": FAR_AM, "settlement": "AM", "right": "C", "strike": 5500.0,
     "oi": 100, "iv": 0.22, "mult": 100},
    {"root": "SPX", "expiry": FAR_AM, "settlement": "AM", "right": "P", "strike": 4500.0,
     "oi": 120, "iv": 0.23, "mult": 100},
    # --- Eligibility edge cases, all on the third Friday so they ride every filter that
    # matters.
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "C", "strike": 5200.0,
     "oi": None, "iv": 0.16, "mult": 100},        # unknown OI -> excluded, not zeroed
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "P", "strike": 4800.0,
     "oi": 0, "iv": 0.16, "mult": 100},           # genuine zero OI -> included, contributes 0
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "C", "strike": 5300.0,
     "oi": 800, "iv": None, "mult": 100},         # no vol -> unpriceable under every policy
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "P", "strike": 4700.0,
     "oi": 900, "iv": 5.0, "mult": 100},          # 500% vol -> outside the default IV band
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "C", "strike": 5400.0,
     "oi": 600, "iv": 0.15, "mult": 50},          # adjusted contract: multiplier is not 100
    # --- Ordinary flanking strikes.
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "C", "strike": 5100.0,
     "oi": 1500, "iv": 0.16, "mult": 100},
    {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "P", "strike": 4900.0,
     "oi": 1800, "iv": 0.16, "mult": 100},
)


def _contract(spec: dict[str, Any]) -> OptionContract:
    return OptionContract.from_occ(
        _occ(spec["root"], spec["expiry"], spec["right"], spec["strike"]),
        open_interest=spec["oi"],
        iv=spec["iv"],
        gamma=0.001,  # a vendor number, deliberately wrong, so vendor mode is distinguishable
        multiplier=spec["mult"],
    )


def synthetic_chain(specs: tuple[dict[str, Any], ...] = SPECS, spot: float = SPOT) -> ChainSnapshot:
    return ChainSnapshot(
        underlying=Underlying.SPX,
        spot=spot,
        captured_at=AS_OF,
        source="synthetic",
        delayed_minutes=0,
        contracts=tuple(_contract(s) for s in specs),
    )


@pytest.fixture
def chain() -> ChainSnapshot:
    return synthetic_chain()


@pytest.fixture
def frame(chain: ChainSnapshot):
    return E.to_frame(chain)


# --------------------------------------------------------------------------------------
# The core claim: per-contract GEX equals the hand computation
# --------------------------------------------------------------------------------------


def test_the_chain_is_twenty_contracts():
    assert len(SPECS) == 20


def test_contract_gex_matches_hand_computation(chain, frame):
    """Every contract, against a reference that shares no code with the engine."""
    got = E.contract_gex(frame, SPOT).to_numpy(dtype=float)
    expected = np.array([gex_reference(s) for s in SPECS])
    assert got == pytest.approx(expected, rel=1e-12, abs=1e-6)
    # The construction is only meaningful if the reference is not trivially all zeros: 15
    # contracts are eligible and 14 of them carry open interest.
    assert np.count_nonzero(expected) == 14
    assert np.count_nonzero(got == 0.0) == 6


def test_net_gex_is_the_sum_of_the_hand_computation(chain):
    result = E.compute_all(chain)
    assert result.net_gex == pytest.approx(sum(gex_reference(s) for s in SPECS), rel=1e-12)


def test_abs_gex_is_the_unsigned_variant(frame):
    signed = E.contract_gex(frame, SPOT).to_numpy(dtype=float)
    assert E.abs_gex(frame, SPOT).to_numpy(dtype=float) == pytest.approx(np.abs(signed), rel=1e-12)


# --------------------------------------------------------------------------------------
# Sign convention: applied exactly once
# --------------------------------------------------------------------------------------


def test_dealer_sign_is_applied_exactly_once(frame):
    """Calls positive, puts negative, and the ratio between a matched pair is the OI ratio.

    Gamma is unsigned and identical for a call and a put at the same strike, expiry and vol,
    so the 0DTE PM pair (OI 1000 call, OI 2000 put) must come back as ``-2x``. A sign applied
    twice, or applied inside ``greeks.gamma``, breaks this exactly.
    """
    gex = E.contract_gex(frame, SPOT).to_numpy(dtype=float)
    rights = frame["right"].to_numpy(dtype=object)
    assert (gex[rights == "C"] >= 0).all()
    assert (gex[rights == "P"] <= 0).all()
    call_0dte, put_0dte = gex[0], gex[1]
    assert call_0dte > 0 and put_0dte < 0
    assert put_0dte == pytest.approx(-2.0 * call_0dte, rel=1e-12)


def test_call_and_put_gex_split_reconciles_with_net(chain):
    levels = E.compute_all(chain).levels
    assert levels.call_gex > 0
    assert levels.put_gex < 0
    assert levels.net_gex == pytest.approx(levels.call_gex + levels.put_gex, rel=1e-12)
    assert levels.abs_gex == pytest.approx(levels.call_gex - levels.put_gex, rel=1e-12)


# --------------------------------------------------------------------------------------
# Expiry, settlement and the one-minute floor
# --------------------------------------------------------------------------------------


def test_expired_am_series_is_dropped_not_floored(chain, frame):
    """The AM 0DTE pair carries 999,999 contracts of OI and must contribute exactly nothing."""
    expired = frame["expired"].to_numpy(dtype=bool)
    assert expired.tolist()[:4] == [False, False, True, True]

    gex = E.contract_gex(frame, SPOT).to_numpy(dtype=float)
    assert gex[2] == 0.0
    assert gex[3] == 0.0

    # And the aggregate is identical to the same chain with those two contracts deleted.
    without = E.compute_all(synthetic_chain(SPECS[:2] + SPECS[4:]))
    assert E.compute_all(chain).net_gex == pytest.approx(without.net_gex, rel=1e-12)
    assert E.compute_all(chain).diagnostics.expired == 2


def test_am_and_pm_on_one_date_have_different_times_to_expiry(frame):
    """Same expiry date, 6.5 hours apart. Read per contract, never inferred from the date."""
    monthly_am = frame[(frame["expiry"] == MONTHLY) & (frame["settlement"] == "AM")]
    monthly_pm = frame[(frame["expiry"] == MONTHLY) & (frame["settlement"] == "PM")]
    t_am = float(monthly_am["t"].iloc[0])
    t_pm = float(monthly_pm["t"].iloc[0])
    assert t_pm - t_am == pytest.approx(6.5 * 3600.0 / SECONDS_PER_YEAR, rel=1e-12)
    assert t_am == pytest.approx(years_to_expiry(MONTHLY, "AM"), rel=1e-12)
    assert t_pm == pytest.approx(years_to_expiry(MONTHLY, "PM"), rel=1e-12)


def test_matched_am_pm_calls_differ_in_gex(frame):
    """Two calls, same strike and vol and expiry date, differing only in settlement."""
    gex = E.contract_gex(frame, SPOT).to_numpy(dtype=float)
    am_per_contract = gex[7] / SPECS[7]["oi"]
    pm_per_contract = gex[8] / SPECS[8]["oi"]
    assert am_per_contract != pytest.approx(pm_per_contract, rel=1e-9)
    # The shorter-dated (AM) contract has the larger ATM gamma.
    assert am_per_contract > pm_per_contract


# --------------------------------------------------------------------------------------
# Eligibility: None OI, zero OI, missing IV, extreme IV, multiplier
# --------------------------------------------------------------------------------------


def test_unknown_open_interest_is_excluded_and_zero_is_included(chain, frame):
    ok = E.include_mask(frame)
    assert ok[13] is np.False_ or not ok[13]  # oi=None -> excluded
    assert ok[14]  # oi=0 -> included
    assert E.contract_gex(frame, SPOT).to_numpy(dtype=float)[14] == 0.0

    diagnostics = E.compute_all(chain).diagnostics
    assert diagnostics.missing_open_interest == 1
    assert diagnostics.zero_open_interest == 1

    # Zeroing the unknown would be indistinguishable from excluding it *unless* the exclusion
    # also removes the strike from the per-strike table, which it must.
    strikes = {row.strike for row in E.compute_all(chain).by_strike}
    assert 5200.0 not in strikes  # the oi=None contract's strike
    assert 4800.0 in strikes  # the oi=0 contract's strike survives, contributing 0


def test_multiplier_is_read_per_contract(frame):
    """The 5400 call has multiplier 50; a hardcoded 100 would double its GEX."""
    gex = E.contract_gex(frame, SPOT).to_numpy(dtype=float)
    assert gex[17] == pytest.approx(gex_reference(SPECS[17]), rel=1e-12)
    doubled = dict(SPECS[17]) | {"mult": 100}
    assert gex[17] == pytest.approx(gex_reference(doubled) / 2.0, rel=1e-12)


def test_missing_iv_is_never_priced_under_any_policy(frame):
    for mode in E.IvPolicyMode:
        policy = E.IvPolicy(mode=mode)
        assert E.contract_gex(frame, SPOT, iv_policy=policy).to_numpy(dtype=float)[15] == 0.0


def test_iv_policy_modes(chain, frame):
    excluded = E.contract_gex(frame, SPOT).to_numpy(dtype=float)[16]
    clamped = E.contract_gex(
        frame, SPOT, iv_policy=E.IvPolicy(mode=E.IvPolicyMode.CLAMP)
    ).to_numpy(dtype=float)[16]
    kept = E.contract_gex(frame, SPOT, iv_policy=E.IvPolicy(mode="KEEP")).to_numpy(dtype=float)[16]

    assert excluded == 0.0
    assert clamped == pytest.approx(
        gex_reference(dict(SPECS[16]) | {"iv": 3.0}, iv_band=False), rel=1e-12
    )
    assert kept == pytest.approx(gex_reference(SPECS[16], iv_band=False), rel=1e-12)
    assert clamped < 0 and kept < 0  # it is a put
    # 500 % vol flattens gamma rather than spiking it, which is exactly why the artifact is
    # insidious: it smears a small non-decaying contribution across the whole profile.
    assert abs(kept) < abs(clamped)


def test_iv_diagnostics_reconcile(chain):
    """``net_gex + extreme_iv_gex_excluded == net_gex_iv_unfiltered``, so T10 needs one sum."""
    result = E.compute_all(chain)
    d = result.diagnostics
    assert d.extreme_iv == 1
    assert d.extreme_iv_open_interest == 900
    assert d.missing_iv == 1
    assert d.iv_max_observed == pytest.approx(5.0)
    assert d.net_gex_iv_unfiltered == pytest.approx(result.net_gex + d.extreme_iv_gex_excluded)

    unfiltered = E.compute_all(chain, iv_policy=E.IvPolicy(mode=E.IvPolicyMode.KEEP))
    assert unfiltered.net_gex == pytest.approx(d.net_gex_iv_unfiltered, rel=1e-12)
    assert unfiltered.diagnostics.extreme_iv_gex_excluded == 0.0


def test_diagnostic_counts_partition_the_selection(chain):
    d = E.compute_all(chain).diagnostics
    assert d.contracts == 20
    # The four exclusion reasons are disjoint on this chain by construction.
    assert d.included == d.contracts - d.expired - d.missing_open_interest - d.missing_iv - d.extreme_iv
    assert d.included == 15


def test_vendor_gamma_mode_uses_the_vendor_number(frame):
    """Every synthetic contract carries a deliberately wrong vendor gamma of 0.001."""
    gex = E.contract_gex(frame, SPOT, use_vendor_gamma=True).to_numpy(dtype=float)
    sign = 1.0 if SPECS[0]["right"] == "C" else -1.0
    expected = sign * 0.001 * SPECS[0]["oi"] * SPECS[0]["mult"] * SPOT * SPOT * 0.01
    assert gex[0] == pytest.approx(expected, rel=1e-12)
    assert gex[2] == 0.0  # expiry filtering still applies in vendor mode


# --------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------


def test_by_strike_merges_spx_and_spxw_into_one_bucket(chain, frame):
    """Four contracts at 5000 on the third Friday — two roots, two rights — plus the 0DTE pair
    and the next-Friday call all land on one row."""
    rows = E.by_strike(frame, spot=SPOT)
    row = rows[rows["strike"] == 5000.0].iloc[0]
    # Eligible contracts at strike 5000: indices 0, 1, 6, 7, 8, 9, 10 (2 and 3 are expired).
    eligible = [0, 1, 6, 7, 8, 9, 10]
    assert int(row["contracts"]) == len(eligible)
    assert int(row["open_interest"]) == sum(SPECS[i]["oi"] for i in eligible)
    assert row["net_gex"] == pytest.approx(sum(gex_reference(SPECS[i]) for i in eligible), rel=1e-12)
    assert row["call_gex"] == pytest.approx(
        sum(gex_reference(SPECS[i]) for i in eligible if SPECS[i]["right"] == "C"), rel=1e-12
    )
    assert row["put_gex"] == pytest.approx(
        sum(gex_reference(SPECS[i]) for i in eligible if SPECS[i]["right"] == "P"), rel=1e-12
    )


def test_by_strike_is_sorted_and_totals_to_net(chain):
    result = E.compute_all(chain)
    strikes = [row.strike for row in result.by_strike]
    assert strikes == sorted(strikes)
    assert len(strikes) == len(set(strikes))
    assert sum(row.net_gex for row in result.by_strike) == pytest.approx(result.net_gex, rel=1e-12)


def test_by_expiry_totals_and_dte(chain):
    result = E.compute_all(chain)
    by_expiry = {row.expiry: row for row in result.by_expiry}
    assert set(by_expiry) == {TODAY, NEXT_FRI, MONTHLY, FAR_AM}
    assert by_expiry[TODAY].dte == 0
    assert by_expiry[NEXT_FRI].dte == 7
    assert by_expiry[MONTHLY].dte == 14
    assert sum(row.net_gex for row in result.by_expiry) == pytest.approx(result.net_gex, rel=1e-12)
    # The 0DTE row holds only the two live PM contracts; the dead AM pair is gone.
    assert by_expiry[TODAY].contracts == 2
    assert by_expiry[TODAY].net_gex == pytest.approx(
        gex_reference(SPECS[0]) + gex_reference(SPECS[1]), rel=1e-12
    )


# --------------------------------------------------------------------------------------
# Expiry filters
# --------------------------------------------------------------------------------------


def _selected_expiries(frame, filters) -> set[dt.date]:
    return set(frame.loc[E.expiry_mask(frame, filters), "expiry"].unique())


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (E.ExpiryFilter.ALL, {TODAY, NEXT_FRI, MONTHLY, FAR_AM}),
        (E.ExpiryFilter.ZERO_DTE, {TODAY}),
        (E.ExpiryFilter.EX_ZERO_DTE, {NEXT_FRI, MONTHLY, FAR_AM}),
        (E.ExpiryFilter.THIS_WEEK, {TODAY}),
        # TODAY is in MONTHLY_ONLY only because this chain deliberately carries an AM-settled
        # SPX pair on a non-third-Friday, which the live chain never does (the AM series *is*
        # the monthly series). The rule is "third Friday OR AM-settled" and it is row-wise:
        # see the next test, where only the AM rows of that date are admitted.
        (E.ExpiryFilter.MONTHLY_ONLY, {TODAY, MONTHLY, FAR_AM}),
    ],
)
def test_each_expiry_filter_selects_what_it_claims(frame, filters, expected):
    assert _selected_expiries(frame, filters) == expected
    assert _selected_expiries(frame, filters.value) == expected  # the string name works too


def test_monthly_only_is_row_wise_not_date_wise(frame):
    """On a date carrying both settlements, MONTHLY_ONLY admits the AM rows only."""
    monthly = frame.loc[E.expiry_mask(frame, E.ExpiryFilter.MONTHLY_ONLY)]
    today = monthly.loc[monthly["expiry"] == TODAY]
    assert set(today["settlement"]) == {"AM"}
    # The third Friday admits both, because the date itself qualifies.
    third = monthly.loc[monthly["expiry"] == MONTHLY]
    assert set(third["settlement"]) == {"AM", "PM"}


def test_monthly_only_catches_the_am_series_that_is_not_a_third_friday():
    """2027-06-17 is a Thursday. The third-Friday rule alone would drop it."""
    assert FAR_AM.weekday() != 4
    assert E._third_friday(FAR_AM) != FAR_AM
    frame = E.to_frame(synthetic_chain())
    monthly = frame.loc[E.expiry_mask(frame, E.ExpiryFilter.MONTHLY_ONLY)]
    assert FAR_AM in set(monthly["expiry"])
    # ...and it is admitted because it is AM-settled, not because of the date.
    assert set(monthly.loc[monthly["expiry"] == FAR_AM, "settlement"]) == {"AM"}


def test_zero_dte_and_ex_zero_dte_partition_the_chain(chain, frame):
    zero = E.expiry_mask(frame, E.ExpiryFilter.ZERO_DTE)
    ex_zero = E.expiry_mask(frame, E.ExpiryFilter.EX_ZERO_DTE)
    assert (zero ^ ex_zero).all()
    total = E.compute_all(chain, E.ExpiryFilter.ALL).net_gex
    parts = (
        E.compute_all(chain, E.ExpiryFilter.ZERO_DTE).net_gex
        + E.compute_all(chain, E.ExpiryFilter.EX_ZERO_DTE).net_gex
    )
    assert total == pytest.approx(parts, rel=1e-12)


def test_explicit_expiry_list(frame):
    assert _selected_expiries(frame, [MONTHLY]) == {MONTHLY}
    assert _selected_expiries(frame, (NEXT_FRI, FAR_AM)) == {NEXT_FRI, FAR_AM}
    # A datetime is accepted and reduced to its date.
    assert _selected_expiries(frame, [dt.datetime(2026, 9, 18, 12, 0, tzinfo=dt.UTC)]) == {MONTHLY}
    # An empty list selects nothing, which is not the same as ALL.
    assert not E.expiry_mask(frame, []).any()
    assert _selected_expiries(frame, [dt.date(2030, 1, 18)]) == set()


def test_explicit_expiry_list_reaches_compute_all(chain):
    result = E.compute_all(chain, [MONTHLY])
    assert result.expiries == (MONTHLY,)
    assert result.filter == "EXPIRIES:2026-09-18"
    monthly_specs = [s for s in SPECS if s["expiry"] == MONTHLY]
    assert result.net_gex == pytest.approx(sum(gex_reference(s) for s in monthly_specs), rel=1e-12)


def test_bad_filter_values_raise(frame):
    with pytest.raises(ValueError, match="unknown expiry filter"):
        E.expiry_mask(frame, "NEXT_WEEK")
    with pytest.raises(ValueError, match="must contain dates"):
        E.expiry_mask(frame, ["2026-09-18"])


# --------------------------------------------------------------------------------------
# Key levels and the wall definition
# --------------------------------------------------------------------------------------


def _wall_chain() -> ChainSnapshot:
    """A chain built to break the per-side wall definition.

    Strike 5000 carries by far the most gamma on **both** sides, so ``argmax(call_gex)`` and
    ``argmin(put_gex)`` both land on it and the two "walls" coincide at a single strike that
    brackets nothing. Its *net* is positive and modest; the real resistance is 5200 (calls
    only) and the real support is 4800 (puts only).
    """
    specs = (
        {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "C", "strike": 5000.0,
         "oi": 50_000, "iv": 0.16, "mult": 100},
        {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "P", "strike": 5000.0,
         "oi": 48_000, "iv": 0.16, "mult": 100},
        {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "C", "strike": 5200.0,
         "oi": 30_000, "iv": 0.16, "mult": 100},
        {"root": "SPXW", "expiry": MONTHLY, "settlement": "PM", "right": "P", "strike": 4800.0,
         "oi": 30_000, "iv": 0.16, "mult": 100},
    )
    return synthetic_chain(specs)


def test_walls_are_net_based_and_bracket_spot_when_one_strike_dominates_both_sides():
    """Regression: the per-side definition collapsed both walls onto the same strike.

    On the live SPX chain the round-number 8000 strike owned ``argmax(call_gex)`` *and*
    ``argmin(put_gex)`` at once, reporting "call wall 8000, put wall 8000" with spot at 7719.
    This chain reproduces that shape in four contracts.
    """
    result = E.compute_all(_wall_chain())
    levels = result.levels

    # The per-side extrema really do collapse — that is the trap, and it is still reported,
    # under a name that does not claim to be a level.
    assert levels.max_call_gex_strike == 5000.0
    assert levels.max_put_gex_strike == 5000.0

    # The walls do not, and they bracket spot.
    assert levels.call_wall == 5200.0
    assert levels.put_wall == 4800.0
    assert levels.put_wall < SPOT < levels.call_wall
    assert levels.call_wall_gex > 0
    assert levels.put_wall_gex < 0

    # call_wall/put_wall are the net extrema, by definition.
    assert levels.call_wall == levels.max_net_strike
    assert levels.put_wall == levels.min_net_strike
    by_strike = {row.strike: row.net_gex for row in result.by_strike}
    assert levels.call_wall_gex == pytest.approx(max(by_strike.values()))
    assert levels.put_wall_gex == pytest.approx(min(by_strike.values()))


def test_walls_cannot_coincide_when_more_than_one_strike_is_present():
    for snapshot in (synthetic_chain(), _wall_chain()):
        levels = E.compute_all(snapshot).levels
        assert levels.call_wall != levels.put_wall


def test_max_abs_strike_is_sign_blind():
    """5000 loses both walls but keeps the most gamma pinned to it."""
    result = E.compute_all(_wall_chain())
    assert result.levels.max_abs_strike == 5000.0
    by_strike = {row.strike: row.abs_gex for row in result.by_strike}
    assert result.levels.max_abs_gex == pytest.approx(max(by_strike.values()))


def test_top_positive_and_negative_are_ranked_by_net_gex(chain):
    levels = E.compute_all(chain, top_n=3).levels
    positives = [row.net_gex for row in levels.top_positive]
    negatives = [row.net_gex for row in levels.top_negative]
    assert positives == sorted(positives, reverse=True)
    assert negatives == sorted(negatives)
    assert all(v > 0 for v in positives)
    assert all(v < 0 for v in negatives)
    assert len(positives) <= 3 and len(negatives) <= 3
    # Nothing with the wrong sign is padded in.
    full = E.compute_all(chain).levels
    all_net = [row.net_gex for row in E.compute_all(chain).by_strike]
    assert len(full.top_positive) == min(5, sum(1 for v in all_net if v > 0))
    assert len(full.top_negative) == min(5, sum(1 for v in all_net if v < 0))


def test_key_levels_on_an_empty_selection_are_all_none():
    """A legitimate case: ZERO_DTE on a day with no 0DTE expiry."""
    chain = synthetic_chain(tuple(s for s in SPECS if s["expiry"] != TODAY))
    result = E.compute_all(chain, E.ExpiryFilter.ZERO_DTE)
    levels = result.levels
    # T100: the aggregates are null for the same reason the levels are -- nothing was in scope.
    assert levels.net_gex is None
    assert levels.call_gex is None
    assert levels.put_gex is None
    assert levels.abs_gex is None
    assert levels.call_wall is None
    assert levels.put_wall is None
    assert levels.max_abs_strike is None
    assert levels.flip_point is None  # not a fabricated level at the spot
    assert result.by_strike == ()
    assert json.dumps(result.to_dict())


# --------------------------------------------------------------------------------------
# Gamma profile and flip point
# --------------------------------------------------------------------------------------


def test_default_profile_grid_is_201_points_over_plus_minus_ten_percent(frame):
    profile = E.gamma_profile(frame, SPOT)
    grid, total = profile  # unpacks as (spot_grid, total_gex), per TASKS.md T08
    assert grid.size == 201
    assert total.size == 201
    assert grid[0] == pytest.approx(0.90 * SPOT)
    assert grid[-1] == pytest.approx(1.10 * SPOT)
    assert grid[100] == pytest.approx(SPOT)
    assert np.diff(grid) == pytest.approx(np.full(200, 0.001 * SPOT))


def test_profile_at_the_actual_spot_equals_net_gex(chain, frame):
    """The profile's midpoint is the same computation the headline number comes from."""
    profile = E.gamma_profile(frame, SPOT)
    assert profile.total_gex[100] == pytest.approx(E.compute_all(chain).net_gex, rel=1e-12)


def test_profile_reprices_gamma_rather_than_rescaling_it(frame):
    """A rescaled profile would be proportional to S^2; a repriced one is not."""
    profile = E.gamma_profile(frame, SPOT)
    grid, total = profile
    rescaled = total[100] * (grid / SPOT) ** 2
    assert total[0] != pytest.approx(rescaled[0], rel=1e-3)


def test_flip_point_interpolates_a_known_crossing():
    grid = np.array([4900.0, 4950.0, 5000.0, 5050.0, 5100.0])
    total = np.array([-2.0, -1.0, 1.0, 2.0, 3.0])
    # Between 4950 (-1) and 5000 (+1): 4950 + 50 * 1/2 = 4975.
    assert E.flip_point((grid, total), 5000.0) == pytest.approx(4975.0)

    # An asymmetric crossing, to prove it is interpolation and not a midpoint.
    total = np.array([-3.0, -3.0, -1.0, 3.0, 3.0])
    # Between 5000 (-1) and 5050 (+3): 5000 + 50 * 1/4 = 5012.5.
    assert E.flip_point((grid, total), 5000.0) == pytest.approx(5012.5)


def test_flip_point_is_none_without_a_sign_change():
    grid = np.array([4900.0, 4950.0, 5000.0, 5050.0, 5100.0])
    assert E.flip_point((grid, np.array([1.0, 2.0, 3.0, 4.0, 5.0])), 5000.0) is None
    assert E.flip_point((grid, np.array([-1.0, -2.0, -3.0, -4.0, -5.0])), 5000.0) is None


def test_flip_point_picks_the_crossing_nearest_to_spot():
    grid = np.array([4900.0, 4950.0, 5000.0, 5050.0, 5100.0])
    total = np.array([1.0, -1.0, -1.0, -1.0, 1.0])
    # Crossings at 4925 and 5075. Spot 5000 is nearer 5075 by 25.
    assert E.flip_point((grid, total), 5010.0) == pytest.approx(5075.0)
    assert E.flip_point((grid, total), 4990.0) == pytest.approx(4925.0)


def test_flip_point_ignores_zeros_that_are_not_sign_changes():
    """Regression: an all-zero or merely zero-touching profile is not a flip.

    A 0DTE profile underflows to exactly 0.0 in both +/-10 % wings — every one-day gamma has
    died by then — and an empty selection is zero everywhere. Reading either as a crossing put
    a fabricated level in the wing (or at the spot itself) onto the dashboard.
    """
    grid = np.array([4900.0, 4950.0, 5000.0, 5050.0, 5100.0])
    assert E.flip_point((grid, np.zeros(5)), 5000.0) is None
    assert E.flip_point((grid, np.array([0.0, 5.0, 9.0, 5.0, 0.0])), 5000.0) is None
    assert E.flip_point((grid, np.array([0.0, -5.0, -9.0, -5.0, 0.0])), 5000.0) is None
    # A zero that genuinely separates the two signs *is* the level.
    assert E.flip_point((grid, np.array([-2.0, -1.0, 0.0, 1.0, 2.0])), 5000.0) == pytest.approx(5000.0)


def test_flip_point_on_degenerate_input():
    assert E.flip_point((np.array([5000.0]), np.array([1.0])), 5000.0) is None
    assert E.flip_point((np.array([]), np.array([])), 5000.0) is None
    assert E.flip_point((np.array([1.0, 2.0]), np.array([np.nan, np.nan])), 1.5) is None


def test_flip_point_defaults_its_reference_to_the_profile_spot(frame):
    profile = E.gamma_profile(frame, SPOT)
    assert E.flip_point(profile) == E.flip_point(profile, SPOT)


# --------------------------------------------------------------------------------------
# compute_all: shape, serialization, frontend contract
# --------------------------------------------------------------------------------------


def test_compute_all_is_json_serializable(chain):
    payload = E.compute_all(chain).to_dict()
    text = json.dumps(payload)
    assert json.loads(text) == payload


def test_compute_all_field_names_match_the_frontend_contract(chain):
    """`frontend/src/api/types.ts` was hand-written ahead of this module; T11 is a
    pass-through only if the names line up."""
    payload = E.compute_all(chain).to_dict()
    assert {"underlying", "filter", "snapshot", "levels", "by_strike", "by_expiry", "profile"} <= set(payload)
    assert {"net_gex", "call_wall", "put_wall", "max_abs_strike", "flip_point", "spot", "computed_at"} <= set(payload["levels"])
    assert {"strike", "call_gex", "put_gex", "net_gex"} <= set(payload["by_strike"][0])
    assert {"expiry", "net_gex", "call_gex", "put_gex"} <= set(payload["by_expiry"][0])
    assert {"spot", "total_gex"} == set(payload["profile"][0])
    assert {"underlying", "captured_at", "source", "delayed_minutes", "spot"} <= set(payload["snapshot"])


def test_compute_all_echoes_snapshot_provenance(chain):
    result = E.compute_all(chain)
    assert result.underlying == "SPX"
    assert result.filter == "ALL"
    assert result.spot == SPOT
    assert result.snapshot.contract_count == 20
    assert result.snapshot.captured_at == chain.captured_at
    assert result.levels.computed_at == chain.captured_at
    assert result.net_gex == result.levels.net_gex
    assert result.flip_point == result.levels.flip_point


def test_compute_all_accepts_a_prebuilt_frame(chain, frame):
    assert E.compute_all(chain, frame=frame).net_gex == pytest.approx(E.compute_all(chain).net_gex)


def test_empty_snapshot_produces_an_empty_result():
    empty = ChainSnapshot(
        underlying=Underlying.SPX, spot=SPOT, captured_at=AS_OF,
        source="synthetic", delayed_minutes=0, contracts=(),
    )
    result = E.compute_all(empty)
    # T100: a snapshot with no contracts measured nothing. `0.0` here would assert a flat book.
    assert result.net_gex is None
    assert result.by_strike == ()
    assert result.by_expiry == ()
    assert result.levels.flip_point is None
    assert len(result.profile) == 201
    assert json.dumps(result.to_dict())


# --------------------------------------------------------------------------------------
# The SPX fixture — plausibility, not proof. See the module docstring.
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spx_fixture_snapshot() -> ChainSnapshot:
    payload = (FIXTURES_DIR / "spx.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def fetch() -> ChainSnapshot:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await CboeProvider(client=client).fetch_chain("SPX")
        finally:
            await client.aclose()

    import asyncio

    return asyncio.run(fetch())


def test_spx_fixture_is_the_trimmed_chain(spx_fixture_snapshot):
    assert len(spx_fixture_snapshot) == 246
    assert set(spx_fixture_snapshot.roots) == {"SPX", "SPXW"}


def test_spx_fixture_aggregates_are_internally_consistent(spx_fixture_snapshot):
    result = E.compute_all(spx_fixture_snapshot)
    levels = result.levels

    assert math.isfinite(levels.net_gex)
    assert levels.call_gex > 0 and levels.put_gex < 0
    assert levels.net_gex == pytest.approx(levels.call_gex + levels.put_gex, rel=1e-12)
    assert sum(r.net_gex for r in result.by_strike) == pytest.approx(levels.net_gex, rel=1e-12)
    assert sum(r.net_gex for r in result.by_expiry) == pytest.approx(levels.net_gex, rel=1e-12)
    assert result.profile[100].total_gex == pytest.approx(levels.net_gex, rel=1e-12)
    assert result.profile[100].spot == pytest.approx(spx_fixture_snapshot.spot)

    # A 246-contract slice around the money still moves tens of billions of dollars of dealer
    # delta per 1 % — the same order as the full chain's per-strike rows, three orders below
    # its 50 B total. Anything outside this band is a units error, not market structure.
    assert 1e9 < abs(levels.net_gex) < 1e12
    assert 1e10 < levels.abs_gex < 1e13


def test_spx_fixture_walls_bracket_spot(spx_fixture_snapshot):
    """The fixture reproduces the collapse in miniature: both per-side extrema sit on 7700."""
    levels = E.compute_all(spx_fixture_snapshot).levels
    assert levels.max_call_gex_strike == levels.max_put_gex_strike  # the trap
    assert levels.call_wall != levels.put_wall  # the fix
    assert levels.put_wall <= levels.spot <= levels.call_wall


def test_spx_fixture_diagnostics_are_reported(spx_fixture_snapshot):
    d = E.compute_all(spx_fixture_snapshot).diagnostics
    assert d.contracts == 246
    assert d.included == d.contracts - d.expired - d.missing_open_interest - d.missing_iv - d.extreme_iv
    assert d.missing_open_interest == 0  # Cboe always publishes OI
    assert d.extreme_iv > 0  # the deep-ITM inversion artifacts are present in the trim
    assert d.iv_max_observed > 3.0
    assert d.net_gex_iv_unfiltered == pytest.approx(
        E.compute_all(spx_fixture_snapshot).net_gex + d.extreme_iv_gex_excluded
    )
    # The IV policy is a rounding error on the total, not a thumb on the scale.
    assert abs(d.extreme_iv_gex_excluded) < 1e-3 * abs(d.net_gex_iv_unfiltered)


def test_spx_fixture_filters_partition_and_contain(spx_fixture_snapshot):
    frame = E.to_frame(spx_fixture_snapshot)
    everything = E.compute_all(spx_fixture_snapshot, frame=frame)
    zero = E.compute_all(spx_fixture_snapshot, "ZERO_DTE", frame=frame)
    ex_zero = E.compute_all(spx_fixture_snapshot, "EX_ZERO_DTE", frame=frame)

    assert zero.net_gex + ex_zero.net_gex == pytest.approx(everything.net_gex, rel=1e-12)
    assert len(zero.expiries) == 1
    assert set(zero.expiries) | set(ex_zero.expiries) == set(everything.expiries)
    assert {r.strike for r in zero.by_strike} <= {r.strike for r in everything.by_strike}


def test_spx_fixture_result_is_json_serializable(spx_fixture_snapshot):
    payload = E.compute_all(spx_fixture_snapshot).to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert len(payload["profile"]) == 201
    assert len(payload["by_strike"]) > 10


def test_compute_all_on_the_spx_fixture_runs_well_under_two_seconds(spx_fixture_snapshot):
    """TASKS.md T08 acceptance. The bar is set for the full 28,650-contract chain, which
    measures ~0.9 s; the 246-contract fixture is ~0.03 s, so a failure here means an
    accidental Python-level loop over contracts or grid points, not a slow machine."""
    start = time.perf_counter()
    E.compute_all(spx_fixture_snapshot)
    assert time.perf_counter() - start < 2.0


# --------------------------------------------------------------------------------------
# T99: a wall must be measurably there, not merely the extreme of the array.
# --------------------------------------------------------------------------------------


def _levels_from(rows):
    """`key_levels` over `(strike, call_gex, put_gex)` triples."""
    frame = pd.DataFrame(
        [
            {
                "strike": k,
                "call_gex": c,
                "put_gex": p,
                "net_gex": c + p,
                "abs_gex": abs(c) + abs(p),
            }
            for k, c, p in rows
        ]
    )
    return E.key_levels(frame, spot=100.0)


#: The three live `ZERO_DTE` rows from 2026-09-21 that motivated the floor: each reports a put
#: wall whose net gamma is numerical residue, because `argmin` over an all-positive array
#: returns the *least positive* strike. `(book max net, residue net)`.
_T99_RESIDUE = [
    pytest.param(1.79e9, -1.87, id="minus-1.87-dollars"),
    pytest.param(2.00e8, -5.48e-37, id="minus-5.48e-37"),
    pytest.param(4.35e6, -2.17e-20, id="minus-2.17e-20"),
]


@pytest.mark.parametrize(("book_max", "residue"), _T99_RESIDUE)
def test_t99_residual_put_wall_is_not_a_wall(book_max, residue):
    """A sign check passes all three of these -- they really are negative. Only a magnitude
    test rejects them, which is why `WALL_MIN_ABS_FRACTION` is not a sign guard."""
    levels = _levels_from([(95.0, 0.0, residue), (105.0, book_max, 0.0)])
    assert levels.put_wall is None
    assert levels.put_wall_gex is None
    # The raw extremum is still reported under its unambiguous name -- it is not a wall claim.
    assert levels.min_net_strike == 95.0
    assert levels.call_wall == 105.0


def test_t99_small_but_real_wall_survives():
    """The floor rejects residue, not small walls. A $44.8mn wall opposite a large 0DTE call
    side is the real case a 1% floor would have destroyed; at 1e-4 it survives."""
    levels = _levels_from([(95.0, 0.0, -44.8e6), (105.0, 20.0e9, 0.0)])
    assert levels.put_wall == 95.0
    assert levels.put_wall_gex == pytest.approx(-44.8e6)


def test_t99_full_chain_walls_are_untouched():
    """688 stored ALL/EX_ZERO_DTE rows have a weakest wall at 3.88% of their book maximum, a
    388x margin over the floor. A normal chain must not lose a level."""
    levels = _levels_from(
        [(95.0, 0.0, -425e6), (100.0, 50e6, -40e6), (105.0, 662e6, 0.0)]
    )
    assert levels.call_wall == 105.0
    assert levels.put_wall == 95.0


def test_t99_all_positive_book_has_no_put_wall():
    """The general form of the bug: with no negative strike at all there is no put wall, and
    saying so is the honest answer. `argmin` would have named the least positive one."""
    levels = _levels_from([(95.0, 1.0e6, 0.0), (105.0, 8.0e9, 0.0)])
    assert levels.put_wall is None
    assert levels.call_wall == 105.0


def test_t99_all_negative_book_has_no_call_wall():
    """The mirror, which had the same latent defect: `argmax` over an all-negative array
    returns the least negative strike and would have called it a call wall."""
    levels = _levels_from([(95.0, 0.0, -8.0e9), (105.0, 0.0, -1.0e6)])
    assert levels.call_wall is None
    assert levels.put_wall == 95.0


# --------------------------------------------------------------------------------------
# T100: an empty aggregate is null, not zero.
# --------------------------------------------------------------------------------------


def test_t100_unmeasurable_chain_reports_null_not_flat():
    """Snapshots 204 (SPX) and 206 (QQQ) on 2026-09-21: first capture of the session, full
    chains of 29,518 and 10,560 contracts, and **zero** admitted strikes -- at 09:43 ET the
    provider had not yet published prior-session open interest, so every contract was
    correctly excluded. The old code summed that empty set to 0.0 and a reader saw
    "dealers are gamma-flat", which is a strong claim about a book nobody measured.
    """
    levels = _levels_from([])
    assert levels.net_gex is None
    assert levels.call_gex is None
    assert levels.put_gex is None
    assert levels.abs_gex is None
    # The pre-existing rule -- levels are null when nothing is in scope -- is unchanged.
    assert levels.call_wall is None
    assert levels.put_wall is None
    assert levels.max_abs_strike is None


def test_t100_exclusion_logic_is_untouched():
    """The open-interest rule is right and is not what was broken. Summing the *result* of a
    correct exclusion was the defect, never the filter itself."""
    levels = _levels_from([(95.0, 0.0, -6.0e9), (105.0, 8.0e9, 0.0)])
    assert levels.net_gex == pytest.approx(2.0e9)
    assert levels.abs_gex == pytest.approx(14.0e9)


def test_t100_a_genuinely_flat_book_still_reports_zero():
    """The distinction the whole change exists to preserve: a book that *was* measured and
    nets to zero must still say zero. Null and zero are different facts and both are real."""
    levels = _levels_from([(95.0, 5.0e9, -5.0e9)])
    assert levels.net_gex is not None
    assert levels.net_gex == pytest.approx(0.0)
    assert levels.abs_gex == pytest.approx(10.0e9)


def test_t100_empty_zero_dte_and_unmeasurable_chain_are_the_same_shape():
    """Deliberate, and worth pinning: `ZERO_DTE` legitimately admitting nothing after the
    close, and a full chain none of whose contracts were usable, both resolve to null. That is
    strictly better than both resolving to 0, and it is still not a *distinction*. Separating
    "no contracts in this bucket" from "contracts existed but none were usable" needs a reason
    field the capture path does not record yet -- see plans/desk-integrity/02-expiry-and-session.md.
    """
    assert _levels_from([]).net_gex is None


# --------------------------------------------------------------------------------------
# T101: the expiry dimension. Horizons partition a strike exactly.
# --------------------------------------------------------------------------------------


def test_t101_horizons_partition_every_strike_exactly(chain):
    """The load-bearing property. If the four horizons do not sum to `net_gex`, every
    downstream "how much expires Friday" answer is wrong by the gap, silently."""
    result = E.compute_all(chain)
    assert result.by_strike
    for row in result.by_strike:
        parts = [
            row.net_gex_0dte,
            row.net_gex_this_week,
            row.net_gex_next_30d,
            row.net_gex_beyond_30d,
        ]
        assert all(p is not None for p in parts), f"strike {row.strike} has an unfilled horizon"
        assert sum(parts) == pytest.approx(row.net_gex, abs=1e-6), (
            f"strike {row.strike}: horizons sum to {sum(parts)}, net_gex is {row.net_gex}"
        )


def test_t101_horizons_are_disjoint(chain):
    """Exhaustive *and* disjoint: a contract counted in two buckets would still sum correctly
    at some strikes, so summing alone does not prove the partition."""
    frame = E.to_frame(chain)
    zero = frame["zero_dte"].to_numpy(dtype=bool)
    week = frame["this_week"].to_numpy(dtype=bool)
    dte = frame["dte"].to_numpy(dtype=float)
    rest = ~zero & ~week
    masks = [zero, week & ~zero, rest & (dte <= 30), rest & (dte > 30)]
    counts = sum(m.astype(int) for m in masks)
    assert (counts == 1).all(), "every contract must land in exactly one horizon"


def test_t101_zero_dte_filter_puts_everything_in_the_0dte_horizon(chain):
    """Self-consistency between the filter and the decomposition: under ZERO_DTE every
    admitted contract expires today, so the other three horizons must be empty."""
    result = E.compute_all(chain, E.ExpiryFilter.ZERO_DTE)
    for row in result.by_strike:
        assert row.net_gex_0dte == pytest.approx(row.net_gex)
        assert row.net_gex_this_week == pytest.approx(0.0)
        assert row.net_gex_next_30d == pytest.approx(0.0)
        assert row.net_gex_beyond_30d == pytest.approx(0.0)


def test_t101_the_wall_question_is_answerable(chain):
    """F5's headline: "how much of this wall expires Friday" -- the first thing anyone asks
    about a wall, and unanswerable from storage before T101."""
    result = E.compute_all(chain)
    wall = result.levels.call_wall or result.levels.put_wall
    assert wall is not None
    row = next(r for r in result.by_strike if r.strike == wall)
    surviving = row.net_gex_next_30d + row.net_gex_beyond_30d
    expiring_this_week = row.net_gex_0dte + row.net_gex_this_week
    assert surviving + expiring_this_week == pytest.approx(row.net_gex, abs=1e-6)


def test_t101_horizons_absent_from_an_older_frame_are_null_not_zero(chain):
    """A Parquet round-trip of a pre-T101 capture yields a frame with no horizon columns.
    `_row` must report that as unknown, not as a strike with no near-dated gamma (T100's rule,
    applied to T101's columns)."""
    strikes = E.by_strike(E.to_frame(chain), spot=SPOT)
    legacy = strikes.drop(columns=[f"net_gex_{h}" for h in E.STRIKE_HORIZONS])
    row = E._row(legacy.iloc[0])
    assert row.net_gex_0dte is None
    assert row.net_gex_beyond_30d is None
    assert row.net_gex is not None
