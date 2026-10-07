"""The dealer-gamma read for one underlying as of one moment (T138).

Recorded beside every live price signal (`research.signal_events`) as **context, not a
filter**, so the forward record can later answer the question the user asked: do dip-buys taken
while dealers are long gamma do better than ones taken while they are short?

**Point-in-time.** The read is the latest `ALL`-filter `gex_levels` row whose snapshot was
captured at or before `as_of`, never the latest one overall, so a signal recorded late after a
gap is annotated with what was knowable at its bar and not afterwards.

**A passed monthly opex voids the read rather than ageing it** (the desk's rule; see the
market-research skill). The third Friday expires the near walls, so a capture whose session is
on or before an opex that has since passed describes a book that no longer exists. The row then
carries `note` and no numbers -- unknown, never stale numbers presented as current. Weekly and
daily expiries are not treated this way, matching the desk's rule.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.db import GexLevel, Snapshot

__all__ = ["MAX_AGE_DAYS", "GammaContext", "gamma_context", "monthly_opex"]

#: A capture older than this many calendar days is not used: a long weekend is three days, and
#: anything past a working week is an outage, not a read.
MAX_AGE_DAYS = 5

_session_factory: sessionmaker[Session] | None = None


def _factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = get_sessionmaker(get_engine())
    return _session_factory


def monthly_opex(day: dt.date) -> dt.date:
    """The third Friday of `day`'s month (the rule `gex.gex.engine` uses for monthly expiry).
    An exchange holiday on that Friday moves expiry to Thursday; ignoring that only ever makes
    the void check fire a day later, never earlier."""
    first = day.replace(day=1)
    return first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)


def _opex_between(after: dt.date, until: dt.date) -> dt.date | None:
    """A monthly opex `d` with `after <= d < until`, if any: the capture on `after` still held
    the contracts, and a session on `until` no longer does."""
    month = after.replace(day=1)
    while month <= until:
        d = monthly_opex(month)
        if after <= d < until:
            return d
        month = (month + dt.timedelta(days=32)).replace(day=1)
    return None


@dataclass(frozen=True, slots=True)
class GammaContext:
    """What the desk knew about `proxy`'s dealer gamma at `as_of`. Numbers are `None` whenever
    `note` is set -- unknown is never written as zero."""

    proxy: str
    session_date: dt.date | None = None
    net_gex: float | None = None
    spot: float | None = None
    flip_point: float | None = None
    note: str | None = None

    @property
    def known(self) -> bool:
        return self.note is None and self.net_gex is not None

    def describe(self) -> str:
        """One line for a digest."""
        if not self.known:
            return f"dealer gamma ({self.proxy}): unknown, {self.note or 'no level stored'}"
        state = "LONG (dampens moves)" if self.net_gex > 0 else "SHORT (amplifies moves)"
        where = (
            "no flip point" if self.flip_point is None or self.spot is None
            else f"spot {self.spot:g} {'above' if self.spot >= self.flip_point else 'below'} flip {self.flip_point:g}"
        )
        return f"dealer gamma ({self.proxy}, {self.session_date:%m-%d} capture): {state}, {where}"


def gamma_context(
    proxy: str,
    as_of: dt.datetime,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> GammaContext:
    """`proxy`'s latest ALL-filter levels captured at or before `as_of`. Never raises on
    missing data: no capture, an old one and a voided one are all answers, each with a note."""
    factory = session_factory or _factory()
    with factory() as session:
        row = session.execute(
            select(Snapshot.session_date, Snapshot.captured_at, GexLevel.net_gex, GexLevel.spot, GexLevel.flip_point)
            .join(GexLevel, GexLevel.snapshot_id == Snapshot.id)
            .where(Snapshot.underlying == proxy, GexLevel.filter == "ALL", Snapshot.captured_at <= as_of)
            .order_by(Snapshot.captured_at.desc())
            .limit(1)
        ).first()
    if row is None:
        return GammaContext(proxy, note="no capture before this bar")
    session_day = row.session_date or row.captured_at.date()
    bar_day = as_of.astimezone(ZoneInfo(settings.TZ)).date()  # the exchange's date, like session_date
    if (bar_day - session_day).days > MAX_AGE_DAYS:
        return GammaContext(proxy, session_date=session_day, note=f"last capture {session_day} is too old")
    voided_by = _opex_between(session_day, bar_day)
    if voided_by is not None:
        return GammaContext(
            proxy, session_date=session_day,
            note=f"monthly opex {voided_by} passed since the {session_day} capture",
        )
    return GammaContext(proxy, session_day, row.net_gex, row.spot, row.flip_point)
