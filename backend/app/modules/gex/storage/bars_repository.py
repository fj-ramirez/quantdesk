"""`daily_bars` read/write access (T42, plans/continuation/00-foundation-daily-bars.md).

Plain module-level functions rather than a `SnapshotRepository`-style class: every caller here
(the bars job, the backfill CLI, `/api/gex/bars`, and every later continuation-plan scan tool that
just wants a `DataFrame`) wants one of exactly four operations and none of them share
in-progress state across calls the way `SnapshotRepository` sometimes does, so a class would
add a constructor step for no benefit. Each function takes an optional `session_factory` --
defaults to this module's own cached sessionmaker (real Postgres, `app.core.config.settings`), same
DI pattern as `app.modules.gex.jobs.capture.get_session_factory` -- so tests inject a temp-SQLite factory
and the whole module runs fully offline.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.bars import DailyBar as DailyBarIn
from app.modules.gex.models.bars import IntradayBar as IntradayBarIn
from app.modules.gex.models.db import DailyBar, IntradayBar

__all__ = [
    "BarsUpsertResult",
    "get_session_factory",
    "last_bar_date",
    "read_bars",
    "read_bars_many",
    "read_intraday_bars",
    "read_universe_closes",
    "upsert_bars",
    "upsert_intraday_bars",
]

# One engine (and its connection pool) for the process's lifetime -- same rationale as
# app.modules.gex.jobs.capture._session_factory: creating a fresh engine per call would open (and never
# close) a new connection pool on every bars job run.
_session_factory: sessionmaker[Session] | None = None


def get_session_factory() -> sessionmaker[Session]:
    """Cached, lazily-created sessionmaker bound to `settings.DATABASE_URL`.

    Tests should not use this -- pass an explicit `session_factory` (SQLite, via `tmp_path`)
    to every function in this module instead, so the test suite never touches the real
    database this points at.
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = get_sessionmaker(get_engine())
    return _session_factory


@dataclass(frozen=True, slots=True)
class BarsUpsertResult:
    """Outcome of one `upsert_bars` call -- also the shape of the bars job's summary log line."""

    inserted: int
    updated: int


def upsert_bars(
    bars: Sequence[DailyBarIn], *, session_factory: sessionmaker[Session] | None = None
) -> BarsUpsertResult:
    """Upsert `bars` on `(symbol, date)`: a date not already stored for its symbol is inserted,
    one already stored is overwritten with the new OHLCV/source values.

    This is what makes a second backfill or bars-job run over an unchanged vendor response
    genuinely idempotent (T42 acceptance: "a second run inserts zero new rows") while still
    picking up vendor revisions -- the plan's "fetch from the last stored date minus 5 days"
    incremental design exists specifically so a handful of already-stored days get silently
    corrected here rather than needing a special "did anything change" comparison first.

    Dialect-specific `INSERT ... ON CONFLICT DO UPDATE` is used (`postgresql` in production,
    `sqlite` in every test) rather than a plain SQLAlchemy merge/query-then-write loop, so the
    whole batch is one round trip per symbol instead of one SELECT-then-INSERT-or-UPDATE per
    row -- relevant once a backfill is writing years of daily bars for ~80 symbols.

    Returns:
        `BarsUpsertResult` with `inserted` (dates that did not exist for their symbol before
        this call) and `updated` (dates that did). Computed from a pre-upsert existence check,
        grouped by symbol so it costs one indexed `SELECT` per symbol rather than one per row.
    """
    if not bars:
        return BarsUpsertResult(inserted=0, updated=0)

    factory = session_factory or get_session_factory()
    inserted = 0
    updated = 0
    with factory() as session:
        insert_fn = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert

        by_symbol: dict[str, list[DailyBarIn]] = {}
        for bar in bars:
            by_symbol.setdefault(bar.symbol, []).append(bar)

        for symbol, symbol_bars in by_symbol.items():
            dates = {b.date for b in symbol_bars}
            existing = set(
                session.execute(
                    select(DailyBar.date).where(
                        DailyBar.symbol == symbol, DailyBar.date.in_(dates)
                    )
                ).scalars()
            )
            inserted += len(dates - existing)
            updated += len(dates & existing)

            rows = [
                {
                    "symbol": b.symbol,
                    "date": b.date,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "source": b.source,
                }
                for b in symbol_bars
            ]
            stmt = insert_fn(DailyBar).values(rows)
            update_cols = {
                col: getattr(stmt.excluded, col)
                for col in ("open", "high", "low", "close", "volume", "source")
            }
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "date"], set_=update_cols
            )
            session.execute(stmt)

        session.commit()

    return BarsUpsertResult(inserted=inserted, updated=updated)


