"""Is the executor there when a leg is due? (T174) -- read-only; `capture-watch` runs it.

From 2026-10-09 to 10-15 the homeserver never ran the executor (it is behind the `broker`
compose profile and `.env` had no `COMPOSE_PROFILES`), and nothing said so. Every gold-asia-drift
entry that week went unsent. The executor cannot report its own absence, so this check lives in
another container and reads two things:

* `broker.executor_heartbeat`, overwritten by every completed tick (every 20 s). A heartbeat
  missing or older than :data:`STALE_AFTER` while any enabled strategy has a leg in its window
  is an outage. The window opens :data:`LEAD` before the leg is due, so the alert lands before
  an 18:00 entry, not after it. A heartbeat that never existed counts as missing.
* `broker.order_intents` rows journalled `expired`: a leg the executor saw too late. That is a
  missed trade whatever the cause, including the executor coming back after an outage.

The legs come from the strategies themselves (`Strategy.legs`), not from the journal, because a
journal is only written by an executor that is running.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.broker.strategies import NY, Leg
from app.modules.broker.tables import ExecutorHeartbeat, OrderIntent

#: Ticks are every 20 s; five minutes is fifteen missed ticks, past any restart or slow bridge.
STALE_AFTER = dt.timedelta(minutes=5)
#: How far ahead of a due leg the executor must already be beating.
LEAD = dt.timedelta(minutes=30)
#: Expired legs this recent are reported. The executor marks a leg expired on its first tick
#: after the window, which after an outage can be a day late, hence a day and a half.
EXPIRED_LOOKBACK = dt.timedelta(hours=36)


@dataclass(frozen=True, slots=True)
class ExpiredLeg:
    key: str
    due_at: dt.datetime
    note: str | None


@dataclass(frozen=True, slots=True)
class ExecutorReport:
    now: dt.datetime
    #: Legs whose window (due - LEAD .. expires) contains `now`.
    watched: list[Leg]
    beat_at: dt.datetime | None
    expired: list[ExpiredLeg] = field(default_factory=list)

    @property
    def stale(self) -> bool:
        return self.beat_at is None or self.now - self.beat_at > STALE_AFTER

    @property
    def is_outage(self) -> bool:
        return bool(self.watched) and self.stale

    def summary(self) -> str:
        last = ("never" if self.beat_at is None
                else f"{self.beat_at.astimezone(NY):%Y-%m-%d %H:%M} New York")
        legs = ", ".join(f"{leg.key} due {leg.due_at.astimezone(NY):%H:%M}" for leg in self.watched)
        return (f"EXECUTOR NOT RUNNING: no heartbeat since {last}, with legs due: {legs}. "
                "Check `docker compose ps executor` on the homeserver.")


def legs_in_window(strategies: list, now: dt.datetime) -> list[Leg]:
    """Every leg of yesterday's and today's New York session whose window contains `now`.
    Yesterday's, because a close falls on the session's next morning."""
    today = now.astimezone(NY).date()
    return sorted(
        (leg for strat in strategies for day in (today - dt.timedelta(days=1), today)
         for leg in strat.legs(day) if leg.due_at - LEAD <= now <= leg.expires_at),
        key=lambda leg: leg.due_at,
    )


def check_executor(session: Session, strategies: list, now: dt.datetime) -> ExecutorReport:
    beat = session.get(ExecutorHeartbeat, "executor")
    names = [s.name for s in strategies]
    rows = session.scalars(
        select(OrderIntent)
        .where(OrderIntent.status == "expired", OrderIntent.strategy.in_(names),
               OrderIntent.expires_at >= now - EXPIRED_LOOKBACK)
        .order_by(OrderIntent.due_at)
    ).all()
    return ExecutorReport(
        now=now,
        watched=legs_in_window(strategies, now),
        beat_at=beat.beat_at if beat is not None else None,
        expired=[ExpiredLeg(r.key, r.due_at, r.note) for r in rows],
    )
