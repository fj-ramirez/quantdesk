"""Provider interface — one abstract class every data source implements.

PLAN.md §2 makes the provider the only place vendor-specific knowledge lives: swapping the
free delayed Cboe feed for a paid real-time one is meant to be a config change, not a
refactor. That only holds if every provider returns an identical
:class:`~app.modules.gex.models.chain.ChainSnapshot`, so the contract below is deliberately narrow.

What a provider is responsible for
----------------------------------

1. **Fetching** the vendor's raw chain for one underlying (network, auth, retries, backoff).
2. **Normalizing** it into :class:`~app.modules.gex.models.chain.ChainSnapshot` using
   :func:`~app.modules.gex.models.chain.parse_occ_symbol` and
   :meth:`~app.modules.gex.models.chain.OptionContract.from_occ`, so that root, underlying, expiry,
   settlement, strike and right are derived identically across providers.
3. **Unit conversion**, in particular implied volatility, which this application stores as a
   decimal fraction (``0.18``), never a percent. See ``docs/schema.md``.
4. **Timezones.** ``captured_at`` and ``last_trade_time`` must be tz-aware; the models reject
   naive datetimes rather than assuming UTC. Vendors are inconsistent — the Cboe payload's
   top-level ``timestamp`` is naive UTC while its ``last_trade_time`` values are naive
   America/New_York — so the provider must attach the right zone to each field.
5. **Reporting missing data as ``None``**, never as a fabricated ``0``. A vendor sentinel of
   ``iv: 0.0`` on an illiquid strike means "no IV", so map it to ``None``; the model rejects a
   non-positive IV precisely so this cannot be passed through by accident.

What a provider must NOT do
---------------------------

No Greeks recomputation (that is T07), no persistence (T04), no GEX (T08), and no filtering
of the chain: return every contract the vendor lists and let the engine apply expiry and
strike filters. A provider that silently drops contracts makes net GEX quietly wrong.

Error handling
--------------

Raise :class:`ProviderError` (or a subclass) for anything the caller might reasonably retry
or route around. The scheduler in T05 is required to survive provider failures, so it catches
this type; letting an ``httpx`` exception escape unwrapped defeats that.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.modules.gex.models.chain import (
    ChainSnapshot,
    OccSymbol,
    OptionContract,
    Right,
    Settlement,
    Underlying,
    parse_occ_symbol,
    settlement_for_root,
    underlying_for_root,
)

__all__ = [
    "ChainSnapshot",
    "OccSymbol",
    "OptionChainProvider",
    "OptionContract",
    "ProviderError",
    "Right",
    "Settlement",
    "SymbolNotSupported",
    "Underlying",
    "UpstreamUnavailable",
    "parse_occ_symbol",
    "settlement_for_root",
    "underlying_for_root",
]


class ProviderError(RuntimeError):
    """Base class for every failure a provider raises. Callers catch this."""


class UpstreamUnavailable(ProviderError):
    """The vendor could not be reached, timed out, rate-limited, or returned garbage.

    Transient by assumption: the caller may retry later. Raise this after the provider's own
    retry budget is exhausted, not on the first failed attempt.
    """


class SymbolNotSupported(ProviderError):
    """The provider cannot serve the requested underlying at all. Retrying will not help."""


class OptionChainProvider(ABC):
    """Fetch a normalized option chain from one data source.

    Implementations are expected to be cheap to construct and safe to reuse across calls; a
    provider that owns an ``httpx.AsyncClient`` should create it lazily and expose a ``close``
    of its own (this interface deliberately does not mandate a lifecycle, since the EOD job
    constructs a provider per run while a streaming provider will not).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable provider identifier, e.g. ``'cboe'``.

        Written to ``ChainSnapshot.source`` and to the snapshot index, so it must not change
        once data has been captured under it. It is also the key the ``PROVIDER`` setting and
        the ``get_provider`` factory (T03) select on.
        """

    @property
    @abstractmethod
    def delayed_minutes(self) -> int:
        """Nominal vendor delay in minutes; ``0`` for real-time.

        Copied onto every :class:`~app.modules.gex.models.chain.ChainSnapshot` this provider returns, and
        surfaced in the UI as a staleness badge. It describes the vendor's *entitlement*
        delay, not observed latency.
        """

    @abstractmethod
    async def fetch_chain(self, underlying: str) -> ChainSnapshot:
        """Fetch and normalize the full option chain for one underlying.

        Args:
            underlying: A canonical symbol — ``'SPX'``, ``'SPY'`` or ``'QQQ'``. Vendor-specific
                mangling (Cboe's ``_SPX`` underscore prefix for indices, for example) is the
                provider's job, not the caller's.

        Returns:
            A snapshot whose ``source`` is :attr:`name`, whose ``delayed_minutes`` is
            :attr:`delayed_minutes`, whose ``captured_at`` is the vendor's effective
            timestamp in UTC, and which contains every contract the vendor listed.

        Raises:
            SymbolNotSupported: the provider cannot serve this underlying.
            UpstreamUnavailable: the vendor failed after the provider's retries.
        """

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} delayed_minutes={self.delayed_minutes}>"
