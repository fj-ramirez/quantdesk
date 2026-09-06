"""Tests for `app/gex/report.py` (T39).

Three layers, deliberately:

1. **Hand-computed arithmetic** on a tiny synthetic chain where the expected max pain,
   put/call ratios and ATM IV are worked out in the test's own comments and can be checked
   with a pencil. These are the tests that would catch a sign or weighting error.
2. **The live measured aggregates** for GLD, DIA and SPY, taken from `docs/validation.md` §9
   and the T39 supervisor measurement, driven straight through `dealer_positioning`. This is
   where the "DIA must read noise-dominated" requirement is pinned — see
   `test_positioning_dia_live_aggregates` for why it is pinned here rather than through a
   trimmed fixture.
3. **End-to-end** over the committed GLD/DIA Cboe fixtures, which exercises the real
   provider → `to_frame` → `compute_all` → `build_report` path offline.

Everything here runs with no network and no database.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.gex import report as R
from app.gex.engine import ExpiryFilter, compute_all, to_frame
from app.models.chain import ChainSnapshot, OptionContract, Underlying
from app.providers.cboe import CboeProvider

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cboe"

# A Thursday, 16:20 New York = 20:20 UTC, so nothing below is expired and the DTE arithmetic
# is unambiguous.
AS_OF = dt.datetime(2026, 9, 3, 20, 20, tzinfo=dt.UTC)
SPOT = 100.0


def _occ(expiry: dt.date, right: str, strike: float) -> str:
    # Root "SPY" because `underlying_for_root` only accepts roots the schema knows; the
    # synthetic strikes below are around 100, not a real SPY level, which is irrelevant to the
    # arithmetic under test and keeps the hand-computed numbers readable.
    return f"SPY{expiry:%y%m%d}{right}{round(strike * 1000):08d}"


def _contract(
    expiry: dt.date,
    right: str,
    strike: float,
    *,
    oi: int | None,
    iv: float | None = 0.20,
    volume: int | None = 0,
    bid: float | None = None,
    ask: float | None = None,
    multiplier: int = 100,
) -> OptionContract:
    return OptionContract.from_occ(
        _occ(expiry, right, strike),
        open_interest=oi,
        iv=iv,
        volume=volume,
        bid=bid,
        ask=ask,
        gamma=0.001,
        multiplier=multiplier,
    )


def _chain(contracts: tuple[OptionContract, ...], spot: float = SPOT) -> ChainSnapshot:
    return ChainSnapshot(
        underlying=Underlying.SPY,
        spot=spot,
        captured_at=AS_OF,
        source="synthetic",
        delayed_minutes=0,
        contracts=contracts,
    )


# --------------------------------------------------------------------------------------
# 1. Hand-computed arithmetic
# --------------------------------------------------------------------------------------

# One expiry, three strikes, open interest chosen so the max-pain minimum is unambiguous and
# checkable by hand:
#
#   strike 95  call OI 10, put OI  0
#   strike 100 call OI 10, put OI 10
#   strike 105 call OI  0, put OI 30
#
# Total intrinsic (x100 multiplier), if the underlying settled at each strike:
#   at  95: calls 0                       puts (100-95)*10 + (105-95)*30 = 50 + 300 = 350
#   at 100: calls (100-95)*10 = 50        puts (105-100)*30 = 150             -> 200
#   at 105: calls (105-95)*10 + (105-100)*10 = 100 + 50 = 150   puts 0        -> 150
# so max pain is 105, at 150 * 100 = 15,000 dollars.
NEAR = dt.date(2026, 10, 2)

_MAX_PAIN_CHAIN = (
    _contract(NEAR, "C", 95.0, oi=10),
    _contract(NEAR, "P", 95.0, oi=0),
    _contract(NEAR, "C", 100.0, oi=10),
    _contract(NEAR, "P", 100.0, oi=10),
    _contract(NEAR, "C", 105.0, oi=0),
    _contract(NEAR, "P", 105.0, oi=30),
)


def test_max_pain_matches_hand_computed_minimum():
    frame = to_frame(_chain(_MAX_PAIN_CHAIN))
    pain = R.max_pain(frame, SPOT)

    assert pain.strike == 105.0
    assert pain.total_pain == pytest.approx(15_000.0)
    assert pain.strikes_evaluated == 3
    assert pain.contracts == 6
    assert pain.open_interest == 60
    assert pain.distance == pytest.approx(5.0)
    assert pain.distance_pct == pytest.approx(5.0)


def test_max_pain_honours_the_per_contract_multiplier():
    """An adjusted contract must not be priced as if it were a standard 100-multiplier one.

    Same strikes as above, but the 95 call carries a 1000 multiplier, which is enough to make
    settling at 105 (where that call is deep in the money) the *expensive* outcome rather than
    the cheap one, moving max pain down to 95.
    """
    contracts = (
        _contract(NEAR, "C", 95.0, oi=10, multiplier=1000),
        _contract(NEAR, "P", 95.0, oi=0),
        _contract(NEAR, "C", 100.0, oi=10),
        _contract(NEAR, "P", 100.0, oi=10),
        _contract(NEAR, "C", 105.0, oi=0),
        _contract(NEAR, "P", 105.0, oi=30),
    )
    assert R.max_pain(to_frame(_chain(contracts)), SPOT).strike == 95.0


def test_max_pain_excludes_unknown_open_interest_but_keeps_zero():
    """`None` open interest is unknown and drops out; `0` is genuine and stays in the count.

    Collapsing the two is the codebase-wide bug this rule exists to prevent (CLAUDE.md
    invariant 3). The 105 put's 30 lots are what pull max pain to 105 above; making that
    contract's OI unknown must change the answer, and the contract count must fall by one.
    """
    contracts = (
        _contract(NEAR, "C", 95.0, oi=10),
        _contract(NEAR, "P", 95.0, oi=0),
        _contract(NEAR, "C", 100.0, oi=10),
        _contract(NEAR, "P", 100.0, oi=10),
        _contract(NEAR, "C", 105.0, oi=0),
        _contract(NEAR, "P", 105.0, oi=None),
    )
    pain = R.max_pain(to_frame(_chain(contracts)), SPOT)
    assert pain.contracts == 5  # the unknown one is gone, the two zeros are still here
    assert pain.strike == 95.0


def test_max_pain_is_all_none_when_nothing_is_open():
    pain = R.max_pain(to_frame(_chain(())), SPOT)
    assert (pain.strike, pain.total_pain, pain.distance) == (None, None, None)
    assert pain.contracts == 0


def test_put_call_ratios_are_puts_over_calls():
    """Orientation, hand-checked. The example report inverted exactly this ratio on GLD.

    Calls: OI 10 + 10 + 0 = 20. Puts: OI 0 + 10 + 30 = 40. So the OI ratio is 40/20 = 2.0,
    which is *bearish*-leaning — and would be 0.5 if the ratio were built upside down.
    """
    frame = to_frame(_chain(_MAX_PAIN_CHAIN))
    ratios = R.put_call_ratios(frame)

    assert ratios.call_open_interest == 20
    assert ratios.put_open_interest == 40
    assert ratios.total_open_interest == 60
    assert ratios.open_interest_ratio == pytest.approx(2.0)
    assert ratios.call_contracts == 3
    assert ratios.put_contracts == 3


def test_put_call_volume_ratio_and_unknown_accounting():
    """Volume and open interest are counted over separate populations.

    The 100 call has known OI (10) but unknown volume, so it contributes to the OI totals and
    not the volume ones, and shows up in `missing_volume` — it is not dropped from both.
    """
    contracts = (
        _contract(NEAR, "C", 95.0, oi=10, volume=100),
        _contract(NEAR, "C", 100.0, oi=10, volume=None),
        _contract(NEAR, "P", 100.0, oi=10, volume=300),
    )
    ratios = R.put_call_ratios(to_frame(_chain(contracts)))

    assert ratios.call_volume == 100
    assert ratios.put_volume == 300
    assert ratios.volume_ratio == pytest.approx(3.0)
    assert ratios.missing_volume == 1
    assert ratios.missing_open_interest == 0
    assert ratios.call_open_interest == 20  # the unknown-volume call still counts here


def test_put_call_ratio_is_none_not_infinity_when_there_are_no_calls():
    contracts = (_contract(NEAR, "P", 100.0, oi=10, volume=5),)
    ratios = R.put_call_ratios(to_frame(_chain(contracts)))
    assert ratios.open_interest_ratio is None
    assert ratios.volume_ratio is None


def test_atm_iv_interpolates_between_the_bracketing_expiries():
    """Hand-checked constant-maturity interpolation, in total variance.

    Two expiries bracketing 30 DTE, both with a flat ATM smile so the strike interpolation is
    a no-op and only the maturity interpolation is under test:

      2026-09-24 -> 21 DTE, IV 0.20
      2026-10-08 -> 35 DTE, IV 0.30

    `t` comes from the frame (PM settlement at 16:00 New York), so it is very slightly under
    dte/365. Interpolating variance linearly in `t` and backing out the vol at 30/365 must
    land strictly between 0.20 and 0.30, and above the straight-line-in-vol answer of 0.2643
    is not required — what *is* required is that it is a variance interpolation, which we
    check by recomputing the same formula here from the reported bracket.
    """
    lower, upper = dt.date(2026, 9, 24), dt.date(2026, 10, 8)
    contracts = tuple(
        _contract(expiry, right, strike, oi=10, iv=iv)
        for expiry, iv in ((lower, 0.20), (upper, 0.30))
        for right in ("C", "P")
        for strike in (98.0, 100.0, 102.0)
    )
    frame = to_frame(_chain(contracts))
    regime = R.iv_regime(frame, SPOT)

    assert regime.interpolated is True
    assert (regime.lower_dte, regime.upper_dte) == (21, 35)
    assert regime.target_dte == 30
    assert 0.20 < regime.atm_iv < 0.30

    # Recompute the documented formula from the frame's own year fractions.
    t_lo = float(frame.loc[frame["dte"] == 21, "t"].mean())
    t_hi = float(frame.loc[frame["dte"] == 35, "t"].mean())
    target_t = 30 / 365.0
    w_lo, w_hi = 0.20**2 * t_lo, 0.30**2 * t_hi
    weight = (target_t - t_lo) / (t_hi - t_lo)
    expected = ((w_lo + weight * (w_hi - w_lo)) / target_t) ** 0.5
    assert regime.atm_iv == pytest.approx(expected)


def test_atm_iv_interpolates_across_strike_to_spot():
    """Within one expiry the ATM vol is read at spot, not averaged over the window.

    Spot 100 sits midway between strikes 98 (IV 0.20) and 102 (IV 0.24), so the ATM vol is
    0.22. A plain mean over the window would give the same answer here only by symmetry, so
    the 104 strike is added with a much higher vol to break the tie: a mean would be dragged
    to 0.2467, interpolation at spot stays at 0.22.
    """
    expiry = dt.date(2026, 10, 2)  # 29 DTE, the only expiry -> no maturity interpolation
    contracts = (
        _contract(expiry, "C", 98.0, oi=10, iv=0.20),
        _contract(expiry, "C", 100.0, oi=10, iv=0.22),
        _contract(expiry, "C", 102.0, oi=10, iv=0.24),
        _contract(expiry, "C", 104.0, oi=10, iv=0.40),
    )
    regime = R.iv_regime(to_frame(_chain(contracts)), SPOT)

    assert regime.atm_iv == pytest.approx(0.22)
    assert regime.interpolated is False
    assert (regime.lower_dte, regime.upper_dte) == (29, 29)


def test_atm_iv_does_not_extrapolate_past_the_term_structure():
    """With only far-dated expiries, the nearest one is used verbatim and flagged as such."""
    far = dt.date(2027, 6, 18)
    contracts = tuple(
        _contract(far, "C", strike, oi=10, iv=0.25) for strike in (98.0, 100.0, 102.0)
    )
    regime = R.iv_regime(to_frame(_chain(contracts)), SPOT)
    assert regime.atm_iv == pytest.approx(0.25)
    assert regime.interpolated is False


def test_atm_iv_is_none_when_no_contract_is_near_the_money():
    contracts = (_contract(NEAR, "C", 300.0, oi=10, iv=0.25),)
    regime = R.iv_regime(to_frame(_chain(contracts)), SPOT)
    assert regime.atm_iv is None
    assert regime.label is None


# --------------------------------------------------------------------------------------
# The IV regime label — the honesty rule
# --------------------------------------------------------------------------------------


def test_iv_regime_label_is_none_without_history():
    """T39's acceptance criterion: the label is `None` on a single-snapshot database.

    This is the state of every deployment today — nothing persists past ATM IVs — so the
    label must be `None` and the UI must say "insufficient history" rather than "NORMAL".
    """
    frame = to_frame(_chain(_MAX_PAIN_CHAIN))
    regime = R.iv_regime(frame, SPOT)

    assert regime.atm_iv is not None, "the number is always reported"
    assert regime.label is None, "the label is not"
    assert regime.history_observations == 0
    assert regime.min_history_required == R.MIN_IV_HISTORY


def test_iv_regime_label_stays_none_just_below_the_history_floor():
    frame = to_frame(_chain(_MAX_PAIN_CHAIN))
    history = [0.20] * (R.MIN_IV_HISTORY - 1)
    regime = R.iv_regime(frame, SPOT, iv_history=history)
    assert regime.label is None
    assert regime.history_observations == R.MIN_IV_HISTORY - 1


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        # Current ATM IV on this chain is 0.20. Against 20 observations all above it, it sits
        # at the 0th percentile -> LOW; all below -> HIGH; evenly split -> NORMAL.
        ([0.50] * R.MIN_IV_HISTORY, "LOW"),
        ([0.05] * R.MIN_IV_HISTORY, "HIGH"),
        ([0.05] * 10 + [0.50] * 10, "NORMAL"),
    ],
)
def test_iv_regime_label_appears_once_there_is_enough_history(history, expected):
    frame = to_frame(_chain(_MAX_PAIN_CHAIN))
    regime = R.iv_regime(frame, SPOT, iv_history=history)
    assert regime.label == expected
    assert regime.history_observations == R.MIN_IV_HISTORY


# --------------------------------------------------------------------------------------
# 2. Dealer positioning, against the live measured aggregates
# --------------------------------------------------------------------------------------


def test_positioning_dia_live_aggregates_are_noise_dominated():
    """DIA must not be given a direction. T39's acceptance criterion.

    The numbers are DIA's **actual measured** net and gross GEX from the 2026-09-05 live
    chain (`docs/validation.md` §9), not a fixture's. That is deliberate. DIA's
    noise-dominance is a property of the *whole* chain nearly cancelling — its net is 0.9 % of
    its gross — and any trimmed fixture destroys it: subsampling the real chain's strikes
    every 2nd/3rd/4th/5th/6th gives net/gross ratios of 19.1 %, 9.1 %, 28.4 %, 1.2 % and
    17.4 % respectively, scattered either side of the floor. Committing whichever trim
    happened to land under 3 % would be selecting a fixture *because* it produces the
    expected answer, which is not evidence of anything. The measured aggregates are.

    §9's finding is what makes this required rather than conservative: DIA's net GEX changes
    sign outright under a plausible carry correction (`q = 0.013` -> `q = 0`), so a report
    calling DIA "short gamma" would assert something the validation document says we cannot
    support.
    """
    positioning = R.dealer_positioning(-8_824_612.39, 1_037_320_166.19)

    assert positioning.ratio == pytest.approx(0.0085, abs=5e-4)
    assert positioning.noise_dominated is True
    assert positioning.label == "NOISE-DOMINATED"
    assert positioning.direction is None, "no direction may be reported for DIA"
    assert "carry" in positioning.description


def test_positioning_gld_live_aggregates_report_a_direction():
    """GLD's net is 42.7 % of gross — comfortably clear of the floor, and §9 measured its sign
    surviving the same carry stress that flips DIA's."""
    positioning = R.dealer_positioning(2_265_403_983.0, 5_302_504_441.0)

    assert positioning.ratio == pytest.approx(0.427, abs=1e-3)
    assert positioning.noise_dominated is False
    assert positioning.direction == "LONG"
    assert positioning.label == "LONG GAMMA"


