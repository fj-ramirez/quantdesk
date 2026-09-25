"""Tests for the ``get_provider`` factory (``app.modules.gex.providers.__init__``).

No network: constructing a provider must not make any request, so these tests only check that
the right class comes back (and, for ``marketdata``, that it is usable — i.e. actually
constructed, not just importable).
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.gex.providers import get_provider
from app.modules.gex.providers.cboe import CboeProvider
from app.modules.gex.providers.marketdata import MarketDataProvider, MissingCredential


def test_get_provider_cboe_by_name():
    provider = get_provider("cboe")
    assert isinstance(provider, CboeProvider)
    assert provider.name == "cboe"


def test_get_provider_cboe_is_case_insensitive():
    assert isinstance(get_provider("CBOE"), CboeProvider)
    assert isinstance(get_provider(" Cboe "), CboeProvider)


def test_get_provider_marketdata_by_name(monkeypatch):
    monkeypatch.setattr(settings, "MARKETDATA_TOKEN", "test-token")
    provider = get_provider("marketdata")
    assert isinstance(provider, MarketDataProvider)
    assert provider.name == "marketdata"


def test_get_provider_marketdata_without_token_raises_clearly(monkeypatch):
    monkeypatch.setattr(settings, "MARKETDATA_TOKEN", "")
    with pytest.raises(MissingCredential, match="MARKETDATA_TOKEN"):
        get_provider("marketdata")


def test_get_provider_defaults_to_settings_provider(monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER", "cboe")
    assert isinstance(get_provider(), CboeProvider)


def test_get_provider_respects_provider_setting_switch(monkeypatch):
    """The T03 acceptance scenario: flipping PROVIDER swaps the returned provider type."""
    monkeypatch.setattr(settings, "MARKETDATA_TOKEN", "test-token")
    monkeypatch.setattr(settings, "PROVIDER", "marketdata")
    assert isinstance(get_provider(), MarketDataProvider)


def test_get_provider_unknown_name_raises_clear_error():
    with pytest.raises(ValueError, match="unknown provider 'fake'"):
        get_provider("fake")


def test_get_provider_unknown_settings_provider_raises_clear_error(monkeypatch):
    monkeypatch.setattr(settings, "PROVIDER", "does-not-exist")
    with pytest.raises(ValueError, match="unknown provider"):
        get_provider()


def test_get_provider_thetadata_by_name(monkeypatch):
    """T126: `PROVIDER=thetadata` resolves to the live provider, not an unknown-name error."""
    from app.modules.gex.providers.thetadata import ThetaDataProvider

    monkeypatch.setattr(settings, "THETADATA_URL", "http://theta-terminal:25503")
    provider = get_provider("thetadata")
    assert isinstance(provider, ThetaDataProvider)
    assert provider.name == "thetadata" and provider.delayed_minutes == 0


def test_get_provider_thetadata_without_url_names_the_setting(monkeypatch):
    from app.modules.gex.providers.base import ProviderError

    monkeypatch.setattr(settings, "THETADATA_URL", "")
    with pytest.raises(ProviderError, match="THETADATA_URL"):
        get_provider("thetadata")


async def test_default_fetch_expiry_keeps_exactly_that_date():
    """A whole-chain vendor (Cboe) still answers the live 0DTE pull correctly."""
    import datetime as dt

    from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
    from app.modules.gex.providers.base import OptionChainProvider

    class WholeChain(OptionChainProvider):
        name = "fake"
        delayed_minutes = 15

        async def fetch_chain(self, underlying: str) -> ChainSnapshot:
            return ChainSnapshot(
                underlying=Underlying.SPY,
                spot=500.0,
                captured_at=dt.datetime(2026, 9, 24, 15, tzinfo=dt.UTC),
                source="fake",
                delayed_minutes=15,
                contracts=[
                    OptionContract.from_occ("SPY260924C00505000", open_interest=1, iv=0.2),
                    OptionContract.from_occ("SPY260925C00505000", open_interest=1, iv=0.2),
                ],
            )

    snap = await WholeChain().fetch_expiry("SPY", dt.date(2026, 9, 24))
    assert [c.expiry for c in snap.contracts] == [dt.date(2026, 9, 24)]
