"""Log flip crosses and wall bounces forward, alert the tradeable ones, and score them once their
session is over (T142, T153).

Two steps, both run by the `gex-capture` worker, never by the API (invariant 7):

* :func:`record_level_events` runs after every 5-minute bar poll. It re-reads **today's**
  session only -- bars and in-session captures for the five symbols -- runs both frozen
  detectors in `app.modules.gex.scan.level_events`, and inserts events it has not stored yet.
  Re-reading the whole day makes a missed poll cost nothing. Only today is ever read, so the
  record cannot reach back into sessions before it was deployed (no backfill, by construction).
  The new events it selects (:func:`select_alerts`) go to Telegram through `app.core.notify`.
* :func:`score_level_events` runs after the close. For each event with no score yet whose
  session has ended, it writes one score row: the desk symbol's 15/30/60-minute and close
  returns, and the excursion path on the CFD's 1-minute bars. If the CFD's bars have not
  arrived it waits, and after `PATH_WAIT` it scores without the path and says why.

**An alert is not a trade instruction.** It names the event, the level and the CFD price, and
says which stage the rule is at. Orders go through the executor only (invariant 11), and neither
rule is in `modules/broker/strategies.py`.

Nothing here raises for an ordinary failure: one symbol's bad read must never stop the others,
and the poll this hangs off must never fail because of it.

**One known imprecision.** A bar is used once its bucket has closed (`ts + 5 min <= now`), and
the vendor may still adjust that bucket's close on the next poll. The event keeps what was
seen first: that is what a live trader would have seen too.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
from collections.abc import Iterable, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import notify
from app.core.db import get_session_factory
from app.modules.broker.basis import PAIRS, translate
from app.modules.broker.tables import Bar as BrokerBar
from app.modules.gex.models.db import (
    GexLevel,
    IntradayBar,
    LevelEvent,
    LevelEventScore,
    Snapshot,
)
from app.modules.gex.scan.level_events import (
    BAR,
    KEY_CALL_BOUNCE,
    KEY_DOWN,
    KEY_PUT_BOUNCE,
    KEY_UP,
    Capture,
    base_key,
    detect_crosses,
    detect_wall_bounces,
    direction_of,
    excursion_path,
    horizon_returns,
    minute_points,
    path_unit,
    side_at,
    variant_key,
)
from app.modules.gex.scan.level_events import LevelEvent as Event

__all__ = [
    "ALERT_FRESH",
    "CFD_OF",
    "ENTRY_WINDOW",
    "FAMILY_GAP",
    "FILTERS",
    "PATH_WAIT",
    "SYMBOLS",
    "alert_text",
    "record_level_events",
    "score_level_events",
    "select_alerts",
]

logger = logging.getLogger("app.modules.gex.jobs.level_events")

_NY = ZoneInfo("America/New_York")

#: Every desk symbol with both 5-minute bars and in-session captures. Order matters for alerts:
#: when SPX and SPY fire on the same bar, SPX's event is the one sent. IWM (-> US2000) since
#: T154; it needs `IWM` in both `SYMBOLS` and `INTRADAY_BARS_SYMBOLS` to produce anything.
SYMBOLS: tuple[str, ...] = ("SPX", "SPY", "QQQ", "GLD", "DIA", "IWM")

#: The `gex_levels` filters both rules run on: all expiries (the frozen specs) and 0DTE (T154).
FILTERS: tuple[str, ...] = ("ALL", "ZERO_DTE")

#: Desk symbol -> the CFD it is traded through. Taken from the broker module's basis pairs, so
#: the mapping the levels are translated with is the one the path is measured on.
_PAIR_OF = {p.desk: p for p in PAIRS}
CFD_OF: dict[str, str] = {p.desk: p.cfd for p in PAIRS}

#: Entries the specs allow, NY time: a 60-minute hold must finish before the close.
ENTRY_WINDOW = (dt.time(9, 45), dt.time(15, 0))

#: An event is alerted only if it is recorded within this long of its entry. Older ones were
#: caught up after a gap, and a demo entry on a stale signal tests nothing.
ALERT_FRESH = dt.timedelta(minutes=10)

#: One alert per index family and key within this window: the specs hold one position per
#: family for 60 minutes, and SPX, SPY and DIA cross together.
FAMILY_GAP = dt.timedelta(minutes=60)

_SESSION_OPEN = dt.time(9, 30)
_SESSION_CLOSE = dt.time(16, 0)
_INTERVAL = "5m"

#: How long to wait for the CFD's 1-minute bars before scoring without the path.
PATH_WAIT = dt.timedelta(days=3)

#: Trailing calendar days of CFD minutes the swing unit is measured over (about 15 sessions).
_UNIT_LOOKBACK = dt.timedelta(days=21)


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


def _captures(session: Session, symbol: str, day: dt.date, level_filter: str = "ALL") -> list[Capture]:
    """`symbol`'s in-session captures for `day` under one `gex_levels` filter, oldest first.
    Any level may be null (a 0DTE filter on a day with no 0DTE expiry has none)."""
    open_, close = _session_bounds(day)
    rows = session.execute(
        select(
            Snapshot.captured_at, GexLevel.flip_point, GexLevel.call_wall, GexLevel.put_wall,
            GexLevel.net_gex,
        )
        .join(GexLevel, GexLevel.snapshot_id == Snapshot.id)
        .where(
            Snapshot.underlying == symbol,
            Snapshot.is_eod.is_(False),
            Snapshot.session_date == day,
            Snapshot.captured_at >= open_,
            Snapshot.captured_at <= close,
            GexLevel.filter == level_filter,
        )
        .order_by(Snapshot.captured_at)
    ).all()
    return [Capture(r.captured_at, r.flip_point, r.call_wall, r.put_wall, r.net_gex) for r in rows]


def _in_window(entry_at: dt.datetime) -> bool:
    t = entry_at.astimezone(_NY).time()
    return ENTRY_WINDOW[0] <= t <= ENTRY_WINDOW[1]


def select_alerts(
    new: Sequence[Event], earlier_alerted: Iterable[tuple[str, str, dt.datetime]], now: dt.datetime
) -> set[tuple[str, dt.datetime, str]]:
    """Which of `new` to alert: fresh, inside the entry window, and first in its family.

    `earlier_alerted` is `(symbol, key, entry_at)` for today's already-alerted events. A family
    is the CFD (SPX and SPY are both US500). An event is dropped if its family already alerted
    the same key within `FAMILY_GAP` before it -- which also means DIA only alerts a move that
    SPX/SPY did not already make, the flip-cross spec's clustering rule. Returns the selected
    events' `(symbol, bar_ts, key)`.
    """
    sent: list[tuple[str, str, dt.datetime]] = [
        (CFD_OF.get(s, s), k, t) for s, k, t in earlier_alerted
    ]
    # US30 is suppressed by a US500 move, never the other way round
    suppressors = {"DJ30.fs": ("DJ30.fs", "S&P.fs")}
    order = {s: i for i, s in enumerate(SYMBOLS)}
    chosen: set[tuple[str, dt.datetime, str]] = set()
    for ev in sorted(new, key=lambda e: (e.entry_at, order.get(e.symbol, 99))):
        if now - ev.entry_at > ALERT_FRESH or not _in_window(ev.entry_at):
            continue
        fam = CFD_OF.get(ev.symbol, ev.symbol)
        blockers = suppressors.get(fam, (fam,))
        if any(f in blockers and k == ev.key and dt.timedelta(0) <= ev.entry_at - t < FAMILY_GAP
               for f, k, t in sent):
            continue
        sent.append((fam, ev.key, ev.entry_at))
        chosen.add((ev.symbol, ev.bar_ts, ev.key))
    return chosen


_HEADLINE = {
    KEY_DOWN: ("🔻", "FLIP CROSS DOWN", "crossed below the flip", "short, hold 60 min"),
    KEY_UP: ("🔺", "FLIP CROSS UP", "crossed above the flip", "fade: short, hold 60 min"),
    KEY_PUT_BOUNCE: ("🟢", "PUT WALL BOUNCE", "bounced off the put wall", "long bias"),
    KEY_CALL_BOUNCE: ("🔴", "CALL WALL BOUNCE", "rejected at the call wall", "short bias"),
}

_STAGE = {
    KEY_DOWN: "Flip-cross: stage-2 candidate. An in-sample leaning, not an edge.",
    KEY_UP: "Flip-cross: stage-2 candidate. An in-sample leaning, not an edge.",
    KEY_PUT_BOUNCE: "Wall-bounce: stage-0 rule, untested. No evidence yet.",
    KEY_CALL_BOUNCE: "Wall-bounce: stage-0 rule, untested. No evidence yet.",
}


_STAGE_0DTE = "0DTE variant: stage-0 rule on 0DTE levels, untested. No evidence yet."


def _fmt(x: float) -> str:
    return f"{x:,.2f}"


def alert_text(ev: Event, *, cfd_price: float | None) -> str:
    """The Telegram message for one event. `cfd_price` is the CFD's close at entry, if known."""
    icon, title, verb, bias = _HEADLINE[base_key(ev.key)]
    zero_dte = ev.key != base_key(ev.key)
    if zero_dte:
        verb = verb.replace("the ", "the 0DTE ", 1)
    pair = _PAIR_OF.get(ev.symbol)
    cfd = pair.cfd if pair else "?"
    et = ev.entry_at.astimezone(_NY).strftime("%H:%M")
    lines = [f"{icon} {title}{' (0DTE)' if zero_dte else ''} · {cfd} ({ev.symbol})"]
    level_name = ("0DTE " if zero_dte else "") + ev.level_kind.replace("_", " ")
    detail = f"{et} ET · {ev.symbol} {_fmt(ev.spot)} {verb} {_fmt(ev.level)}"
    if ev.touch_ts is not None:
        detail += f" (touched {ev.touch_ts.astimezone(_NY).strftime('%H:%M')}, two closes away)"
    lines.append(detail)
    if pair is not None and cfd_price is not None:
        offset, ratio = cfd_price - ev.spot, cfd_price / ev.spot
        cfd_level = translate(ev.level, pair, offset, ratio)
        lines.append(f"{cfd} {_fmt(cfd_price)} · {level_name} ≈ {_fmt(cfd_level)}")
    else:
        lines.append(f"{cfd} price not available yet; translate the {level_name} by hand")
    if ev.net_gex is not None:
        lines.append(f"net GEX {ev.net_gex / 1e9:+.2f}bn ({'long' if ev.net_gex > 0 else 'short'} gamma)")
    stage = _STAGE_0DTE if zero_dte else _STAGE[ev.key]
    lines.append(f"Spec: {bias}. {stage}")
    return "\n".join(lines)