def test_positioning_spy_live_aggregates_clear_the_floor():
    """SPY sits at 4.8 %: above the 3 % floor, so a direction is reported.

    Recorded explicitly because it is the closest of the measured symbols to the boundary and
    is therefore the one a future change to `POSITIONING_RATIO_FLOOR` would flip first.
    """
    positioning = R.dealer_positioning(-1_845_200_000.0, 38_662_500_000.0)

    assert positioning.ratio == pytest.approx(0.0477, abs=5e-4)
    assert positioning.noise_dominated is False
    assert positioning.direction == "SHORT"


def test_positioning_floor_boundary_is_inclusive_upward():
    """Exactly at the floor counts as clearing it; a hair below does not."""
    at_floor = R.dealer_positioning(3.0, 100.0, ratio_floor=0.03)
    below = R.dealer_positioning(2.99, 100.0, ratio_floor=0.03)
    assert at_floor.noise_dominated is False
    assert below.noise_dominated is True


def test_positioning_with_no_gross_reports_no_data():
    positioning = R.dealer_positioning(0.0, 0.0)
    assert positioning.label == "NO DATA"
    assert positioning.direction is None
    assert positioning.noise_dominated is True


# --------------------------------------------------------------------------------------
# Support / resistance ordering
# --------------------------------------------------------------------------------------


