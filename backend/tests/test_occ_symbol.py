"""Tests for OCC symbol parsing and the root-derived settlement rule.

The required cases come from TASKS.md T01. The malformed cases exist because providers feed
this parser whatever the vendor sends, and a symbol that silently mis-parses becomes a
contract priced at the wrong strike or expiry — a failure that would only surface as a
slightly wrong GEX number many tasks later.
"""

import datetime as dt

import pytest

from app.models.chain import (
    OCC_TAIL_LEN,
    Right,
    Settlement,
    Underlying,
    parse_occ_symbol,
    settlement_for_root,
    strike_to_occ_int,
    underlying_for_root,
)

# (symbol, root, expiry, right, strike, settlement, underlying)
CASES = [
    ("SPX260918C00200000", "SPX", dt.date(2026, 9, 18), Right.CALL, 200.0, Settlement.AM, "SPX"),
    ("SPXW260904P07700000", "SPXW", dt.date(2026, 9, 4), Right.PUT, 7700.0, Settlement.PM, "SPX"),
    ("SPY260904C00500000", "SPY", dt.date(2026, 9, 4), Right.CALL, 500.0, Settlement.PM, "SPY"),
    ("QQQ261218P00400000", "QQQ", dt.date(2026, 12, 18), Right.PUT, 400.0, Settlement.PM, "QQQ"),
]


@pytest.mark.parametrize(("symbol", "root", "expiry", "right", "strike", "_s", "_u"), CASES)
def test_parses_required_cases(symbol, root, expiry, right, strike, _s, _u):
    parsed = parse_occ_symbol(symbol)
    assert parsed == (root, expiry, right, strike)
    assert parsed.root == root and parsed.strike == strike


@pytest.mark.parametrize(("symbol", "root", "_e", "_r", "_k", "settlement", "underlying"), CASES)
def test_settlement_and_underlying_are_root_derived(
    symbol, root, _e, _r, _k, settlement, underlying
):
    """SPX is AM-settled, SPXW/SPY/QQQ are PM-settled, and SPXW merges into SPX."""
    assert settlement_for_root(root) is settlement
    assert underlying_for_root(root) is Underlying(underlying)


def test_spx_and_spxw_coexist_on_the_same_third_friday():
    """Verified on the live Cboe chain: settlement cannot be inferred from the date alone."""
    am = parse_occ_symbol("SPX260918C07710000")
    pm = parse_occ_symbol("SPXW260918C07710000")
    assert (am.expiry, am.strike, am.right) == (pm.expiry, pm.strike, pm.right)
    assert settlement_for_root(am.root) is Settlement.AM
    assert settlement_for_root(pm.root) is Settlement.PM
    assert underlying_for_root(am.root) is underlying_for_root(pm.root) is Underlying.SPX


def test_accepts_official_space_padded_root():
    """The OCC standard pads the root to six characters; vendor JSON usually does not."""
    padded = parse_occ_symbol("SPY   260904C00500000")
    assert len("SPY   260904C00500000") == 21  # the official fixed-width form
    assert padded.root == "SPY"
    assert padded == parse_occ_symbol("SPY260904C00500000")
    assert parse_occ_symbol("SPXW  260904P07700000") == parse_occ_symbol("SPXW260904P07700000")


def test_accepts_surrounding_whitespace_and_lowercase():
    assert parse_occ_symbol("  spy260904c00500000 ") == parse_occ_symbol("SPY260904C00500000")


@pytest.mark.parametrize("length", [1, 2, 3, 4, 5, 6])
def test_handles_roots_of_one_to_six_characters(length):
    root = "ABCDEF"[:length]
    parsed = parse_occ_symbol(f"{root}260904C00500000")
    assert parsed.root == root
    assert parsed.expiry == dt.date(2026, 9, 4)


def test_root_ending_in_a_digit_parses_from_the_right():
    """Adjusted-option roots such as SPY1 make a left-anchored letters-only match ambiguous."""
    parsed = parse_occ_symbol("SPY1260904C00500000")
    assert parsed.root == "SPY1"
    assert parsed.expiry == dt.date(2026, 9, 4)
    assert parsed.strike == 500.0


@pytest.mark.parametrize(
    ("symbol", "expected_strike"),
    [
        ("SPY260904C00500000", 500.0),  # whole dollars
        ("SPY260904C00502500", 502.5),  # half-dollar strike, common on SPY/QQQ
        ("SPY260904C00000500", 0.5),  # sub-dollar strike
        ("SPX260918C20000000", 20000.0),  # highest strike listed on the live SPX chain
        ("SPY260904C00123456", 123.456),  # thousandths, the encoding's full resolution
    ],
)
def test_strike_decoding_and_exact_round_trip(symbol, expected_strike):
    """Justifies float over Decimal: int -> int/1000 -> round(x*1000) is exact in this range."""
    strike = parse_occ_symbol(symbol).strike
    assert strike == pytest.approx(expected_strike)
    assert strike_to_occ_int(strike) == int(symbol[-8:])


def test_every_plausible_strike_round_trips_through_float():
    """Exhaustive over $0.50 increments to $20,000 plus every cent to $1,000."""
    ints = list(range(500, 20_000_001, 500)) + list(range(10, 1_000_001, 10))
    strikes = [i / 1000 for i in ints]
    assert [strike_to_occ_int(s) for s in strikes] == ints
    assert len(set(strikes)) == len(set(ints))  # injective: no two strikes collide


def test_two_digit_year_maps_to_2000s():
    assert parse_occ_symbol("SPX991218C00200000").expiry == dt.date(2099, 12, 18)


@pytest.mark.parametrize(
    ("symbol", "why"),
    [
        ("", "empty"),
        ("   ", "whitespace only"),
        ("SPY", "no tail"),
        ("260904C00500000", "no root"),
        ("      260904C00500000", "padding but no root"),
        ("TOOLONG260904C00500000", "root over six characters"),
        ("SPY26090C00500000", "five-digit date"),
        ("SPY2609040C00500000", "seven-digit date"),
        ("SPY26AB04C00500000", "non-numeric date"),
        ("SPY261332C00500000", "month 13"),
        ("SPY260931C00500000", "31 September"),
        ("SPY260229C00500000", "29 February in a non-leap year"),
        ("SPY260904X00500000", "right is not C or P"),
        ("SPY260904C0050000A", "non-numeric strike"),
        ("SPY260904C00000000", "zero strike"),
        ("SPY260904C0050000", "seven-digit strike"),
        ("SPY260904C005000000", "nine-digit strike"),
        ("SP-260904C00500000", "punctuation in root"),
        ("1SPY260904C00500000", "root starts with a digit"),
        ("SPY 260904C0050000", "internal space, short tail"),
    ],
)
def test_rejects_malformed_symbols(symbol, why):
    with pytest.raises(ValueError):
        parse_occ_symbol(symbol)


@pytest.mark.parametrize("bad", [None, 12345, b"SPY260904C00500000", 3.14])
def test_rejects_non_string_input(bad):
    with pytest.raises(ValueError):
        parse_occ_symbol(bad)


def test_unknown_root_raises_rather_than_guessing():
    """A new listing must surface as an error, not silently pollute an underlying's GEX."""
    with pytest.raises(ValueError, match="unknown option root"):
        underlying_for_root("NDX")


def test_settlement_defaults_to_pm_for_unknown_roots():
    """Settlement is a safe default (PM); the underlying mapping is the strict gate."""
    assert settlement_for_root("IWM") is Settlement.PM


def test_occ_tail_length_is_the_documented_fifteen():
    assert OCC_TAIL_LEN == len("260904") + len("C") + len("00500000")