def _cfd_close_at(session: Session, cfd: str, at: dt.datetime) -> float | None:
    """The CFD's last 1-minute close at or before `at`, within five minutes."""
    row = session.execute(
        select(BrokerBar.close).where(
            BrokerBar.symbol == cfd, BrokerBar.timeframe == "M1",
            BrokerBar.ts <= at - dt.timedelta(minutes=1),
            BrokerBar.ts >= at - dt.timedelta(minutes=6),
        ).order_by(BrokerBar.ts.desc()).limit(1)
    ).first()
    return row[0] if row else None


def record_level_events(
    *,
    now: dt.datetime | None = None,
    session_factory: sessionmaker[Session] | None = None,
    send=notify.send,
) -> list[LevelEvent]:
    """Detect today's events from the closed bars so far, insert the new ones, alert the
    selected ones. Never raises."""
    now = now or dt.datetime.now(dt.UTC)
    day = now.astimezone(_NY).date()
    factory = session_factory or get_session_factory()
    inserted: list[LevelEvent] = []
    messages: list[str] = []
    try:
        with factory() as session:
            spx_bars = {b.ts: b for b in _bars(session, "SPX", day, closed_by=now)}
            spx_caps = _captures(session, "SPX", day)
            stored = session.execute(
                select(LevelEvent.symbol, LevelEvent.bar_ts, LevelEvent.key,
                       LevelEvent.alerted, LevelEvent.entry_at)
                .where(LevelEvent.session_date == day)
            ).all()
            existing = {(r.symbol, r.bar_ts, r.key) for r in stored}
            earlier_alerted = [(r.symbol, r.key, r.entry_at) for r in stored if r.alerted]

            fresh: list[Event] = []
            for symbol in SYMBOLS:
                try:
                    bars = _bars(session, symbol, day, closed_by=now)
                    found: list[Event] = []
                    for level_filter in FILTERS:
                        caps = _captures(session, symbol, day, level_filter)
                        found += [
                            dataclasses.replace(e, key=variant_key(e.key, level_filter))
                            for e in detect_crosses(symbol, bars, caps) + detect_wall_bounces(symbol, bars, caps)
                        ]
                except Exception:
                    logger.exception("level_events: detection failed for %s", symbol)
                    continue
                fresh += [e for e in found if (symbol, e.bar_ts, e.key) not in existing]

            chosen = select_alerts(fresh, earlier_alerted, now)
            for ev in fresh:
                spx_bar = spx_bars.get(ev.bar_ts)
                spx_flip = [k for k in spx_caps if k.flip is not None and k.captured_at <= ev.entry_at]
                alerted = (ev.symbol, ev.bar_ts, ev.key) in chosen
                row = LevelEvent(
                    symbol=ev.symbol, bar_ts=ev.bar_ts, key=ev.key, session_date=day,
                    entry_at=ev.entry_at, spot=ev.spot, level=ev.level, level_kind=ev.level_kind,
                    touch_ts=ev.touch_ts, net_gex=ev.net_gex, capture_at=ev.capture_at,
                    spx_flip=spx_flip[-1].flip if spx_flip else None,
                    spx_side=side_at(spx_bar, spx_caps) if spx_bar is not None else None,
                    in_window=_in_window(ev.entry_at), alerted=alerted, recorded_at=now,
                )
                session.add(row)
                inserted.append(row)
                if alerted:
                    cfd = CFD_OF.get(ev.symbol)
                    price = _cfd_close_at(session, cfd, ev.entry_at) if cfd else None
                    messages.append(alert_text(ev, cfd_price=price))
            session.commit()
    except Exception:
        logger.exception("level_events: record failed")
        return []
    for row in inserted:
        logger.info(json.dumps({
            "event": "level_event", "symbol": row.symbol, "key": row.key,
            "entry_at": row.entry_at.isoformat(), "spot": row.spot, "level": row.level,
            "in_window": row.in_window, "alerted": row.alerted,
        }))
    # After the commit: a message never goes out for an event that was not stored.
    for msg in messages:
        send(msg)
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