def _fixture_snapshot(symbol: str, filename: str) -> ChainSnapshot:
    """Parse a committed Cboe fixture through the real provider. No network."""
    payload = (FIXTURES_DIR / filename).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def fetch() -> ChainSnapshot:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await CboeProvider(client=client).fetch_chain(symbol)
        finally:
            await client.aclose()

    return asyncio.run(fetch())


@pytest.fixture(scope="module")
def gld_report() -> Any:
    snapshot = _fixture_snapshot("GLD", "gld.json")
    frame = to_frame(snapshot)
    result = compute_all(snapshot, ExpiryFilter.ALL, frame=frame)
    return result, R.build_report(result, frame, ExpiryFilter.ALL)


@pytest.fixture(scope="module")
def dia_report() -> Any:
    snapshot = _fixture_snapshot("DIA", "dia.json")
    frame = to_frame(snapshot)
    result = compute_all(snapshot, ExpiryFilter.ALL, frame=frame)
    return result, R.build_report(result, frame, ExpiryFilter.ALL)


def test_resistance_never_sorts_below_support(gld_report, dia_report):
    """The invariant the example report broke: it listed 407 as support and 405 as resistance.

    Checked on both fixtures, including DIA, whose chain genuinely has negative-gamma strikes
    sitting above spot.
    """
    for _result, report in (gld_report, dia_report):
        levels = report.levels
        if levels.resistance and levels.support:
            assert min(level.strike for level in levels.resistance) > max(
                level.strike for level in levels.support
            )
        assert all(level.strike > report.spot for level in levels.resistance)
        assert all(level.strike < report.spot for level in levels.support)