def upsert_intraday_bars(
    bars: Sequence[IntradayBarIn], *, session_factory: sessionmaker[Session] | None = None
) -> BarsUpsertResult:
    """Upsert intraday buckets on `(symbol, interval, ts)` -- the same contract as
    `upsert_bars`, one grain finer (T74).

    The upsert is not an optimisation here, it is the mechanism. T74's job re-fetches the whole
    session every five minutes, so the newest bucket arrives repeatedly while it is still
    forming: at 15:14 the 15:10 bucket is partial, and at 15:19 the same bucket arrives settled.
    Keying on the bucket's instant means the second write *corrects* the first instead of
    appending a near-duplicate, which is what makes the stored series continuous rather than
    one interval behind. It also means a missed poll costs nothing -- the next one carries the
    buckets the missed one would have written.

    Returns:
        `BarsUpsertResult`, with `inserted` counting buckets new to their (symbol, interval) and
        `updated` counting buckets already stored -- so a steady session reads as a handful of
        inserts and one or two updates per poll, and a run of pure updates means the vendor
        stopped publishing new buckets.
    """
    if not bars:
        return BarsUpsertResult(inserted=0, updated=0)

    factory = session_factory or get_session_factory()
    inserted = 0
    updated = 0
    with factory() as session:
        insert_fn = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert

        by_key: dict[tuple[str, str], list[IntradayBarIn]] = {}
        for bar in bars:
            by_key.setdefault((bar.symbol, bar.interval), []).append(bar)

        for (symbol, interval), group in by_key.items():
            stamps = {b.ts for b in group}
            existing = set(
                session.execute(
                    select(IntradayBar.ts).where(
                        IntradayBar.symbol == symbol,
                        IntradayBar.interval == interval,
                        IntradayBar.ts.in_(stamps),
                    )
                ).scalars()
            )
            inserted += len(stamps - existing)
            updated += len(stamps & existing)

            rows = [
                {
                    "symbol": b.symbol,
                    "interval": b.interval,
                    "ts": b.ts,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "source": b.source,
                }
                for b in group
            ]
            stmt = insert_fn(IntradayBar).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "interval", "ts"],
                set_={
                    col: getattr(stmt.excluded, col)
                    for col in ("open", "high", "low", "close", "volume", "source")
                },
            )
            session.execute(stmt)

        session.commit()

    return BarsUpsertResult(inserted=inserted, updated=updated)


def read_intraday_bars(
    symbol: str,
    *,
    interval: str,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    session_factory: sessionmaker[Session] | None = None,
) -> list[IntradayBar]:
    """Stored buckets for `(symbol, interval)` in `[start, end]`, ascending by `ts`.

    Bounds must be tz-aware if given: `UTCDateTime` always hands back aware values, so a naive
    bound would compare wrongly rather than loudly (invariant 4).
    """
    factory = session_factory or get_session_factory()
    with factory() as session:
        stmt = select(IntradayBar).where(
            IntradayBar.symbol == symbol, IntradayBar.interval == interval
        )
        if start is not None:
            stmt = stmt.where(IntradayBar.ts >= start)
        if end is not None:
            stmt = stmt.where(IntradayBar.ts <= end)
        return list(session.execute(stmt.order_by(IntradayBar.ts.asc())).scalars().all())


def last_bar_date(
    symbol: str, *, session_factory: sessionmaker[Session] | None = None
) -> dt.date | None:
    """Most recent stored date for `symbol`, or `None` if it has no bars at all.

    The bars job's incremental design reads this per symbol and fetches from
    `last_bar_date(symbol) - 5 days` (plan design decision: "to pick up vendor revisions"),
    falling back to a full backfill window when it is `None`.
    """
    factory = session_factory or get_session_factory()
    with factory() as session:
        stmt = select(func.max(DailyBar.date)).where(DailyBar.symbol == symbol)
        return session.execute(stmt).scalar_one_or_none()


