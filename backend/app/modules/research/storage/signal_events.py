"""`research.signal_events` writes and reads (T135).

Same idiom as `app.modules.gex.storage.decisions_repository`: module-level functions with an
optional `session_factory`, so tests run offline against SQLite and production takes the
cached factory bound to `settings.DATABASE_URL`.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_engine, get_sessionmaker
from app.modules.research.models.db import SignalEvent as SignalEventRow
from app.modules.research.signals import SignalEvent

__all__ = ["get_session_factory", "record_events"]

_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = get_sessionmaker(get_engine())
    return _session_factory


def record_events(
    events: Sequence[SignalEvent],
    *,
    now: dt.datetime | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[SignalEvent]:
    """Insert every event whose key is not stored yet; return exactly those, in input order.

    Insert-when-unseen, never upsert: a run that re-reads its window must not rewrite what an
    earlier run recorded -- in particular not flip an event's `late` flag after the fact.
    """
    if not events:
        return []
    factory = session_factory or get_session_factory()
    stamp = now or dt.datetime.now(dt.UTC)
    keys = {e.key for e in events}
    with factory() as session:
        existing = set(
            session.execute(
                select(
                    SignalEventRow.signal, SignalEventRow.symbol, SignalEventRow.bar_ts, SignalEventRow.action
                ).where(
                    SignalEventRow.signal.in_({k[0] for k in keys}),
                    SignalEventRow.symbol.in_({k[1] for k in keys}),
                )
            ).all()
        )
        inserted: list[SignalEvent] = []
        for e in events:
            if e.key in existing:
                continue
            session.add(SignalEventRow(
                signal=e.signal, symbol=e.symbol, bar_ts=e.bar_ts, action=e.action,
                family=e.family, timeframe=e.timeframe, side=e.side, price=e.price,
                stop=e.stop, target=e.target, reason=e.reason[:256], params=e.params,
                late=e.late, recorded_at=stamp,
            ))
            existing.add(e.key)
            inserted.append(e)
        session.commit()
    return inserted