def test_straddling_strikes_are_labelled_not_reordered(dia_report):
    """DIA's chain concentrates gamma on both sides of spot; that must be said out loud.

    On the committed fixture, negative-net strikes sit *above* the 532.34 spot. They belong in
    neither list, so they land in `straddling` with `overlapping` set and a note explaining
    the condition — rather than being silently sorted into "support" below a lower
    "resistance", which is what the example report did.
    """
    _result, report = dia_report
    levels = report.levels

    assert levels.straddling, "the DIA fixture is expected to straddle spot"
    assert levels.overlapping is True
    assert levels.overlap_note is not None
    assert all(level.side == "STRADDLING" for level in levels.straddling)
    # Every straddling strike really is on the wrong side for its sign.
    for level in levels.straddling:
        assert (level.net_gex > 0) == (level.strike < report.spot)

    codes = {alert.code for alert in report.alerts}
    assert "LEVELS_OVERLAP" in codes


def test_levels_are_ordered_nearest_to_spot_first(gld_report):
    _result, report = gld_report
    resistance = [level.strike for level in report.levels.resistance]
    support = [level.strike for level in report.levels.support]
    assert resistance == sorted(resistance)
    assert support == sorted(support, reverse=True)


# --------------------------------------------------------------------------------------
# Premium selling — screening output
# --------------------------------------------------------------------------------------


