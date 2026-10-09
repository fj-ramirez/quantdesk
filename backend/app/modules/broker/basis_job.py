"""The basis job (T145): read desk and CFD prices, measure, find rolls, store.

Run by `broker-ingest` once an hour, after ticks. Everything it writes is derived, so each run
**recomputes and replaces** `broker.basis` and `broker.rolls` in one transaction. A changed bar
on either side is reflected on the next run, and nothing can be left half-updated. Five years of
daily closes plus the 5-minute bars since mid-September is about 13,000 rows. The pure half is
`basis.py`; this file only moves data.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterable

import numpy as np
import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.modules.broker import basis
from app.modules.broker.tables import Bar, BasisRow, RollRow
from app.modules.gex.models.db import DailyBar, IntradayBar

__all__ = ["BasisResult", "run_basis"]

logger = logging.getLogger("app.modules.broker.basis_job")

NY = "America/New_York"
_FIVE = pd.Timedelta(minutes=5)
_ONE = pd.Timedelta(minutes=1)


class BasisResult(dict):
    """Per-pair row counts plus the rolls found: the job's log line."""


def _cfd_closes(session: Session, symbol: str, bar_opens: Iterable[pd.Timestamp]) -> pd.Series:
    """CFD M1 closes for the given bar-open instants, indexed by the bar's **close** instant."""
    opens = sorted({t.to_pydatetime() for t in bar_opens})
    out: dict[pd.Timestamp, float] = {}
    for i in range(0, len(opens), 500):
        chunk = opens[i : i + 500]
        for ts, close in session.execute(
            select(Bar.ts, Bar.close).where(Bar.symbol == symbol, Bar.timeframe == "M1",
                                            Bar.ts.in_(chunk))
        ):
            out[pd.Timestamp(ts) + _ONE] = close
    return pd.Series(out, dtype=float)


def _daily(session: Session, pair: basis.Pair) -> pd.DataFrame:
    rows = session.execute(
        select(DailyBar.date, DailyBar.close).where(DailyBar.symbol == pair.desk)
    ).all()
    days = [d for d, _ in rows if not basis.is_early_close_candidate(d)]
    close_at = {
        d: pd.Timestamp(dt.datetime.combine(d, dt.time(16)), tz=NY).tz_convert("UTC") for d in days
    }
    desk = pd.Series({close_at[d]: c for d, c in rows if d in close_at}, dtype=float)
    cfd = _cfd_closes(session, pair.cfd, (t - _ONE for t in desk.index))
    return basis.measure(desk, cfd)


def _intraday(session: Session, pair: basis.Pair, now: dt.datetime) -> pd.DataFrame:
    rows = session.execute(
        select(IntradayBar.ts, IntradayBar.close)
        .where(IntradayBar.symbol == pair.desk, IntradayBar.interval == "5m")
    ).all()
    # A 5-minute bar closes at its open plus five minutes. The newest one may still be forming.
    desk = pd.Series({pd.Timestamp(ts) + _FIVE: c for ts, c in rows
                      if pd.Timestamp(ts) + _FIVE <= pd.Timestamp(now)}, dtype=float)
    cfd = _cfd_closes(session, pair.cfd, (t - _ONE for t in desk.index))
    return basis.measure(desk, cfd)


def _refine(roll: basis.Roll, daily: pd.DataFrame, intraday: pd.DataFrame) -> tuple[pd.Timestamp, str]:
    """Place a daily roll on the 5-minute grid when the desk has 5-minute bars across it: the
    largest step in log(ratio) between the previous daily close and the roll's close."""
    before = daily.index[daily.index < roll.occurred]
    if intraday.empty or before.empty:
        return roll.occurred, "1d"
    lo = before[-1]
    window = intraday[(intraday.index > lo) & (intraday.index <= roll.occurred)]
    prior = intraday[intraday.index <= lo]
    if window.empty or prior.empty:
        return roll.occurred, "1d"
    lr = np.log(pd.concat([prior["ratio"].iloc[-1:], window["ratio"]]).to_numpy())
    return window.index[int(np.argmax(np.abs(np.diff(lr))))], "5m"


def run_basis(factory: sessionmaker[Session], now: dt.datetime | None = None) -> BasisResult:
    now = now or dt.datetime.now(dt.UTC)
    result = BasisResult()
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    with factory() as session:
        for pair in basis.PAIRS:
            frames[(pair.desk, "1d")] = _daily(session, pair)
            frames[(pair.desk, "5m")] = _intraday(session, pair, now)

    # Rolls per CFD, from its first (primary) pair: SPX for S&P.fs, the index rather than the
    # ETF, because an ETF's dividends move its ratio too.
    rolls: list[RollRow] = []
    seen: set[str] = set()
    for pair in basis.PAIRS:
        if not pair.rolls or pair.cfd in seen:
            continue
        seen.add(pair.cfd)
        daily, intraday = frames[(pair.desk, "1d")], frames[(pair.desk, "5m")]
        for r in basis.detect_rolls(daily):
            at, res = _refine(r, daily, intraday)
            rolls.append(RollRow(cfd_symbol=pair.cfd, rolled_at=at.to_pydatetime(),
                                 confirmed_at=r.confirmed.to_pydatetime(), step=r.step,
                                 desk_symbol=pair.desk, resolution=res))
    starts: dict[str, list[pd.Timestamp]] = {}
    for r in rolls:
        starts.setdefault(r.cfd_symbol, []).append(pd.Timestamp(r.rolled_at))

    rows: list[BasisRow] = []
    for pair in basis.PAIRS:
        cuts = sorted(starts.get(pair.cfd, []))
        for kind in ("1d", "5m"):
            df = frames[(pair.desk, kind)]
            result[f"{pair.desk}/{kind}"] = len(df)
            for at, r in df.iterrows():
                seg = max((c for c in cuts if c <= at), default=None)
                rows.append(BasisRow(
                    desk_symbol=pair.desk, kind=kind, at=at.to_pydatetime(), cfd_symbol=pair.cfd,
                    method=pair.method, proxy=pair.proxy, desk_price=float(r["desk"]),
                    cfd_price=float(r["cfd"]), offset=float(r["offset"]), ratio=float(r["ratio"]),
                    segment_start=None if seg is None else seg.to_pydatetime()))

    result["rolls"] = [(r.cfd_symbol, r.rolled_at.isoformat(), round(100 * r.step, 2), r.resolution)
                       for r in rolls]
    with factory() as session:
        session.execute(delete(BasisRow))
        session.execute(delete(RollRow))
        session.add_all(rows)
        session.add_all(rolls)
        session.commit()
    logger.info("broker: basis %s", dict(result))
    return result
