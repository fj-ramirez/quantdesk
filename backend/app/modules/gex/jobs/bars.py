"""Daily-bars update job (T42, plans/continuation/00-foundation-daily-bars.md).

Mirrors `app.modules.gex.jobs.capture`'s shape deliberately: one call per symbol funnels through a
`BarUpdateResult` rather than an exception, because the same P0 guardrail applies here as to
the EOD option capture -- one symbol's failure (Yahoo throttling, a delisted ETF, a transient
network blip) must never stop the other ~80 symbols in `SCAN_UNIVERSE`, and this job's own
top-level failure must never be capable of reaching, delaying or crashing the option capture
job it shares a process with. See `app.modules.gex.jobs.scheduler`'s `update_bars_job` registration for how
that second guarantee is enforced at the scheduler level (a separate 17:30 ET job, own
try/except, same misfire/coalesce policy).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.modules.gex.providers.bars import BarProviderRegistry, ProviderError
from app.modules.gex.storage.bars_repository import last_bar_date, upsert_bars

__all__ = ["BarUpdateResult", "update_bars_job", "update_one_symbol"]

logger = logging.getLogger("app.modules.gex.jobs.bars")

# Incremental-fetch overlap: re-fetch the last 5 stored days on every run to pick up vendor
# revisions (plan design decision, "Incremental by default"), even though `upsert_bars` is a
# no-op for a day whose values didn't actually change.
_OVERLAP_DAYS = 5

# First-ever fetch for a symbol with no stored bars at all -- keeps a symbol newly added to
# `SCAN_UNIVERSE` from silently sitting at zero history until someone happens to run the CLI's
# larger --years window by hand.
_DEFAULT_LOOKBACK_YEARS = 5


@dataclass(frozen=True, slots=True)
class BarUpdateResult:
    """Outcome of one `update_one_symbol` call -- also the shape of its JSON log line."""

    symbol: str
    ok: bool
    inserted: int = 0
    updated: int = 0
    error: str | None = None


def _log_result(result: BarUpdateResult) -> None:
    level = logging.INFO if result.ok else logging.ERROR
    logger.log(level, json.dumps({"event": "bars_update", **asdict(result)}, default=str))


async def update_one_symbol(
    symbol: str,
    *,
    registry: BarProviderRegistry,
    session_factory: sessionmaker[Session] | None = None,
) -> BarUpdateResult:
    """Fetch and upsert bars for one symbol. Never raises.

    Incremental by default (plan design decision): fetches from `last_bar_date(symbol) - 5
    days`, or `_DEFAULT_LOOKBACK_YEARS` back when the symbol has no stored bars at all.
    `registry.for_symbol` resolves the provider per call so `BAR_PROVIDER_GROUPS` routing
    (T54's Cboe index-history provider for `^VIX`-shaped series, say) takes effect per symbol,
    while the registry itself caches provider instances across the whole loop -- see
    `BarProviderRegistry`'s own docstring.
    """
    try:
        last = last_bar_date(symbol, session_factory=session_factory)
        if last is None:
            start = dt.datetime.now(dt.UTC).date() - dt.timedelta(
                days=365 * _DEFAULT_LOOKBACK_YEARS
            )
        else:
            start = last - dt.timedelta(days=_OVERLAP_DAYS)

        provider = registry.for_symbol(symbol)
        bars = await provider.fetch_daily_bars(symbol, start=start)
        upsert_result = upsert_bars(bars, session_factory=session_factory)
    except ProviderError as exc:
        result = BarUpdateResult(symbol=symbol, ok=False, error=str(exc))
        _log_result(result)
        return result
    except Exception as exc:
        # Same rationale as app.modules.gex.jobs.capture.capture_snapshot's identical catch: a provider is
        # *supposed* to wrap every failure in a ProviderError, but a bug or an unanticipated
        # vendor payload can still leak something else. Letting that escape here would abort
        # `update_bars_job` mid-loop, silently freezing every symbol after the failing one at
        # its last stored date until the next run happens to succeed all the way through.
        logger.exception("bars: %s update raised a non-ProviderError", symbol)
        result = BarUpdateResult(symbol=symbol, ok=False, error=f"{type(exc).__name__}: {exc}")
        _log_result(result)
        return result

    result = BarUpdateResult(
        symbol=symbol, ok=True, inserted=upsert_result.inserted, updated=upsert_result.updated
    )
    _log_result(result)
    return result


async def update_bars_job(
    symbols: Sequence[str] | None = None,
    *,
    registry: BarProviderRegistry | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[BarUpdateResult]:
    """Update every symbol in `symbols` (default `settings.scan_universe`).

    One symbol's failure never stops the others -- see `update_one_symbol`'s docstring -- and a
    single summary line is logged once every symbol has been attempted, so a partial outage
    (Yahoo throttling a handful of the ~80 symbols, say) is visible as one line rather than
    only as N separate per-symbol error logs a human has to count.

    Args:
        symbols: Defaults to `settings.scan_universe`. Tests pass a short explicit list.
        registry: Defaults to a fresh `BarProviderRegistry()` built from settings, closed at
            the end of this call. A caller that passes its own registry keeps ownership of
            closing it (same convention as `app.modules.gex.providers.base.OptionChainProvider`).
        session_factory: Defaults to `app.modules.gex.storage.bars_repository.get_session_factory` (real
            Postgres) via the repository functions this delegates to. Tests pass a SQLite
            factory so the whole job runs fully offline.
    """
    active_symbols = symbols if symbols is not None else settings.scan_universe
    owns_registry = registry is None
    active_registry = registry if registry is not None else BarProviderRegistry()

    results: list[BarUpdateResult] = []
    try:
        for symbol in active_symbols:
            results.append(
                await update_one_symbol(
                    symbol, registry=active_registry, session_factory=session_factory
                )
            )
    finally:
        if owns_registry:
            await active_registry.aclose()

    ok = sum(1 for r in results if r.ok)
    failed = len(results) - ok
    logger.info(
        json.dumps(
            {
                "event": "bars_update_summary",
                "total": len(results),
                "ok": ok,
                "failed": failed,
                "failed_symbols": [r.symbol for r in results if not r.ok],
            }
        )
    )
    return results