def test_premium_candidates_are_otm_and_beyond_the_wall(dia_report):
    """Both conditions, not just the wall.

    DIA's put wall (533) sits *above* its 532.34 spot on this fixture, so screening on the
    wall alone would return near-ATM puts and present them as premium selling. Every returned
    put must be at or below spot as well as at or below the wall.
    """
    result, report = dia_report
    premium = report.premium

    for candidate in premium.calls:
        assert candidate.strike >= result.levels.call_wall
        assert candidate.strike >= report.spot
        assert premium.dte_min <= candidate.dte <= premium.dte_max
        assert candidate.mid is not None and candidate.mid > 0
    for candidate in premium.puts:
        assert candidate.strike <= result.levels.put_wall
        assert candidate.strike <= report.spot
        assert premium.dte_min <= candidate.dte <= premium.dte_max


def test_premium_mid_requires_both_sides_quoted():
    """A one-sided market has no mid, and a contract with no bid cannot be sold at all."""
    expiry = dt.date(2026, 9, 24)  # 21 DTE, inside the default window
    contracts = (
        _contract(expiry, "C", 110.0, oi=10, bid=1.0, ask=1.4),  # quoted both sides
        _contract(expiry, "C", 115.0, oi=10, bid=0.5, ask=None),  # no ask -> no mid
        _contract(expiry, "C", 120.0, oi=10, bid=None, ask=0.2),  # no bid -> unsellable
        _contract(expiry, "C", 125.0, oi=10, bid=0.0, ask=0.2),  # zero bid -> unsellable
    )
    snapshot = _chain(contracts)
    frame = to_frame(snapshot)
    result = compute_all(snapshot, ExpiryFilter.ALL, frame=frame)
    premium = R.premium_selling(frame, result)

    assert [c.strike for c in premium.calls] == [110.0]
    assert premium.calls[0].mid == pytest.approx(1.2)


def test_premium_screen_reports_an_empty_side_rather_than_widening(gld_report):
    """An empty side is an answer. The screen must not move its own boundary to fill it."""
    _result, report = gld_report
    premium = report.premium
    if not premium.calls or not premium.puts:
        assert premium.note, "an empty side must be explained"


# --------------------------------------------------------------------------------------
# Playbook and alerts — every number traceable
# --------------------------------------------------------------------------------------


def test_playbook_numbers_are_all_computed_levels(gld_report):
    """No trigger, target or invalidation may be a number that appears nowhere else."""
    result, report = gld_report
    known = {row.strike for row in result.by_strike}
    known |= {
        value
        for value in (
            result.levels.call_wall,
            result.levels.put_wall,
            result.levels.flip_point,
            report.max_pain.strike,
        )
        if value is not None
    }

    for entry in report.playbook.entries:
        for value in (entry.trigger, entry.target, entry.invalidation):
            assert value is None or value in known, f"{entry.key}: {value} is not a computed level"


def test_playbook_range_only_exists_when_spot_is_inside_it(dia_report):
    """DIA's put wall is above spot on this fixture, so there is no range to report."""
    _result, report = dia_report
    assert report.playbook.spot_in_range is False
    assert report.playbook.range_low is None
    assert report.playbook.range_high is None
    assert not any(entry.key == "RANGE_BOUND" for entry in report.playbook.entries)


def test_empty_filter_scope_reports_an_empty_state_not_an_error():
    """`ZERO_DTE` on an end-of-day capture admits nothing. That is the normal case (T37)."""
    snapshot = _fixture_snapshot("GLD", "gld.json")
    frame = to_frame(snapshot)
    result = compute_all(snapshot, ExpiryFilter.ZERO_DTE, frame=frame)
    report = R.build_report(result, frame, ExpiryFilter.ZERO_DTE)

    assert result.by_strike == ()
    assert report.max_pain.strike is None
    assert report.levels.resistance == ()
    assert report.positioning.label == "NO DATA"
    assert {alert.code for alert in report.alerts} == {"EMPTY_SCOPE"}
    # And it still renders, rather than raising.
    assert "GLD" in R.render_text(report)


