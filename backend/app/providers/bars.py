"""Daily-bar provider interface and registry (T42,
plans/continuation/00-foundation-daily-bars.md).

Mirrors `app.providers.base`: `BarProvider` is the one abstract class every bars data source
implements, and every caller (the update job, the backfill CLI, the `/api/bars` route) goes
through `BarProviderRegistry` rather than importing a concrete provider module directly --
CLAUDE.md invariant 6 ("provider swaps are a config change, never an edit to callers") applies
to bars exactly as it does to the option-chain providers in `app.providers.__init__`.

The error hierarchy is reused, not reinvented: `app.providers.base.ProviderError`,
`UpstreamUnavailable` and `SymbolNotSupported` are generic to "a provider fetched something and
failed" and carry no option-chain-specific meaning, so bars providers raise them directly
rather than defining parallel bars-only exception classes a caller would have to catch twice.
They are re-exported here so `app.providers.yahoo` / `app.providers.tiingo` can import them
from the bars module they already depend on, rather than reaching into `app.providers.base`
for something conceptually shared, not chain-specific.

Provider routing by symbol, not one global provider
-----------------------------------------------------
The plan's design decision: T54 adds a Cboe index-history provider for `^VIX`-style series
where a vendor-native index history is preferable to Yahoo's. Rather than one `BARS_PROVIDER`
setting picking a single provider for every symbol, `BarProviderRegistry.for_symbol()` resolves
a symbol to a provider using a config map of provider -> symbol group, parsed from
`settings.BAR_PROVIDER_GROUPS`. Symbols not listed in any group fall through to the default
(`settings.BARS_PROVIDER`). This is deliberately just a lookup table, not a plugin system: no
discovery, no per-symbol wildcards, one flat dict built once per registry.

`BAR_PROVIDER_GROUPS` format: ``"provider1:SYM1,SYM2;provider2:SYM3"`` -- semicolon-separated
groups, each a provider name, a colon, then a comma-separated symbol list. Empty (the default)
means every symbol uses `BARS_PROVIDER`. Example for T54: ``"cboe-index:^VIX"``.

Concrete provider modules (`app.providers.yahoo`, `app.providers.tiingo`) are imported lazily,
inside `_construct`, rather than at module level -- importing them eagerly here would create a
circular import, since both of those modules import `BarProvider` from this one.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod

from app.config import settings
from app.models.bars import DailyBar
from app.providers.base import ProviderError, SymbolNotSupported, UpstreamUnavailable

__all__ = [
    "BarProvider",
    "BarProviderRegistry",
    "ProviderError",
    "SymbolNotSupported",
    "UpstreamUnavailable",
    "get_bar_provider",
]

#: Registered bar provider names -> lazy constructor. Adding a provider is a one-line change to
#: `_construct` below plus this tuple (kept only for the error message's "here is what exists").
_REGISTERED_PROVIDERS = ("yahoo", "tiingo")


class BarProvider(ABC):
    """Fetch normalized daily OHLCV bars from one data source.

    Same lifecycle contract as `app.providers.base.OptionChainProvider`: cheap to construct,
    safe to reuse across calls, owns its own `httpx.AsyncClient` lazily with an optional
    injected one for tests (see `app.providers.cboe.CboeProvider` for the exact pattern every
    bars provider here reuses).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable provider identifier written to `DailyBar.source`, e.g. 'yahoo-splitadj'."""

    @abstractmethod
    async def fetch_daily_bars(
        self, symbol: str, *, start: dt.date, end: dt.date | None = None
    ) -> list[DailyBar]:
        """Fetch daily bars for `symbol` in `[start, end]` inclusive, ascending by date.

        Args:
            symbol: A plain ticker as this app's callers know it (`'SPY'`, `'^VIX'`, `'SPX'`).
                Vendor-specific symbol mangling (Yahoo's `SPX` -> `^GSPC`) is the provider's job
                -- see `app.providers.yahoo`'s module docstring -- never the caller's, and never
                leaks back out: every `DailyBar.symbol` this returns is the plain ticker the
                caller asked for, not the vendor's internal one.
            start: Inclusive lower bound, exchange-local date.
            end: Inclusive upper bound, exchange-local date. Defaults to "as much as the vendor
                has up to today." A provider must never return a bar for the vendor's own
                in-progress trading session -- see `app.providers.yahoo`'s module docstring for
                why persisting that row is this task's single most damaging failure mode.

        Raises:
            SymbolNotSupported: the vendor has no such symbol at all.
            UpstreamUnavailable: the vendor failed after the provider's own retry budget.
        """

    async def close(self) -> None:  # pragma: no cover - default no-op, overridden where owned
        """Close any owned network resources. A no-op unless a subclass overrides it."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"


def _construct(provider_name: str) -> BarProvider:
    """Construct the `BarProvider` registered under `provider_name`. See module docstring for
    why the concrete modules are imported here rather than at module load time."""
    key = provider_name.strip().lower()
    if key == "yahoo":
        from app.providers.yahoo import YahooBarProvider

        return YahooBarProvider()
    if key == "tiingo":
        from app.providers.tiingo import TiingoBarProvider

        return TiingoBarProvider()
    raise ValueError(
        f"unknown bars provider {key!r}; registered providers are {_REGISTERED_PROVIDERS}"
    )


def _parse_groups(raw: str) -> dict[str, str]:
    """Parse `BAR_PROVIDER_GROUPS` into a `{SYMBOL: provider_name}` map. See module docstring
    for the ``"provider:SYM1,SYM2;provider2:SYM3"`` format. Malformed groups (no ``:``, an
    empty symbol list) are simply skipped rather than raising -- a typo in this setting should
    degrade to "that symbol falls back to the default provider," not break every bars fetch.
    """
    mapping: dict[str, str] = {}
    for group in raw.split(";"):
        group = group.strip()
        if not group or ":" not in group:
            continue
        provider_name, _, symbols_part = group.partition(":")
        provider_name = provider_name.strip().lower()
        if not provider_name:
            continue
        for sym in symbols_part.split(","):
            sym = sym.strip().upper()
            if sym:
                mapping[sym] = provider_name
    return mapping


class BarProviderRegistry:
    """Resolves a plain ticker to a `BarProvider`, per the symbol-group routing described in
    the module docstring. Caches one provider instance per provider name for its own lifetime,
    so a caller that touches 80 symbols but only two providers reuses each provider's (and
    therefore each provider's `httpx.AsyncClient`'s) connection across every symbol that routes
    to it, rather than opening a fresh one per symbol.
    """

    def __init__(self, *, default: str | None = None, groups: str | None = None) -> None:
        """
        Args:
            default: Provider name for any symbol not listed in `groups`. Defaults to
                `settings.BARS_PROVIDER`.
            groups: Raw `BAR_PROVIDER_GROUPS`-format string. Defaults to
                `settings.BAR_PROVIDER_GROUPS`. Tests pass this explicitly to exercise routing
                without touching `.env`.
        """
        self._default = (default if default is not None else settings.BARS_PROVIDER).strip().lower()
        self._symbol_provider = _parse_groups(
            groups if groups is not None else settings.BAR_PROVIDER_GROUPS
        )
        self._instances: dict[str, BarProvider] = {}

    def provider_name_for(self, symbol: str) -> str:
        """The provider name `symbol` routes to, without constructing anything."""
        return self._symbol_provider.get(symbol.strip().upper(), self._default)

    def for_symbol(self, symbol: str) -> BarProvider:
        """The (possibly cached) `BarProvider` instance `symbol` routes to."""
        provider_name = self.provider_name_for(symbol)
        if provider_name not in self._instances:
            self._instances[provider_name] = _construct(provider_name)
        return self._instances[provider_name]

    async def aclose(self) -> None:
        """Close every provider instance this registry has constructed so far."""
        for provider in self._instances.values():
            await provider.close()


def get_bar_provider(symbol: str, *, default: str | None = None, groups: str | None = None) -> BarProvider:
    """One-shot convenience: build a registry from settings (or the given overrides) and
    resolve `symbol` immediately. Prefer constructing a `BarProviderRegistry` directly and
    reusing it across many symbols (see its own docstring) -- this exists for call sites that
    only ever need one symbol at a time, e.g. a single `/api/bars/{symbol}` request.
    """
    return BarProviderRegistry(default=default, groups=groups).for_symbol(symbol)
