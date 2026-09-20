"""Tests for the OptionContract / ChainSnapshot contract itself.

These lock in the conventions that later tasks depend on: immutability, tz-aware UTC
timestamps, the ``None``-versus-zero rule for missing vendor data, IV as a decimal, and the
SPX/SPXW merge. If one of these fails, a provider or the engine has drifted from the contract.
"""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.modules.gex.models.chain import (
    ChainSnapshot,
    OptionContract,
    Right,
    Settlement,
    Underlying,
)

NY = ZoneInfo("America/New_York")


def make_contract(symbol: str = "SPXW260904C07710000", **kw) -> OptionContract:
    base = {
        "bid": 4.8,
        "ask": 4.9,
        "last": 4.85,
        "volume": 1200,
        "open_interest": 1288,
        "iv": 0.1092,
        "delta": 0.4795,
        "gamma": 0.03,
        "vega": 0.5,
        "theta": -2.1,
    }
    return OptionContract.from_occ(symbol, **(base | kw))


def make_snapshot(contracts=(), **kw) -> ChainSnapshot:
    base = {
        "underlying": Underlying.SPX,
        "spot": 7709.52,
        "captured_at": dt.datetime(2026, 9, 4, 18, 5, 33, tzinfo=dt.UTC),
        "source": "cboe",
        "delayed_minutes": 15,
        "contracts": contracts,
    }
    return ChainSnapshot(**(base | kw))


def test_from_occ_derives_every_identity_field():
    c = make_contract("SPX260918C00200000")
    assert c.root == "SPX"
    assert c.underlying is Underlying.SPX
    assert c.expiry == dt.date(2026, 9, 18)
    assert c.settlement is Settlement.AM
    assert c.strike == 200.0
    assert c.right is Right.CALL
    assert c.multiplier == 100


def test_from_occ_normalizes_padded_and_lowercase_symbols():
    c = make_contract("spxw  260904c07710000")
    assert c.occ_symbol == "SPXW260904C07710000"


def test_spx_and_spxw_merge_under_one_underlying_but_keep_distinct_roots():
    """PLAN.md §3 item 2. The natural key must include root, or these two collide."""
    am = make_contract("SPX260918C07710000")
    pm = make_contract("SPXW260918C07710000")
    assert am.underlying is pm.underlying is Underlying.SPX
    assert (am.expiry, am.strike, am.right) == (pm.expiry, pm.strike, pm.right)
    assert am.root != pm.root
    assert am.settlement is Settlement.AM and pm.settlement is Settlement.PM
    assert make_snapshot([am, pm]).roots == ("SPX", "SPXW")


def test_contract_is_frozen():
    c = make_contract()
    with pytest.raises(ValidationError):
        c.strike = 1.0


def test_missing_vendor_data_is_none_not_zero():
    c = OptionContract.from_occ("SPY260904C00500000")
    assert (c.bid, c.ask, c.last, c.iv, c.delta, c.gamma, c.vega, c.theta) == (None,) * 8
    assert c.open_interest is None and c.volume is None
    assert c.last_trade_time is None


def test_zero_open_interest_is_distinct_from_unknown():
    assert make_contract(open_interest=0).open_interest == 0
    assert make_contract(open_interest=None).open_interest is None


def test_iv_sentinel_zero_is_rejected_so_providers_must_map_it_to_none():
    """Cboe emits iv: 0.0 on illiquid strikes; passing it through would poison the Greeks."""
    with pytest.raises(ValidationError):
        make_contract(iv=0.0)
    with pytest.raises(ValidationError):
        make_contract(iv=-0.1)
    assert make_contract(iv=None).iv is None


def test_iv_is_a_decimal_fraction_not_a_percent():
    """A percent-scaled IV (10.64 for 10.64%) is accepted by the type but is a bug; this test
    documents the intended magnitude so a reviewer sees the convention in one place."""
    atm_spx = make_contract("SPX260918C07710000", iv=0.1064)
    assert 0.0 < atm_spx.iv < 1.0


def test_gamma_zero_is_accepted_because_vendors_round_it():
    assert make_contract(gamma=0.0).gamma == 0.0
    with pytest.raises(ValidationError):
        make_contract(gamma=-0.001)


def test_greeks_carry_no_dealer_sign():
    """Puts keep a negative delta and a positive gamma; T08 applies the ±1 dealer sign."""
    put = make_contract("SPXW260904P07710000", delta=-0.5205, gamma=0.03)
    assert put.delta < 0
    assert put.gamma > 0


def test_extra_vendor_fields_are_rejected():
    """Cboe sends rho/theo/tick/prev_day_close; the adapter must drop them explicitly."""
    with pytest.raises(TypeError):
        make_contract(rho=0.07)
    with pytest.raises(ValidationError):
        OptionContract(
            occ_symbol="SPY260904C00500000",
            root="SPY",
            underlying=Underlying.SPY,
            expiry=dt.date(2026, 9, 4),
            settlement=Settlement.PM,
            strike=500.0,
            right=Right.CALL,
            rho=0.07,
        )


def test_last_trade_time_must_be_aware_and_is_normalized_to_utc():
    """Cboe's last_trade_time is naive America/New_York — a blanket UTC assumption is wrong."""
    with pytest.raises(ValidationError):
        make_contract(last_trade_time=dt.datetime(2026, 9, 4, 13, 50, 31))  # noqa: DTZ001
    c = make_contract(last_trade_time=dt.datetime(2026, 9, 4, 13, 50, 31, tzinfo=NY))
    assert c.last_trade_time == dt.datetime(2026, 9, 4, 17, 50, 31, tzinfo=dt.UTC)
    assert c.last_trade_time.tzinfo is dt.UTC


def test_snapshot_captured_at_must_be_aware_and_is_normalized_to_utc():
    with pytest.raises(ValidationError):
        make_snapshot(captured_at=dt.datetime(2026, 9, 4, 18, 5, 33))  # noqa: DTZ001
    snap = make_snapshot(captured_at=dt.datetime(2026, 9, 4, 14, 5, 33, tzinfo=NY))
    assert snap.captured_at == dt.datetime(2026, 9, 4, 18, 5, 33, tzinfo=dt.UTC)


def test_snapshot_rejects_contracts_for_a_different_underlying():
    with pytest.raises(ValidationError, match="contains contracts for"):
        make_snapshot([make_contract("SPY260904C00500000")])


def test_snapshot_is_frozen_and_contracts_are_a_tuple():
    snap = make_snapshot([make_contract()])
    assert isinstance(snap.contracts, tuple)
    with pytest.raises(ValidationError):
        snap.spot = 1.0


def test_snapshot_helpers():
    snap = make_snapshot(
        [make_contract("SPXW260904C07710000"), make_contract("SPX260918C00200000")]
    )
    assert len(snap) == 2
    assert snap.expiries == (dt.date(2026, 9, 4), dt.date(2026, 9, 18))


def test_snapshot_rejects_nonsensical_scalars():
    with pytest.raises(ValidationError):
        make_snapshot(spot=0)
    with pytest.raises(ValidationError):
        make_snapshot(delayed_minutes=-1)
    with pytest.raises(ValidationError):
        make_snapshot(source="")


def test_round_trips_through_json_for_storage_and_api_layers():
    snap = make_snapshot([make_contract()])
    assert ChainSnapshot.model_validate_json(snap.model_dump_json()) == snap