# --------------------------------------------------------------------------------------
# 3. End-to-end shape, determinism and rendering
# --------------------------------------------------------------------------------------


def test_report_matches_the_fixture_chains_own_aggregates(gld_report):
    """The report's positioning restates the GexResult beside it, rather than recomputing."""
    result, report = gld_report
    assert report.positioning.net_gex == pytest.approx(result.levels.net_gex)
    assert report.positioning.abs_gex == pytest.approx(result.levels.abs_gex)
    assert report.spot == pytest.approx(result.spot)
    assert report.levels.call_wall == result.levels.call_wall
    assert report.levels.put_wall == result.levels.put_wall


def test_report_reads_no_clock(gld_report):
    """`generated_at` is the snapshot's own instant, so an old snapshot reproduces exactly."""
    result, report = gld_report
    assert report.generated_at == result.snapshot.captured_at


def test_report_is_deterministic(gld_report):
    _result, first = gld_report
    snapshot = _fixture_snapshot("GLD", "gld.json")
    frame = to_frame(snapshot)
    again = R.build_report(compute_all(snapshot, ExpiryFilter.ALL, frame=frame), frame, ExpiryFilter.ALL)
    assert again.to_dict() == first.to_dict()


def test_to_dict_is_json_serializable(gld_report):
    import json

    _result, report = gld_report
    text = json.dumps(report.to_dict())
    assert '"underlying": "GLD"' in text


#: Everything `app/gex/report.py` is allowed to import. Deliberately a hard allowlist rather
#: than a denylist of known-bad modules: a denylist silently permits the next I/O library
#: nobody thought to ban, which is exactly how a "pure" module stops being one.
_ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "math",
    "numpy",
    "pandas",
    "typing",
    "app.gex.engine",
}

#: Call targets that would make the module non-deterministic or touch the outside world.
_FORBIDDEN_CALLS = {"open", "print", "input", "now", "today", "time", "getLogger"}


def test_report_module_performs_no_io():
    """T39's first acceptance criterion: `report.py` is import-clean of I/O.

    Parsed rather than grepped. An earlier version of this test searched the raw source for
    substrings and tripped over the phrase "datetime.now()" inside a *comment* explaining why
    the module never calls it — a false positive that would have trained the next reader to
    weaken the test. The AST sees only real imports and real call targets, so prose is free to
    discuss what the code must not do.
    """
    import ast

    tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert imported <= _ALLOWED_IMPORTS, (
        f"app/gex/report.py must stay pure; unexpected imports: {sorted(imported - _ALLOWED_IMPORTS)}"
    )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = (
            target.id
            if isinstance(target, ast.Name)
            else target.attr
            if isinstance(target, ast.Attribute)
            else None
        )
        assert name not in _FORBIDDEN_CALLS, f"app/gex/report.py must stay pure; calls {name}()"


def test_render_text_snapshot(gld_report):
    """Snapshot test of the rendered report's structure.

    Pins the section order (which follows `context/example-report.md`'s layout), the presence
    of the honesty strings, and the header figures. Deliberately structural rather than a
    byte-for-byte golden file: the fixture chain is real market data, and a full golden would
    have to be regenerated for any formatting change while catching nothing this does not.
    """
    _result, report = gld_report
    text = R.render_text(report)
    lines = text.splitlines()

    sections = [
        "GLD OPTIONS INTELLIGENCE",
        "DEALER POSITIONING",
        "GAMMA EXPOSURE LANDSCAPE",
        "MARKET SENTIMENT",
        "PREMIUM SELLING SCREEN",
        "PLAYBOOK",
        "RISK ALERTS",
        "EXECUTIVE SUMMARY",
    ]
    positions = [next(i for i, line in enumerate(lines) if section in line) for section in sections]
    assert positions == sorted(positions), "sections must appear in the documented order"

    assert "Current price:   406.77" in text
    assert f"Max pain:        {report.max_pain.strike:g}" in text
    assert "IV regime:       insufficient history (0 of 20 prior observations needed)" in text
    assert "Screening output computed from the current chain, not a recommendation." in text
    assert "no order is ever routed" in text
    # Nothing in the rendered report may claim a regime band it does not have.
    assert "NORMAL VOLATILITY" not in text
    assert text.endswith("\n")



# --------------------------------------------------------------------------------------
# CFD translation (T41) — the report re-expressed in the instrument the user actually trades
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def gld_result_and_frame() -> Any:
    snapshot = _fixture_snapshot("GLD", "gld.json")
    frame = to_frame(snapshot)
    result = compute_all(snapshot, ExpiryFilter.ALL, frame=frame)
    return result, frame


#: The user's own platform quote used throughout these tests, echoing the task's own example
#: (XAUUSD against a GLD spot near 406.77).
_CFD_SPOT = 4412.50