def _path_fields(session: Session, ev: LevelEvent, now: dt.datetime, cache: dict) -> dict | None:
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


def score_level_events(
    *,
    now: dt.datetime | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[LevelEventScore]:
    """Write one score row per event whose session has ended. Never raises."""
    now = now or dt.datetime.now(dt.UTC)
    factory = session_factory or get_session_factory()
    written: list[LevelEventScore] = []
    unit_cache: dict = {}
    try:
        with factory() as session:
            pending = session.execute(
                select(LevelEvent).where(~exists().where(and_(
                    LevelEventScore.symbol == LevelEvent.symbol,
                    LevelEventScore.bar_ts == LevelEvent.bar_ts,
                    LevelEventScore.key == LevelEvent.key,
                ))).order_by(LevelEvent.bar_ts)
            ).scalars().all()
            for ev in pending:
                if now < _session_bounds(ev.session_date)[1] + BAR:
                    continue  # the session's last bar has not closed yet
                try:
                    bars = _bars(session, ev.symbol, ev.session_date)
                    event = Event(
                        symbol=ev.symbol, bar_ts=ev.bar_ts, key=ev.key, spot=ev.spot,
                        level=ev.level, level_kind=ev.level_kind, net_gex=ev.net_gex,
                        capture_at=ev.capture_at, touch_ts=ev.touch_ts,
                    )
                    rets, r_close = horizon_returns(event, bars)
                    path = _path_fields(session, ev, now, unit_cache)
                except Exception:
                    logger.exception("level_events: scoring failed for %s %s", ev.symbol, ev.bar_ts)
                    continue
                if path is None:
                    continue
                row = LevelEventScore(
                    symbol=ev.symbol, bar_ts=ev.bar_ts, key=ev.key,
                    ret_15m_bps=rets[15], ret_30m_bps=rets[30], ret_60m_bps=rets[60],
                    ret_close_bps=r_close, scored_at=now, **path,
                )
                session.add(row)
                written.append(row)
            session.commit()
    except Exception:
        logger.exception("level_events: scoring run failed")
        return []
    logger.info(json.dumps({"event": "level_event_scores", "written": len(written)}))
    return written
