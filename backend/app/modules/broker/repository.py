"""Read queries behind the broker chart (T159). Nothing here writes.

Each function takes a `session_factory`, so a test passes a SQLite one. `levels` reads the
`broker.levels` **view**, which exists only in Postgres (migration `f7a8b9c0d1e2`); tests
replace that function rather than build the view.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.modules.broker.tables import Bar

__all__ = ["configured_symbols", "last_bar_at", "levels", "m1_bars"]

_M1 = "M1"


def configured_symbols() -> list[str]:
    """The CFDs the ingest worker captures, in config order. Same parse as the worker's."""
    return [s.strip() for s in settings.BROKER_SYMBOLS.split(",") if s.strip()]


def last_bar_at(symbol: str, *, session_factory: sessionmaker[Session]) -> dt.datetime | None:
    """The newest stored M1 bar's open, or None. A `max` on the primary key's prefix, so it is
    an index lookup rather than a scan."""
    with session_factory() as session:
        return session.scalar(
            select(func.max(Bar.ts)).where(Bar.symbol == symbol, Bar.timeframe == _M1)
        )


def m1_bars(
    symbol: str,
    *,
    limit: int,
    end: dt.datetime | None,
    session_factory: sessionmaker[Session],
) -> pd.DataFrame:
    """The newest `limit` M1 bars of `symbol` that had **closed** by `end`, ascending.

    A row's `ts` is its open, so a bar has closed by `end` when `ts + 1 minute <= end`.
    """
    stmt = select(
        Bar.ts, Bar.open, Bar.high, Bar.low, Bar.close, Bar.tick_volume, Bar.spread, Bar.offset_s
    ).where(Bar.symbol == symbol, Bar.timeframe == _M1)
    if end is not None:
        stmt = stmt.where(Bar.ts <= end - dt.timedelta(minutes=1))
    stmt = stmt.order_by(Bar.ts.desc()).limit(limit)
    with session_factory() as session:
        rows = session.execute(stmt).all()
    cols = ["ts", "open", "high", "low", "close", "tick_volume", "spread", "offset_s"]
    frame = pd.DataFrame(rows, columns=cols)
    return frame.iloc[::-1].reset_index(drop=True)


_LEVELS_SQL = text(
    """
    SELECT captured_at, desk_spot, spot, call_wall, put_wall, flip_point, basis_at, basis_kind,
           method, proxy, roll_after_basis
    FROM broker.levels
    WHERE cfd_symbol = :cfd AND desk_symbol = :desk AND filter = 'ALL'
      AND captured_at >= :start AND captured_at <= :end
    ORDER BY captured_at
    """
)


def levels(
    cfd: str,
    desk: str,
    *,
    start: dt.datetime,
    end: dt.datetime,
    session_factory: sessionmaker[Session],
) -> pd.DataFrame:
    """GEX levels of `desk` in `cfd` price, captured in `[start, end]`, ascending."""
    with session_factory() as session:
        rows = session.execute(
            _LEVELS_SQL, {"cfd": cfd, "desk": desk, "start": start, "end": end}
        ).mappings().all()
    return pd.DataFrame([dict(r) for r in rows])
