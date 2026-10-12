"""Log intraday flip crosses forward, then score them once their session is over (T142).

Two steps, both run by the `gex-capture` worker, never by the API (invariant 7):

* :func:`record_flip_crosses` runs after every 5-minute bar poll. It re-reads **today's**
  session only -- bars and in-session captures for the five symbols -- runs the frozen
  detector in `app.modules.gex.scan.flip_cross`, and inserts crosses it has not stored yet.
  Re-reading the whole day makes a missed poll cost nothing. Only today is ever read, so the
  record cannot reach back into sessions before it was deployed (no backfill, by construction).
* :func:`score_flip_crosses` runs after the close. For each event with no score yet whose
  session has ended, it writes one score row: the desk symbol's 15/30/60-minute and close
  returns, and the excursion path on the CFD's 1-minute bars. If the CFD's bars have not
  arrived it waits, and after `PATH_WAIT` it scores without the path and says why.

Nothing here raises for an ordinary failure: one symbol's bad read must never stop the others,
and the poll this hangs off must never fail because of it.

**One known imprecision.** A bar is used once its bucket has closed (`ts + 5 min <= now`), and
the vendor may still adjust that bucket's close on the next poll. The event keeps what was
seen first: that is what a live trader would have seen too.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_session_factory
from app.modules.broker.basis import PAIRS
from app.modules.broker.tables import Bar as BrokerBar
from app.modules.gex.models.db import (
    FlipCrossEvent,
    FlipCrossScore,
    GexLevel,
    IntradayBar,
    Snapshot,
)
from app.modules.gex.scan.flip_cross import (
    BAR,
    FlipCross,
    detect_crosses,
    direction_of,
    excursion_path,
    horizon_returns,
    minute_points,
    path_unit,
    side_at,
)

__all__ = [
    "CFD_OF",
    "ENTRY_WINDOW",
    "PATH_WAIT",
    "SYMBOLS",
    "record_flip_crosses",
    "score_flip_crosses",
]

logger = logging.getLogger("app.modules.gex.jobs.flip_crosses")

_NY = ZoneInfo("America/New_York")

#: The spec's symbols: every desk symbol with both 5-minute bars and in-session captures.
SYMBOLS: tuple[str, ...] = ("SPX", "SPY", "QQQ", "GLD", "DIA")

#: Desk symbol -> the CFD it is traded through. Taken from the broker module's basis pairs, so
#: the mapping the levels are translated with is the one the path is measured on.
CFD_OF: dict[str, str] = {p.desk: p.cfd for p in PAIRS}

#: Entries the spec allows, NY time: a 60-minute hold must finish before the close.
ENTRY_WINDOW = (dt.time(9, 45), dt.time(15, 0))

_SESSION_OPEN = dt.time(9, 30)
_SESSION_CLOSE = dt.time(16, 0)
_INTERVAL = "5m"

#: How long to wait for the CFD's 1-minute bars before scoring without the path.
PATH_WAIT = dt.timedelta(days=3)

#: Trailing calendar days of CFD minutes the swing unit is measured over (about 15 sessions).
_UNIT_LOOKBACK = dt.timedelta(days=21)


@dataclass(frozen=True, slots=True)
class _Capture:
    captured_at: dt.datetime
    flip: float
    net_gex: float | None


def _session_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    open_ = dt.datetime.combine(day, _SESSION_OPEN, tzinfo=_NY).astimezone(dt.UTC)
    close = dt.datetime.combine(day, _SESSION_CLOSE, tzinfo=_NY).astimezone(dt.UTC)
    return open_, close


def _bars(session: Session, symbol: str, day: dt.date, *, closed_by: dt.datetime | None = None):
    open_, close = _session_bounds(day)
    rows = session.execute(
        select(IntradayBar).where(
            IntradayBar.symbol == symbol,
            IntradayBar.interval == _INTERVAL,
            IntradayBar.ts >= open_,
            IntradayBar.ts < close,
        ).order_by(IntradayBar.ts)
    ).scalars().all()
    if closed_by is not None:
        rows = [b for b in rows if b.ts + BAR <= closed_by]
    return rows


def _captures(session: Session, symbol: str, day: dt.date) -> list[_Capture]:
    """`symbol`'s in-session captures for `day`, with a flip, oldest first."""
    open_, close = _session_bounds(day)
    rows = session.execute(
        select(Snapshot.captured_at, GexLevel.flip_point, GexLevel.net_gex)
        .join(GexLevel, GexLevel.snapshot_id == Snapshot.id)
        .where(
            Snapshot.underlying == symbol,
            Snapshot.is_eod.is_(False),
            Snapshot.session_date == day,
            Snapshot.captured_at >= open_,
            Snapshot.captured_at <= close,
            GexLevel.filter == "ALL",
            GexLevel.flip_point.is_not(None),
        )
        .order_by(Snapshot.captured_at)
    ).all()
    return [_Capture(r.captured_at, r.flip_point, r.net_gex) for r in rows]


