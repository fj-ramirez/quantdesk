"""Detect a capture outage: sessions that traded and produced nothing (T104).

September quarterly opex week — 2026-09-14 to 09-18, five open sessions, the whole 28-symbol
universe — is simply missing from `gex.snapshots`, and nothing said so for twelve days. QQQ's
gamma regime flipped from −2.17bn to +4.80bn entirely inside that gap. Both endpoints exist;
the path does not.

`GET /api/gex/health/capture` already computed per-symbol staleness, and `api/health.py` even
logged a warning about it — **on request only**. The detection was built; the watching was not.
This module is the watching.

**It alerts on the universe going silent, never on one symbol being late.** That distinction
decides whether the alert survives its first month. `scan/regime.STALE_THRESHOLD_MINUTES`
documents measured per-symbol lags — XBI 4h21m, XLC 2h27m, GDX 27m — so a single stale symbol
is ordinary and an alert that fires on it gets muted, after which the next real outage is
silent again for a better reason than last time.

**It is keyed on `session_date`, not `captured_at`** (T102). "Did this session produce
anything" is a question about the session a chain belongs to, and a weekend capture carries the
previous session's book — so counting by capture date both invents Sunday sessions and misses
the fact that Friday was covered. The column exists precisely so this query can be written
correctly in SQL rather than approximated in prose.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.gex.jobs.calendar import is_trading_day
from app.modules.gex.models.db import Snapshot

__all__ = ["OutageReport", "check_capture_outage", "recent_trading_sessions"]

logger = logging.getLogger("app.modules.gex.jobs.outage")

#: How many completed trading sessions to look back over. Two weeks of sessions is enough to
#: describe the September gap (five) with room around it, and short enough that the query stays
#: an index scan.
LOOKBACK_SESSIONS = 10

#: How many symbols a session must have captured before it counts as covered. **Not the full
#: universe**: one symbol failing while 27 succeed is a per-symbol problem, which this module
#: deliberately does not alert on. A session with nothing at all is the alertable event, and a
#: session with a handful is the staggered partial recovery the September outage ended with
#: (3 symbols Saturday, 25 Sunday, 28 Monday) — worth flagging as degraded, not as an outage.
MIN_SYMBOLS_FOR_COVERED = 1


@dataclass(frozen=True, slots=True)
class OutageReport:
    """What the last `LOOKBACK_SESSIONS` completed sessions actually produced.

    `missing` is the alertable fact. `degraded` is reported separately rather than folded in:
    a session that captured 3 of 28 symbols is not silent, and calling it an outage would be
    the same overreach as alerting on one stale symbol.
    """

    checked: tuple[dt.date, ...]
    missing: tuple[dt.date, ...]
    degraded: tuple[tuple[dt.date, int], ...]
    covered: tuple[tuple[dt.date, int], ...]

    @property
    def is_outage(self) -> bool:
        return bool(self.missing)

    def summary(self) -> str:
        """One human line, for a log or a phone."""
        if not self.checked:
            return "No completed trading sessions in range — nothing to check."
        if self.is_outage:
            days = ", ".join(d.isoformat() for d in self.missing)
            plural = "s" if len(self.missing) > 1 else ""
            return (
                f"CAPTURE OUTAGE: {len(self.missing)} trading session{plural} with no snapshots "
                f"at all ({days}). Options open interest cannot be backfilled — a capture that "
                f"did not happen is gone permanently."
            )
        if self.degraded:
            worst = min(self.degraded, key=lambda pair: pair[1])
            return (
                f"Capture degraded: {len(self.degraded)} of {len(self.checked)} sessions "
                f"captured few symbols (fewest: {worst[1]} on {worst[0].isoformat()})."
            )
        return (
            f"Capture healthy: all {len(self.checked)} completed sessions since "
            f"{self.checked[-1].isoformat()} produced snapshots."
        )


def recent_trading_sessions(today: dt.date, *, count: int = LOOKBACK_SESSIONS) -> list[dt.date]:
    """The `count` most recent trading days strictly before `today`, newest first.

    Strictly before: a session still in progress has not had its chance to produce a full day
    of captures, and alerting on it at 09:31 would fire every single morning.
    """
    sessions: list[dt.date] = []
    day = today - dt.timedelta(days=1)
    guard = 0
    while len(sessions) < count and guard < count * 10:
        if is_trading_day(day):
            sessions.append(day)
        day -= dt.timedelta(days=1)
        guard += 1
    return sessions


def check_capture_outage(
    session: Session,
    *,
    today: dt.date,
    expected_symbols: int = MIN_SYMBOLS_FOR_COVERED,
    lookback: int = LOOKBACK_SESSIONS,
) -> OutageReport:
    """Compare the sessions that traded against the sessions that produced snapshots."""
    sessions = recent_trading_sessions(today, count=lookback)
    if not sessions:
        return OutageReport(checked=(), missing=(), degraded=(), covered=())

    rows = session.execute(
        select(Snapshot.session_date, func.count(func.distinct(Snapshot.underlying)))
        .where(Snapshot.session_date.in_(sessions))
        .group_by(Snapshot.session_date)
    ).all()
    counts = {day: int(n) for day, n in rows if day is not None}

    missing, degraded, covered = [], [], []
    for day in sessions:
        n = counts.get(day, 0)
        if n == 0:
            missing.append(day)
        elif n < expected_symbols:
            degraded.append((day, n))
        else:
            covered.append((day, n))

    report = OutageReport(
        checked=tuple(sessions),
        missing=tuple(missing),
        degraded=tuple(degraded),
        covered=tuple(covered),
    )
    log = logger.error if report.is_outage else logger.info
    log("capture_outage_check: %s", report.summary())
    return report