def test_cfd_instrument_map_covers_every_supported_underlying():
    """Adding an instrument is one line in `CFD_INSTRUMENTS`; this pins the five it must cover
    today (T41's own examples: GLD/XAUUSD, DIA/US30, SPX & SPY/US500, QQQ/NAS100)."""
    assert R.CFD_INSTRUMENTS == {
        "GLD": "XAUUSD",
        "DIA": "US30",
        "SPX": "US500",
        "SPY": "US500",
        "QQQ": "NAS100",
    }


def test_cfd_is_none_when_no_cfd_spot_is_supplied(gld_report):
    """The default, and every deployment's state today: no converted block, no placeholder."""
    _result, report = gld_report
    assert report.cfd is None
    assert report.to_dict()["cfd"] is None


def test_absent_cfd_spot_leaves_the_report_byte_identical(gld_result_and_frame):
    """`build_report(..., cfd_spot=None)` must produce the same report `build_report(...)`
    (T39's own default) does — the whole point of `cfd` being an *additional* field rather
    than a rewrite of any existing one."""
    result, frame = gld_result_and_frame
    with_default = R.build_report(result, frame, ExpiryFilter.ALL)
    explicit_none = R.build_report(result, frame, ExpiryFilter.ALL, cfd_spot=None)
    assert with_default.to_dict() == explicit_none.to_dict()


def test_cfd_spot_only_adds_the_cfd_block_and_changes_nothing_else(gld_result_and_frame):
    """Supplying `cfd_spot` must not perturb a single field this report already had.

    Compares the two `to_dict()` outputs with the `cfd` key removed from each — every other
    key, including `positioning` (GEX magnitudes), `premium` (bid/ask/mid/iv) and `iv_regime`,
    must be untouched by translation.
    """
    result, frame = gld_result_and_frame
    native = R.build_report(result, frame, ExpiryFilter.ALL).to_dict()
    translated = R.build_report(result, frame, ExpiryFilter.ALL, cfd_spot=_CFD_SPOT).to_dict()

    native.pop("cfd")
    translated.pop("cfd")
    assert native == translated


def test_cfd_translation_never_touches_gex_premiums_or_iv(gld_result_and_frame):
    """Belt-and-braces on the specific fields T41 calls out as never-convert, read straight off
    the dataclasses rather than through `to_dict()`."""
    result, frame = gld_result_and_frame
    report = R.build_report(result, frame, ExpiryFilter.ALL, cfd_spot=_CFD_SPOT)

    assert report.positioning.net_gex == pytest.approx(result.levels.net_gex)
    assert report.positioning.abs_gex == pytest.approx(result.levels.abs_gex)
    for level in (*report.levels.resistance, *report.levels.support, *report.levels.straddling):
        # `net_gex`/`abs_gex` on each level are dollars of dealer delta in the underlying's
        # options — translation must not have touched them.
        assert isinstance(level.net_gex, float)
    for candidate in (*report.premium.calls, *report.premium.puts):
        assert candidate.mid is None or isinstance(candidate.mid, float)
    # The CFD block itself carries no IV, no bid/ask/mid anywhere in its shape.
    cfd_dict = report.cfd.to_dict()
    for blob in (cfd_dict, *cfd_dict["premium_calls"], *cfd_dict["premium_puts"]):
        assert "iv" not in blob
        assert "mid" not in blob
        assert "bid" not in blob
        assert "ask" not in blob


def test_cfd_percentage_distance_invariant(gld_result_and_frame):
    """T41's own acceptance test: percentage distances from spot are identical in both units.

    This holds by construction (`CfdLevel.distance_pct` is passed through, never recomputed —
    see `translate_to_cfd`), but this test proves it holds for the *actual* numbers this
    fixture chain produces, not merely that the code path exists.
    """
    result, frame = gld_result_and_frame
    report = R.build_report(result, frame, ExpiryFilter.ALL, cfd_spot=_CFD_SPOT)
    cfd = report.cfd
    ratio = _CFD_SPOT / result.spot

    assert cfd.ratio == pytest.approx(ratio)
    assert cfd.cfd_spot == _CFD_SPOT
    assert cfd.underlying_spot == pytest.approx(result.spot)

    for native_level, cfd_level in zip(report.levels.resistance, cfd.resistance, strict=True):
        assert cfd_level.strike == pytest.approx(native_level.strike * ratio)
        assert cfd_level.distance_pct == native_level.distance_pct
        # And the translated distance, independently recomputed, agrees to float precision —
        # this is the "+2.02% in both units" check made concrete.
        recomputed = (cfd_level.strike - cfd.cfd_spot) / cfd.cfd_spot * 100.0
        assert recomputed == pytest.approx(native_level.distance_pct, abs=1e-9)

    for native_level, cfd_level in zip(report.levels.support, cfd.support, strict=True):
        assert cfd_level.strike == pytest.approx(native_level.strike * ratio)
        assert cfd_level.distance_pct == native_level.distance_pct

    if report.max_pain.strike is not None:
        assert cfd.max_pain.strike == pytest.approx(report.max_pain.strike * ratio)
        assert cfd.max_pain.distance_pct == report.max_pain.distance_pct

    if report.levels.call_wall is not None:
        assert cfd.call_wall.strike == pytest.approx(report.levels.call_wall * ratio)
    if report.levels.put_wall is not None:
        assert cfd.put_wall.strike == pytest.approx(report.levels.put_wall * ratio)

    for native_entry, cfd_entry in zip(report.playbook.entries, cfd.playbook, strict=True):
        assert native_entry.key == cfd_entry.key
        for native_val, cfd_val in (
            (native_entry.trigger, cfd_entry.trigger),
            (native_entry.target, cfd_entry.target),
            (native_entry.invalidation, cfd_entry.invalidation),
        ):
            if native_val is None:
                assert cfd_val is None
            else:
                assert cfd_val == pytest.approx(native_val * ratio)


