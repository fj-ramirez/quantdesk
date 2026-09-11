"""Record today's opportunities and score yesterday's (T61).

Two steps, run together once a day at 17:45 ET (after the 17:30 bars job, so today's bar is
in the database when today's decisions are written and yesterday's are scored):

1. **Record.** Run the `/decisions` pipeline (`app.api.scan.build_regime_rows` +
   `app.scan.decisions.decide`) and insert every emitted opportunity through
   `app.storage.decisions_repository.record_decisions`, which is a no-op for a
   `(snapshot, filter, key)` already stored -- so a manual re-run, or the safety-net capture
   producing no new snapshot, never duplicates a row.
2. **Evaluate.** For every `pending` row, read the daily bars strictly after its
   `decided_on` and hand them to `app.scan.outcomes.evaluate`; write whatever it says back.
   A resolved row is never re-evaluated (it is not `pending` any more), so a resolved
   outcome is final the day it is reached.

**Never raises.** Each step is fenced individually and reported in `DecisionsJobResult`
(`errors` names what failed), matching `update_bars_job`'s posture: a bug here must not be
able to take the scheduler down, and the option capture must be structurally unaffected.

The `/decisions` pipeline is synchronous and takes ~10 s across the universe (the IV lookups
-- see `app.api.scan`'s T45 timing note), so it runs in a worker thread via
`asyncio.to_thread`; the scheduler's event loop, and any request being served, stay free.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.api.scan import RegimeBuild, build_regime_rows
from app.gex.engine import ExpiryFilter
from app.scan.breakouts import (
    DEFAULT_K,
    DEFAULT_LOOKBACK,
    DEFAULT_N,
    BreakoutSummary,
    detect_events,
    summarize,
)
from app.scan.decisions import DecisionResult, decide
from app.scan.outcomes import evaluate
from app.storage import decisions_repository as repo
from app.storage.bars_repository import read_bars

__all__ = ["DecisionsJobResult", "breakout_summary_for", "decide_build", "record_decisions_job"]

logger = logging.getLogger("app.jobs.decisions")


def breakout_summary_for(build: RegimeBuild) -> BreakoutSummary | None:
    """`app.scan.breakouts.summarize` over the plan-default window, from the bars the regime
    pipeline already fetched. `None` for a symbol with no bars -- an absent ledger, not an
    empty one, so the engine scores it as unavailable rather than as a 0 % rate."""
    bars = build.bars
    if bars.empty:
        return None
    window = bars.tail(DEFAULT_LOOKBACK)
    cutoff = window["date"].iloc[0]
    events = [e for e in detect_events(bars, DEFAULT_N, DEFAULT_K) if e.date >= cutoff]
    return summarize(events, DEFAULT_LOOKBACK)


def decide_build(build: RegimeBuild) -> DecisionResult:
    """The one way a `RegimeBuild` becomes a `DecisionResult` -- shared by the read API and
    this job so the row the page shows and the row the table records can never differ."""
    assert build.row is not None
    return decide(
        build.row,
        by_strike=build.by_strike,
        trend_composite=build.trend_pct,
        breakouts=breakout_summary_for(build),
    )


@dataclass(frozen=True, slots=True)
class DecisionsJobResult:
    recorded: int
    evaluated: int
    resolved: int
    errors: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "recorded": self.recorded,
            "evaluated": self.evaluated,
            "resolved": self.resolved,
            "errors": list(self.errors),
        }


def _record(filter_: ExpiryFilter, session_factory: sessionmaker[Session] | None) -> int:
    builds = build_regime_rows(filter_)
    pairs = [
        (decide_build(b), b.snapshot_id) for b in builds if b.row is not None and b.snapshot_id is not None
    ]
    return repo.record_decisions(pairs, session_factory=session_factory)


def _evaluate(
    session_factory: sessionmaker[Session] | None, bars_session_factory: sessionmaker[Session] | None
) -> tuple[int, int]:
    evaluated = resolved = 0
    for record in repo.unresolved(session_factory=session_factory):
        bars = read_bars(
            record.underlying,
            start=record.decided_on + dt.timedelta(days=1),
            session_factory=bars_session_factory,
        )
        outcome = evaluate(record.spec, bars)
        repo.apply_outcome(record.id, outcome, session_factory=session_factory)
        evaluated += 1
        if outcome.outcome != "pending":
            resolved += 1
    return evaluated, resolved


async def record_decisions_job(
    *,
    filter_: ExpiryFilter = ExpiryFilter.ALL,
    session_factory: sessionmaker[Session] | None = None,
    bars_session_factory: sessionmaker[Session] | None = None,
) -> DecisionsJobResult:
    """Record today's opportunities, then score every pending row. See the module docstring.

    Args:
        filter_: The persisted expiry filter to record under. `ALL` is the scheduled one;
            the others are available for a manual run but are not scheduled -- one track
            record per filter would triple the rows for a single-user app with no calibration
            data yet.
        session_factory: For the `decisions` table. Defaults to the repository's own cached
            factory (real Postgres). Tests inject SQLite.
        bars_session_factory: For `daily_bars`. Same default/injection story. The `/decisions`
            pipeline itself reads through `app.api.scan`'s module-level factories, which tests
            monkeypatch exactly as `test_scan_api.py` does.
    """
    errors: list[str] = []
    recorded = evaluated = resolved = 0
    try:
        recorded = await asyncio.to_thread(_record, filter_, session_factory)
    except Exception as exc:  # the capture must be structurally unaffected by anything here
        logger.exception("record_decisions_job: recording failed")
        errors.append(f"record: {exc}")
    try:
        evaluated, resolved = await asyncio.to_thread(_evaluate, session_factory, bars_session_factory)
    except Exception as exc:
        logger.exception("record_decisions_job: evaluation failed")
        errors.append(f"evaluate: {exc}")

    result = DecisionsJobResult(recorded, evaluated, resolved, tuple(errors))
    logger.info(json.dumps({"event": "decisions_job_summary", **result.to_dict()}))
    return result
