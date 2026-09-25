"""Provider factory — the one place ``PROVIDER`` config resolves to a live provider instance.

PLAN.md §2's "swapping providers is a config change, not a refactor" only holds if there is a
single, tested seam where the config value turns into an object. This module is that seam:
every caller (the T05 scheduler, the T02/T03 CLIs, tests) should go through :func:`get_provider`
rather than importing a concrete provider class directly, so that changing ``PROVIDER`` in
``.env`` is genuinely the only thing that has to change.
"""

from __future__ import annotations

from app.core.config import settings
from app.modules.gex.providers.base import OptionChainProvider
from app.modules.gex.providers.cboe import CboeProvider
from app.modules.gex.providers.marketdata import MarketDataProvider
from app.modules.gex.providers.thetadata import ThetaDataProvider

__all__ = ["get_provider"]

#: Registered provider name -> constructor. Adding a provider is a one-line change here.
_PROVIDERS: dict[str, type[OptionChainProvider]] = {
    "cboe": CboeProvider,
    "marketdata": MarketDataProvider,
    "thetadata": ThetaDataProvider,
}


def get_provider(name: str | None = None) -> OptionChainProvider:
    """Construct the :class:`OptionChainProvider` registered under ``name``.

    Args:
        name: A registered provider name (case-insensitive). Defaults to
            ``settings.PROVIDER`` when omitted, which is how ``PROVIDER=marketdata`` in
            ``.env`` swaps the whole application's data source without any code change.

    Returns:
        A freshly constructed provider instance. Providers are documented as cheap to
        construct (see ``base.py``), so no caching is done here.

    Raises:
        ValueError: ``name`` (or the configured ``PROVIDER``) is not a registered provider
            name. This is a startup-time configuration error, not a
            :class:`~app.modules.gex.providers.base.ProviderError` — there is no vendor to blame for an
            unrecognized name typed into ``.env``.
    """
    key = (name if name is not None else settings.PROVIDER).strip().lower()
    try:
        provider_cls = _PROVIDERS[key]
    except KeyError:
        raise ValueError(
            f"unknown provider {key!r}; registered providers are {sorted(_PROVIDERS)}"
        ) from None
    return provider_cls()