def test_cfd_premium_candidate_strike_translates_but_nothing_else(gld_result_and_frame):
    # DIA's fixture actually screens in candidates on this filter; use it here so the
    # premium-strike translation has real rows to check rather than an empty screen.
    dia_snapshot = _fixture_snapshot("DIA", "dia.json")
    dia_frame = to_frame(dia_snapshot)
    dia_result = compute_all(dia_snapshot, ExpiryFilter.ALL, frame=dia_frame)
    report = R.build_report(dia_result, dia_frame, ExpiryFilter.ALL, cfd_spot=44125.0)
    ratio = 44125.0 / dia_result.spot

    by_symbol = {c.occ_symbol: c for c in report.cfd.premium_calls + report.cfd.premium_puts}
    native_candidates = list(report.premium.calls) + list(report.premium.puts)
    assert native_candidates, "expected at least one screened DIA candidate on this fixture"
    for candidate in native_candidates:
        translated = by_symbol[candidate.occ_symbol]
        assert translated.strike == pytest.approx(candidate.strike * ratio)


@pytest.mark.parametrize("bad_spot", [0.0, -100.0, float("nan"), float("inf")])
def test_translate_to_cfd_rejects_non_positive_or_non_finite_spot(gld_result_and_frame, bad_spot):
    """A zero, negative, NaN or infinite `cfd_spot` must raise rather than silently producing
    an infinite or NaN translated level."""
    result, frame = gld_result_and_frame
    with pytest.raises(ValueError):
        R.build_report(result, frame, ExpiryFilter.ALL, cfd_spot=bad_spot)


def test_translate_to_cfd_rejects_an_unmapped_underlying():
    with pytest.raises(ValueError, match="no CFD instrument"):
        R.translate_to_cfd(
            "NOPE",
            100.0,
            50.0,
            R.LevelSet(
                resistance=(), support=(), straddling=(), overlapping=False, overlap_note=None,
                call_wall=None, put_wall=None, flip_point=None,
            ),
            R.MaxPain(strike=None, distance=None, distance_pct=None, total_pain=None, strikes_evaluated=0, contracts=0, open_interest=0),
            R.Playbook(entries=(), range_low=None, range_high=None, range_magnet=None, spot_in_range=False),
            R.PremiumSelling(calls=(), puts=(), dte_min=7, dte_max=45, call_boundary=None, put_boundary=None, note=None),
        )


def test_render_text_carries_the_cfd_translation(gld_result_and_frame):
    """The user asked specifically for trade plans to carry the translated numbers; the text
    render is what gets copied out of the app."""
    result, frame = gld_result_and_frame
    report = R.build_report(result, frame, ExpiryFilter.ALL, cfd_spot=_CFD_SPOT)
    text = R.render_text(report)

    assert "XAUUSD" in text
    assert "TRANSLATION" in text
    assert f"{_CFD_SPOT:,.2f}" in text
    # The translated call wall value appears somewhere in the rendered text.
    assert f"{report.cfd.call_wall.strike:,.2f}" in text


def test_render_text_has_no_cfd_section_without_cfd_spot(gld_report):
    _result, report = gld_report
    text = R.render_text(report)
    assert "XAUUSD" not in text
    assert "TRANSLATION" not in text


def test_render_text_marks_dia_noise_dominated_when_it_is():
    """The rendered text must carry the positioning label verbatim, whatever it is."""
    positioning = R.dealer_positioning(-8_824_612.39, 1_037_320_166.19)
    assert positioning.label in R.render_text(
        R.ReportResult(
            underlying="DIA",
            filter="ALL",
            spot=532.34,
            generated_at=AS_OF,
            snapshot=compute_all(_chain(_MAX_PAIN_CHAIN)).snapshot,
            max_pain=R.max_pain(to_frame(_chain(())), 532.34),
            ratios=R.put_call_ratios(to_frame(_chain(()))),
            iv_regime=R.iv_regime(to_frame(_chain(())), 532.34),
            positioning=positioning,
            levels=R.support_resistance(compute_all(_chain(()))),
            premium=R.premium_selling(to_frame(_chain(())), compute_all(_chain(()))),
            playbook=R.Playbook(entries=(), range_low=None, range_high=None, range_magnet=None, spot_in_range=False),
        )
    )