def read_bars(
    symbol: str,
    start: dt.date | None = None,
    end: dt.date | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> pd.DataFrame:
    """Every stored bar for `symbol` in `[start, end]` (either bound optional), ascending by
    date, as a `DataFrame` with columns `date, open, high, low, close, volume, source`.

    Returns an empty `DataFrame` with those same columns -- never `None` and never a frame
    missing a column -- when `symbol` has no bars in range, so a caller can index a column
    unconditionally without a prior "is this empty" branch.
    """
    factory = session_factory or get_session_factory()
    # T124: plain columns, not `select(DailyBar)`. Hydrating an ORM object per row cost ~2.9 s
    # across the 125-symbol universe (157k rows) for data that goes straight into a frame.
    stmt = select(*_BAR_COLUMNS).where(DailyBar.symbol == symbol)
    if start is not None:
        stmt = stmt.where(DailyBar.date >= start)
    if end is not None:
        stmt = stmt.where(DailyBar.date <= end)
    stmt = stmt.order_by(DailyBar.date.asc())
    with factory() as session:
        rows = session.execute(stmt).all()
    return _bars_frame(rows)


def read_bars_many(
    symbols: Sequence[str],
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> dict[str, pd.DataFrame]:
    """`read_bars(symbol)` for every symbol in `symbols`, in one query (T124).

    Each frame is built by the same `_bars_frame` as `read_bars`, per symbol, so it is
    identical to what `read_bars` returns -- including a symbol whose volume is all `None`
    staying `None` rather than being widened to `NaN` by a neighbour's integers in a shared
    frame. Every requested symbol gets a key; one with no bars maps to the empty frame.
    """
    factory = session_factory or get_session_factory()
    wanted = list(dict.fromkeys(symbols))
    stmt = (
        select(DailyBar.symbol, *_BAR_COLUMNS)
        .where(DailyBar.symbol.in_(wanted))
        .order_by(DailyBar.symbol, DailyBar.date.asc())
    )
    grouped: dict[str, list] = {symbol: [] for symbol in wanted}
    with factory() as session:
        for symbol, *bar in session.execute(stmt):
            grouped[symbol].append(bar)
    return {symbol: _bars_frame(rows) for symbol, rows in grouped.items()}


_BAR_COLUMNS = (
    DailyBar.date,
    DailyBar.open,
    DailyBar.high,
    DailyBar.low,
    DailyBar.close,
    DailyBar.volume,
    DailyBar.source,
)
_BAR_FRAME_COLUMNS = ["date", "open", "high", "low", "close", "volume", "source"]


def _bars_frame(rows: Sequence[Sequence]) -> pd.DataFrame:
    """`read_bars`' frame from `(date, open, high, low, close, volume, source)` tuples."""
    columns = (list(col) for col in zip(*rows)) if rows else ([] for _ in _BAR_FRAME_COLUMNS)
    return pd.DataFrame(dict(zip(_BAR_FRAME_COLUMNS, columns)), columns=_BAR_FRAME_COLUMNS)


def read_universe_closes(
    symbols: Sequence[str],
    start: dt.date,
    end: dt.date,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> pd.DataFrame:
    """Wide `DataFrame` of close prices: one row per date, one column per symbol in `symbols`.

    The shape every later continuation-plan scan tool wants (rotation, cross-asset strip):
    correlations, relative-strength ratios and the like are all naturally wide-frame
    operations. Every symbol in `symbols` gets a column even if it has zero bars in range
    (filled with `NA`), so a caller can do `wide[symbol]` unconditionally rather than checking
    membership first -- a symbol newly added to `SCAN_UNIVERSE` before its first bars job run
    should read as "all NA," not raise a `KeyError`.
    """
    factory = session_factory or get_session_factory()
    stmt = (
        select(DailyBar.symbol, DailyBar.date, DailyBar.close)
        .where(
            DailyBar.symbol.in_(list(symbols)),
            DailyBar.date >= start,
            DailyBar.date <= end,
        )
        .order_by(DailyBar.date.asc())
    )
    with factory() as session:
        rows = session.execute(stmt).all()

    long_df = pd.DataFrame(rows, columns=["symbol", "date", "close"])
    if long_df.empty:
        wide = pd.DataFrame(index=pd.Index([], name="date"))
    else:
        wide = long_df.pivot(index="date", columns="symbol", values="close")
    for symbol in symbols:
        if symbol not in wide.columns:
            wide[symbol] = pd.NA
    return wide[list(symbols)].sort_index()
