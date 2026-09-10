"""ETF shares-outstanding update job (T52, plans/continuation/05-etf-flows.md).

Mirrors `app.jobs.bars`'s shape (one call per unit of work funnels through a result dataclass
rather than an exception, a per-unit try/except so one family's failure never stops another,
a summary log line once every unit has been attempted) at family, not symbol, granularity --
`SpdrAllFundsProvider.fetch` is one HTTP request answering for up to 17 symbols at once, so the
natural unit of "did this succeed" here is the family's `fetch()` call, with per-symbol misses
inside a family surfaced through `SharesOutstandingFetchResult.failures` rather than a second
level of per-symbol result objects this job would have to invent.

**P0 guardrail, same as `app.jobs.bars.bars_update_job`'s own docstring states for itself: this
job must be structurally incapable of affecting the 16:20/20:00 option capture.** It shares a
process with those jobs only through the scheduler; every failure mode here (a family's request
failing, a parse bug, an unexpected exception) is caught and logged, never re-raised past this
module's own top-level functions.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session, sessionmaker

from app.providers.etf_flows import (
    ISharesProductPageProvider,
    ProviderError,
    SharesOutstandingProvider,
    SpdrAllFundsProvider,
)
from app.storage.flows_repository import insert_new_rows

__all__ = ["FlowsFamilyResult", "update_flows_job"]

logger = logging.getLogger("app.jobs.flows")


@dataclass(frozen=True, slots=True)
class FlowsFamilyResult:
    """Outcome of updating one fund family -- also the shape of its JSON log line."""

    family: str
    ok: bool
    inserted: int = 0
    skipped: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    error: str | None = None


def _log_result(result: FlowsFamilyResult) -> None:
    level = logging.INFO if result.ok else logging.ERROR
    logger.log(level, json.dumps({"event": "flows_update", **asdict(result)}, default=str))


def _default_providers() -> list[SharesOutstandingProvider]:
    """The two supported families, per `docs/etf-flows-sources.md`'s survey result. VanEck,
    Invesco and USCF have no provider at all -- see that survey and
    `app.providers.etf_flows.UNSUPPORTED_SYMBOLS` -- so there is nothing to register for them
    here; a caller cannot accidentally "fix" that by asking for one of their symbols, since
    every provider's `fetch` silently ignores a symbol outside its own `symbols`.
    """
    return [SpdrAllFundsProvider(), ISharesProductPageProvider()]


async def update_flows_job(
    symbols: Sequence[str] | None = None,
    *,
    providers: list[SharesOutstandingProvider] | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[FlowsFamilyResult]:
    """Update shares outstanding for every supported family, restricted to `symbols` if given.

    One family's failure never stops another -- see the module docstring -- and a single
    summary line is logged once every family has been attempted.

    Args:
        symbols: Restricts the fetch to these symbols (each routed to whichever provider's
            `symbols` contains it; a symbol no provider covers is simply never fetched, not an
            error -- the unsupported families have no provider to route to at all). Defaults
            to every symbol every default provider covers (`ALL_SUPPORTED_SYMBOLS`).
        providers: Defaults to `_default_providers()` (fresh SPDR + iShares provider
            instances), closed at the end of this call. A caller that passes its own providers
            keeps ownership of closing them (same convention as `app.jobs.bars.update_bars_job`
            and its `registry` parameter).
        session_factory: Defaults to `app.storage.flows_repository.get_session_factory` (real
            Postgres) via `insert_new_rows`. Tests pass a SQLite factory so the whole job runs
            fully offline.

    Returns:
        One `FlowsFamilyResult` per provider in `providers` that had at least one requested
        symbol to fetch (a provider none of whose symbols were requested is skipped entirely,
        not reported as a zero-row success).
    """
    owns_providers = providers is None
    active_providers = providers if providers is not None else _default_providers()

    results: list[FlowsFamilyResult] = []
    try:
        for provider in active_providers:
            requested: list[str] | None = None
            if symbols is not None:
                requested = [s for s in symbols if s in provider.symbols]
                if not requested:
                    continue  # nothing in this request routes to this family

            try:
                fetch_result = await provider.fetch(requested)
            except ProviderError as exc:
                result = FlowsFamilyResult(
                    family=provider.name,
                    ok=False,
                    failed_symbols=list(requested if requested is not None else provider.symbols),
                    error=str(exc),
                )
                results.append(result)
                _log_result(result)
                continue
            except Exception as exc:
                # Same rationale as app.jobs.bars.update_one_symbol's identical catch: a
                # provider is supposed to wrap every failure in a ProviderError, but a bug or
                # an unanticipated vendor payload can still leak something else. Letting that
                # escape here would abort the loop mid-way, leaving every family after the
                # failing one un-attempted this run.
                logger.exception("flows: %s fetch raised a non-ProviderError", provider.name)
                result = FlowsFamilyResult(
                    family=provider.name,
                    ok=False,
                    failed_symbols=list(requested if requested is not None else provider.symbols),
                    error=f"{type(exc).__name__}: {exc}",
                )
                results.append(result)
                _log_result(result)
                continue

            insert_result = insert_new_rows(fetch_result.rows, session_factory=session_factory)
            for failed_symbol, reason in fetch_result.failures.items():
                logger.error("flows: %s (%s) failed: %s", failed_symbol, provider.name, reason)

            result = FlowsFamilyResult(
                family=provider.name,
                ok=True,
                inserted=insert_result.inserted,
                skipped=insert_result.skipped,
                failed_symbols=list(fetch_result.failures),
            )
            results.append(result)
            _log_result(result)
    finally:
        if owns_providers:
            for provider in active_providers:
                await provider.close()

    ok = sum(1 for r in results if r.ok)
    logger.info(
        json.dumps(
            {
                "event": "flows_update_summary",
                "families": len(results),
                "ok": ok,
                "failed": len(results) - ok,
                "failed_families": [r.family for r in results if not r.ok],
            }
        )
    )
    return results
