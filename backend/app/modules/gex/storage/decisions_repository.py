"""`decisions` table read/write access (T61).

Plain module-level functions with an optional `session_factory`, the same DI idiom as
`app.modules.gex.storage.bars_repository`: production callers take the cached sessionmaker bound to
`settings.DATABASE_URL`, tests inject a temp-SQLite factory and run fully offline.

Every reader returns frozen `DecisionRecord` dataclasses built *inside* the session, never
ORM instances -- callers get plain values that cannot raise a detached-instance error after
the session closes, and the API serializes them with `to_dict()` without touching SQLAlchemy.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.db import Decision
from app.modules.gex.scan.decisions import DecisionResult, Opportunity
from app.modules.gex.scan.outcomes import Outcome, TradeSpec

__all__ = [
    "DecisionRecord",
    "apply_outcome",
    "decided_on_for",
    "get_session_factory",
    "read_history",
    "record_decisions",
    "unresolved",
]

_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    """Cached, lazily-created sessionmaker bound to `settings.DATABASE_URL`. Tests should not
    use this -- pass an explicit `session_factory` to every function in this module instead."""
    global _session_factory
    if _session_factory is None:
        _session_factory = get_sessionmaker(get_engine())
    return _session_factory


def decided_on_for(effective_at: dt.datetime) -> dt.date:
    """The trading date a chain describes: its effective instant in the exchange's own zone.
    A 16:20 ET capture is 20:20 UTC, still the same New York date; a UTC date would roll
    over at 20:00 ET and mislabel every capture after that as tomorrow's."""
    return effective_at.astimezone(ZoneInfo(settings.TZ)).date()


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One `decisions` row as plain values. `opportunity` is the stored payload, parsed."""

    id: int
    underlying: str
    filter: str
    snapshot_id: int
    key: str
    decided_on: dt.date
    as_of: dt.datetime
    setup: str
    side: str
    status: str
    score: int
    grade: str
    entry: float
    stop: float
    target: float
    target_2: float | None
    spot: float
    atr14: float | None
    outcome: str
    fill: float | None
    triggered_on: dt.date | None
    resolved_on: dt.date | None
    bars_held: int | None
    mfe_r: float | None
    mae_r: float | None
    result_r: float | None
    mark_r: float | None
    evaluated_through: dt.date | None
    outcome_note: str | None
    opportunity: dict[str, Any]

    @property
    def spec(self) -> TradeSpec:
        return TradeSpec(setup=self.setup, side=self.side, entry=self.entry, stop=self.stop, target=self.target)

    def to_dict(self) -> dict[str, Any]:
        def d(value: dt.date | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "id": self.id,
            "underlying": self.underlying,
            "filter": self.filter,
            "snapshot_id": self.snapshot_id,
            "key": self.key,
            "decided_on": self.decided_on.isoformat(),
            "as_of": self.as_of.isoformat(),
            "setup": self.setup,
            "side": self.side,
            "status": self.status,
            "score": self.score,
            "grade": self.grade,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "target_2": self.target_2,
            "spot": self.spot,
            "atr14": self.atr14,
            "outcome": self.outcome,
            "fill": self.fill,
            "triggered_on": d(self.triggered_on),
            "resolved_on": d(self.resolved_on),
            "bars_held": self.bars_held,
            "mfe_r": self.mfe_r,
            "mae_r": self.mae_r,
            "result_r": self.result_r,
            "mark_r": self.mark_r,
            "evaluated_through": d(self.evaluated_through),
            "outcome_note": self.outcome_note,
            "opportunity": self.opportunity,
        }


def _record(row: Decision) -> DecisionRecord:
    return DecisionRecord(
        id=row.id,
        underlying=row.underlying,
        filter=row.filter,
        snapshot_id=row.snapshot_id,
        key=row.key,
        decided_on=row.decided_on,
        as_of=row.as_of,
        setup=row.setup,
        side=row.side,
        status=row.status,
        score=row.score,
        grade=row.grade,
        entry=row.entry,
        stop=row.stop,
        target=row.target,
        target_2=row.target_2,
        spot=row.spot,
        atr14=row.atr14,
        outcome=row.outcome,
        fill=row.fill,
        triggered_on=row.triggered_on,
        resolved_on=row.resolved_on,
        bars_held=row.bars_held,
        mfe_r=row.mfe_r,
        mae_r=row.mae_r,
        result_r=row.result_r,
        mark_r=row.mark_r,
        evaluated_through=row.evaluated_through,
        outcome_note=row.outcome_note,
        opportunity=json.loads(row.payload),
    )


def _row(result: DecisionResult, snapshot_id: int, opp: Opportunity) -> Decision:
    return Decision(
        underlying=result.underlying,
        filter=result.filter,
        snapshot_id=snapshot_id,
        key=opp.key,
        decided_on=decided_on_for(result.effective_at),
        as_of=result.as_of,
        setup=opp.setup,
        side=opp.side,
        status=opp.status,
        score=opp.score,
        grade=opp.grade,
        entry=opp.entry,
        stop=opp.stop,
        target=opp.target,
        target_2=opp.target_2,
        spot=result.spot,
        atr14=result.atr14,
        payload=json.dumps(opp.to_dict()),
        outcome="pending",
    )


def record_decisions(
    results: Sequence[tuple[DecisionResult, int]],
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> int:
    """Insert every opportunity in `results` (paired with its `snapshots.id`) that is not
    already stored for that `(snapshot_id, filter, key)`. Returns the number inserted.

    Insert-when-unseen rather than upsert, deliberately: a recorded level is a *commitment*
    the track record scores, and rewriting it on a second run (say, after a code change to
    the engine on the same day's snapshot) would quietly re-score history against levels the
    engine never actually suggested at the time.
    """
    factory = session_factory or get_session_factory()
    snapshot_ids = {snapshot_id for _, snapshot_id in results}
    if not snapshot_ids:
        return 0
    inserted = 0
    with factory() as session:
        existing = set(
            session.execute(
                select(Decision.snapshot_id, Decision.filter, Decision.key).where(
                    Decision.snapshot_id.in_(snapshot_ids)
                )
            ).all()
        )
        for result, snapshot_id in results:
            for opp in result.opportunities:
                if (snapshot_id, result.filter, opp.key) in existing:
                    continue
                session.add(_row(result, snapshot_id, opp))
                existing.add((snapshot_id, result.filter, opp.key))
                inserted += 1
        session.commit()
    return inserted


def unresolved(*, session_factory: sessionmaker[Session] | None = None) -> list[DecisionRecord]:
    """Every row still `pending` -- the ones the evaluator has more to say about."""
    factory = session_factory or get_session_factory()
    with factory() as session:
        rows = session.execute(
            select(Decision).where(Decision.outcome == "pending").order_by(Decision.decided_on, Decision.id)
        ).scalars()
        return [_record(r) for r in rows]


def apply_outcome(
    record_id: int, outcome: Outcome, *, session_factory: sessionmaker[Session] | None = None
) -> None:
    factory = session_factory or get_session_factory()
    with factory() as session:
        row = session.get(Decision, record_id)
        if row is None:
            return
        row.outcome = outcome.outcome
        row.fill = outcome.fill
        row.triggered_on = outcome.triggered_on
        row.resolved_on = outcome.resolved_on
        row.bars_held = outcome.bars_held
        row.mfe_r = outcome.mfe_r
        row.mae_r = outcome.mae_r
        row.result_r = outcome.result_r
        row.mark_r = outcome.mark_r
        row.evaluated_through = outcome.evaluated_through
        row.outcome_note = outcome.note
        session.commit()


def read_history(
    *,
    underlying: str | None = None,
    outcome: str | None = None,
    limit: int = 200,
    session_factory: sessionmaker[Session] | None = None,
) -> list[DecisionRecord]:
    """Newest decisions first (by `decided_on`, then score), optionally narrowed."""
    factory = session_factory or get_session_factory()
    stmt = select(Decision)
    if underlying is not None:
        stmt = stmt.where(Decision.underlying == underlying)
    if outcome is not None:
        stmt = stmt.where(Decision.outcome == outcome)
    stmt = stmt.order_by(Decision.decided_on.desc(), Decision.score.desc(), Decision.id).limit(limit)
    with factory() as session:
        return [_record(r) for r in session.execute(stmt).scalars()]
