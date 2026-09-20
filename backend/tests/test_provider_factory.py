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
