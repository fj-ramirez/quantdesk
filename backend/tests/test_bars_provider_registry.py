"""Tests for `app/providers/bars.py`'s `BarProviderRegistry` symbol-routing logic.

Offline throughout -- no provider here ever makes an HTTP call; these tests only check which
class gets constructed for which symbol, using `token=` overrides so `TiingoBarProvider`'s
credential guard never fires.
"""

from __future__ import annotations

import pytest

from app.providers.bars import BarProviderRegistry, get_bar_provider
from app.providers.tiingo import TiingoBarProvider
from app.providers.yahoo import YahooBarProvider


def test_default_provider_is_yahoo_when_unconfigured():
    registry = BarProviderRegistry(default="yahoo", groups="")
    assert registry.provider_name_for("SPY") == "yahoo"
    assert isinstance(registry.for_symbol("SPY"), YahooBarProvider)


def test_groups_route_specific_symbols_to_a_different_provider():
    registry = BarProviderRegistry(default="yahoo", groups="tiingo:^VIX,SPX")
    assert registry.provider_name_for("^VIX") == "tiingo"
    assert registry.provider_name_for("SPX") == "tiingo"
    assert registry.provider_name_for("SPY") == "yahoo"  # not listed -> falls to default


def test_symbol_matching_is_case_insensitive():
    registry = BarProviderRegistry(default="yahoo", groups="tiingo:spy")
    assert registry.provider_name_for("SPY") == "tiingo"
    assert registry.provider_name_for("spy") == "tiingo"


def test_multiple_groups_are_semicolon_separated():
    registry = BarProviderRegistry(default="yahoo", groups="tiingo:^VIX;yahoo:SPX")
    assert registry.provider_name_for("^VIX") == "tiingo"
    assert registry.provider_name_for("SPX") == "yahoo"


def test_empty_groups_string_routes_everything_to_default():
    registry = BarProviderRegistry(default="tiingo", groups="")
    assert registry.provider_name_for("ANYTHING") == "tiingo"


def test_malformed_group_entry_is_skipped_not_fatal():
    """A group with no ':' is dropped rather than raising -- a typo in BAR_PROVIDER_GROUPS
    should degrade to 'falls back to default', not break every bars fetch."""
    registry = BarProviderRegistry(default="yahoo", groups="not-a-valid-group;tiingo:SPX")
    assert registry.provider_name_for("SPX") == "tiingo"
    assert registry.provider_name_for("SPY") == "yahoo"


def test_unknown_provider_name_raises_on_construction():
    registry = BarProviderRegistry(default="not-a-real-provider", groups="")
    with pytest.raises(ValueError, match="unknown bars provider"):
        registry.for_symbol("SPY")


def test_provider_instances_are_cached_per_provider_name(monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "TIINGO_TOKEN", "test-token")  # avoid MissingCredential

    registry = BarProviderRegistry(default="yahoo", groups="tiingo:SPX")
    first_yahoo = registry.for_symbol("SPY")
    second_yahoo = registry.for_symbol("QQQ")
    assert first_yahoo is second_yahoo  # both route to "yahoo" -> same cached instance

    tiingo_provider = registry.for_symbol("SPX")
    assert isinstance(tiingo_provider, TiingoBarProvider)
    assert tiingo_provider is not first_yahoo


async def test_aclose_closes_every_constructed_provider():
    registry = BarProviderRegistry(default="yahoo", groups="")
    registry.for_symbol("SPY")
    await registry.aclose()  # must not raise even though no client was ever opened (lazy)


def test_get_bar_provider_one_shot_convenience():
    provider = get_bar_provider("SPY", default="yahoo", groups="")
    assert isinstance(provider, YahooBarProvider)