def _in_window(cross: FlipCross) -> bool:
    t = cross.entry_at.astimezone(_NY).time()
    return ENTRY_WINDOW[0] <= t <= ENTRY_WINDOW[1]


def record_flip_crosses(
    *,
    now: dt.datetime | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[FlipCrossEvent]:
    """Detect today's crosses from the closed bars so far and insert the new ones. Never raises."""
    now = now or dt.datetime.now(dt.UTC)
    day = now.astimezone(_NY).date()
    factory = session_factory or get_session_factory()
    inserted: list[FlipCrossEvent] = []
    try:
        with factory() as session:
            spx_bars = {b.ts: b for b in _bars(session, "SPX", day, closed_by=now)}
            spx_caps = _captures(session, "SPX", day)
            existing = set(session.execute(
                select(FlipCrossEvent.symbol, FlipCrossEvent.bar_ts)
                .where(FlipCrossEvent.session_date == day)
            ).all())
            for symbol in SYMBOLS:
                try:
                    crosses = detect_crosses(
                        symbol, _bars(session, symbol, day, closed_by=now), _captures(session, symbol, day)
                    )
                except Exception:
                    logger.exception("flip_crosses: detection failed for %s", symbol)
                    continue
                for c in crosses:
                    if (symbol, c.bar_ts) in existing:
                        continue
                    spx_bar = spx_bars.get(c.bar_ts)
                    spx_cap = [k for k in spx_caps if k.captured_at <= c.entry_at]
                    row = FlipCrossEvent(
                        symbol=symbol, bar_ts=c.bar_ts, session_date=day, entry_at=c.entry_at,
                        key=c.key, spot=c.spot, flip=c.flip, net_gex=c.net_gex,
                        capture_at=c.capture_at,
                        spx_flip=spx_cap[-1].flip if spx_cap else None,
                        spx_side=side_at(spx_bar, spx_caps) if spx_bar is not None else None,
                        in_window=_in_window(c), recorded_at=now,
                    )
                    session.add(row)
                    existing.add((symbol, c.bar_ts))
                    inserted.append(row)
            session.commit()
    except Exception:
        logger.exception("flip_crosses: record failed")
        return []
    for row in inserted:
        logger.info(json.dumps({
            "event": "flip_cross", "symbol": row.symbol, "key": row.key,
            "entry_at": row.entry_at.isoformat(), "spot": row.spot, "flip": row.flip,
            "in_window": row.in_window,
        }))
    return inserted


def _cfd_minutes(session: Session, cfd: str, start: dt.datetime, end: dt.datetime) -> Sequence[BrokerBar]:
    return session.execute(
        select(BrokerBar).where(
            BrokerBar.symbol == cfd, BrokerBar.timeframe == "M1",
            BrokerBar.ts >= start, BrokerBar.ts < end,
        ).order_by(BrokerBar.ts)
    ).scalars().all()


def _unit(session: Session, cfd: str, day: dt.date, cache: dict) -> float | None:
    """Half the CFD's median regular-session 5-minute range over the sessions before `day`."""
    if (cfd, day) not in cache:
        start = dt.datetime.combine(day - _UNIT_LOOKBACK, dt.time.min, tzinfo=_NY).astimezone(dt.UTC)
        end = _session_bounds(day)[0]
        rth = [
            b for b in _cfd_minutes(session, cfd, start, end)
            if _SESSION_OPEN <= b.ts.astimezone(_NY).time() < _SESSION_CLOSE
        ]
        cache[(cfd, day)] = path_unit(rth)
    return cache[(cfd, day)]


def _path_fields(session: Session, ev: FlipCrossEvent, now: dt.datetime, cache: dict) -> dict | None:
    """The `path_*` columns for `ev`, or `None` to mean "not yet, try again later"."""
    cfd = CFD_OF.get(ev.symbol)
    if cfd is None:
        return {"cfd_symbol": None, "path_note": f"no CFD mapped for {ev.symbol}"}
    _, close = _session_bounds(ev.session_date)
    minutes = _cfd_minutes(session, cfd, ev.entry_at - dt.timedelta(minutes=1), close)
    entry_bar = minutes[0] if minutes and minutes[0].ts == ev.entry_at - dt.timedelta(minutes=1) else None
    path = [b for b in minutes if b.ts >= ev.entry_at]
    complete = entry_bar is not None and path and path[-1].ts >= close - dt.timedelta(minutes=5)
    if not complete:
        if now - close < PATH_WAIT:
            return None
        why = "no CFD bar at entry" if entry_bar is None else "CFD bars stop before the close"
        return {"cfd_symbol": cfd, "path_note": why}
    unit = _unit(session, cfd, ev.session_date, cache)
    if unit is None:
        return {"cfd_symbol": cfd, "path_entry": entry_bar.close, "path_note": "no CFD history for the swing unit"}
    exc = excursion_path(
        minute_points(path, entry_bar.close, direction_of(ev.key), ev.entry_at), unit
    )
    if exc is None:  # unreachable with a non-empty path; kept so a null never reads as a zero
        return {"cfd_symbol": cfd, "path_note": "empty path"}
    return {
        "cfd_symbol": cfd, "path_entry": entry_bar.close, "path_unit": unit,
        "path_swings": json.dumps([[round(t, 1), round(x, 4)] for t, x in exc.swings]),
        "path_mae": exc.mae, "path_mfe": exc.mfe, "path_close": exc.close,
        "path_end_at": path[-1].ts + dt.timedelta(minutes=1),
    }


def score_flip_crosses(
    *,
    now: dt.datetime | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[FlipCrossScore]:
    """Write one score row per event whose session has ended. Never raises."""
    now = now or dt.datetime.now(dt.UTC)
    factory = session_factory or get_session_factory()
    written: list[FlipCrossScore] = []
    unit_cache: dict = {}
    try:
        with factory() as session:
            pending = session.execute(
                select(FlipCrossEvent).where(~exists().where(and_(
                    FlipCrossScore.symbol == FlipCrossEvent.symbol,
                    FlipCrossScore.bar_ts == FlipCrossEvent.bar_ts,
                ))).order_by(FlipCrossEvent.bar_ts)
            ).scalars().all()
            for ev in pending:
                if now < _session_bounds(ev.session_date)[1] + BAR:
                    continue  # the session's last bar has not closed yet
                try:
                    bars = _bars(session, ev.symbol, ev.session_date)
                    cross = FlipCross(
                        symbol=ev.symbol, bar_ts=ev.bar_ts, key=ev.key, spot=ev.spot,
                        flip=ev.flip, net_gex=ev.net_gex, capture_at=ev.capture_at, index=-1,
                    )
                    rets, r_close = horizon_returns(cross, bars)
                    path = _path_fields(session, ev, now, unit_cache)
                except Exception:
                    logger.exception("flip_crosses: scoring failed for %s %s", ev.symbol, ev.bar_ts)
                    continue
                if path is None:
                    continue
                row = FlipCrossScore(
                    symbol=ev.symbol, bar_ts=ev.bar_ts,
                    ret_15m_bps=rets[15], ret_30m_bps=rets[30], ret_60m_bps=rets[60],
                    ret_close_bps=r_close, scored_at=now, **path,
                )
                session.add(row)
                written.append(row)
            session.commit()
    except Exception:
        logger.exception("flip_crosses: scoring run failed")
        return []
    logger.info(json.dumps({"event": "flip_cross_scores", "written": len(written)}))
    return written
