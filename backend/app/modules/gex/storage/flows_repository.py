"""`etf_shares_outstanding` read/write access (T52, plans/continuation/05-etf-flows.md).

Mirrors `app.modules.gex.storage.bars_repository`'s shape (plain module-level functions, a cached
module-level sessionmaker with an injectable `session_factory` for tests, the same
dialect-specific `INSERT ... ON CONFLICT` idiom) with one deliberate divergence:
`insert_new_rows` below is `DO NOTHING` on conflict, never `DO UPDATE`.

**Why this table never overwrites, unlike `daily_bars`.** `upsert_bars` overwrites an existing
`(symbol, date)` row on purpose -- a bars vendor can revise a recent day, and the bars job
re-fetches the last 5 days on every run specifically to pick that up. Shares outstanding has
no such "vendor revises a recent day" story; the risk here runs the other way. Per
`docs/etf-flows-sources.md`, issuer files lag a full trading day and are not fetched at a fixed
lag -- a same-evening safety-net-style re-run (or the health-check-driven "just try it again")
could see a file whose as-of date has not advanced yet. Overwriting on that re-run would be
harmless (same value back over itself) *only* if the value never legitimately changes once
stored -- which it doesn't, by construction, once a given as-of date's row exists: the
row for `(symbol, date)` is the historical truth for that specific calendar day, and there is no
correction path the issuer publishes for it after the fact. So the correct behavior is
`DO NOTHING`: insert once per `(symbol, date)`, never touch it again.
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
from app.modules.gex.models.db import EtfSharesOutstanding
from app.modules.gex.providers.etf_flows import SharesOutstandingRow

__all__ = [
    "FlowsInsertResult",
    "get_session_factory",
    "insert_new_rows",
    "last_as_of_date",
    "read_shares_outstanding",
    "read_universe_nav",
    "read_universe_shares_outstanding",
]

# One engine (and its connection pool) for the process's lifetime -- same rationale as
# app.modules.gex.storage.bars_repository's own module-level cache.
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
class FlowsInsertResult:
    """Outcome of one `insert_new_rows` call -- also the shape of the flows job's per-family
    summary log line."""

    inserted: int
    skipped: int


def insert_new_rows(
    rows: Sequence[SharesOutstandingRow], *, session_factory: sessionmaker[Session] | None = None
) -> FlowsInsertResult:
    """Insert every row in `rows` whose `(symbol, date)` is not already stored; leave an
    existing `(symbol, date)` untouched. See the module docstring for why this is `DO NOTHING`
    rather than `bars_repository.upsert_bars`'s `DO UPDATE`.

    This is what makes the T52 acceptance check literal: `python -m app.modules.gex.flows_fetch
    --symbols XLK,IWM` run twice in a row inserts a row the first time and zero rows the
    second time, for exactly the same reason `upsert_bars`'s second-run test is zero *new*
    rows -- except here a second run also never *updates* anything, by design.

    Returns:
        `FlowsInsertResult` with `inserted` (rows whose `(symbol, date)` did not exist before
        this call) and `skipped` (rows whose `(symbol, date)` already existed, computed from a
        pre-insert existence check the same way `upsert_bars` computes its own counts, grouped
        by symbol so it costs one indexed `SELECT` per symbol rather than one per row).
    """
    if not rows:
        return FlowsInsertResult(inserted=0, skipped=0)

    factory = session_factory or get_session_factory()
    inserted = 0
    skipped = 0
    with factory() as session:
        insert_fn = pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert

        by_symbol: dict[str, list[SharesOutstandingRow]] = {}
        for row in rows:
            by_symbol.setdefault(row.symbol, []).append(row)

        for symbol, symbol_rows in by_symbol.items():
            dates = {r.as_of_date for r in symbol_rows}
            existing = set(
                session.execute(
                    select(EtfSharesOutstanding.date).where(
                        EtfSharesOutstanding.symbol == symbol, EtfSharesOutstanding.date.in_(dates)
                    )
                ).scalars()
            )
            inserted += len(dates - existing)
            skipped += len(dates & existing)

            new_rows = [r for r in symbol_rows if r.as_of_date not in existing]
            if not new_rows:
                continue

            values = [
                {
                    "symbol": r.symbol,
                    "date": r.as_of_date,
                    "shares": r.shares_outstanding,
                    "nav": r.nav,
                    "source": r.source,
                }
                for r in new_rows
            ]
            stmt = insert_fn(EtfSharesOutstanding).values(values)
            # DO NOTHING, not DO UPDATE -- the module docstring's reasoning. The pre-check
            # existence set above is what makes `inserted`/`skipped` accurate; this clause is
            # the backstop against two writers racing past that check, same role the unique
            # constraint plays for `GexByStrike`.
            stmt = stmt.on_conflict_do_nothing(index_elements=["symbol", "date"])
            session.execute(stmt)

        session.commit()

    return FlowsInsertResult(inserted=inserted, skipped=skipped)


def last_as_of_date(
    symbol: str, *, session_factory: sessionmaker[Session] | None = None
) -> dt.date | None:
    """Most recent stored as-of date for `symbol`, or `None` if it has no rows at all.

    This is what the health block reports per family (the max over each family's symbols) --
    per `docs/etf-flows-sources.md`'s corrected freshness rule: "the health block must report
    the as-of date of the newest stored row per family, not the last fetch time."
    """
    factory = session_factory or get_session_factory()
    with factory() as session:
        stmt = select(func.max(EtfSharesOutstanding.date)).where(EtfSharesOutstanding.symbol == symbol)
        return session.execute(stmt).scalar_one_or_none()


def read_shares_outstanding(
    symbol: str,
    start: dt.date | None = None,
    end: dt.date | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> pd.DataFrame:
    """Every stored row for `symbol` in `[start, end]` (either bound optional), ascending by
    date, as a `DataFrame` with columns `symbol, date, shares, nav, source`.

    Returns an empty `DataFrame` with those same columns -- never `None`, never a frame missing
    a column -- when `symbol` has no rows in range, mirroring `bars_repository.read_bars`'s
    same contract for the same reason: a caller indexes a column unconditionally.
    """
    factory = session_factory or get_session_factory()
    stmt = select(EtfSharesOutstanding).where(EtfSharesOutstanding.symbol == symbol)
    if start is not None:
        stmt = stmt.where(EtfSharesOutstanding.date >= start)
    if end is not None:
        stmt = stmt.where(EtfSharesOutstanding.date <= end)
    stmt = stmt.order_by(EtfSharesOutstanding.date.asc())
    with factory() as session:
        rows = session.execute(stmt).scalars().all()
    return pd.DataFrame(
        {
            "symbol": [r.symbol for r in rows],
            "date": [r.date for r in rows],
            "shares": [r.shares for r in rows],
            "nav": [r.nav for r in rows],
            "source": [r.source for r in rows],
        },
        columns=["symbol", "date", "shares", "nav", "source"],
    )


def _read_universe_wide(
    symbols: Sequence[str],
    start: dt.date,
    end: dt.date,
    value_column,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> pd.DataFrame:
    """Shared pivot logic behind `read_universe_shares_outstanding`/`read_universe_nav` --
    mirrors `bars_repository.read_universe_closes`'s exact shape (one row per date, one column
    per symbol, every requested symbol present even with zero rows, filled `NA`) so
    `app.modules.gex.scan.flows.compute_flows` can index `frame[symbol]` unconditionally the same way every
    other continuation-plan scan tool already does against `read_universe_closes`.
    """
    factory = session_factory or get_session_factory()
    stmt = (
        select(EtfSharesOutstanding.symbol, EtfSharesOutstanding.date, value_column)
        .where(
            EtfSharesOutstanding.symbol.in_(list(symbols)),
            EtfSharesOutstanding.date >= start,
            EtfSharesOutstanding.date <= end,
        )
        .order_by(EtfSharesOutstanding.date.asc())
    )
    with factory() as session:
        rows = session.execute(stmt).all()

    long_df = pd.DataFrame(rows, columns=["symbol", "date", "value"])
    if long_df.empty:
        wide = pd.DataFrame(index=pd.Index([], name="date"))
    else:
        wide = long_df.pivot(index="date", columns="symbol", values="value")
    for symbol in symbols:
        if symbol not in wide.columns:
            wide[symbol] = pd.NA
    return wide[list(symbols)].sort_index()


def read_universe_shares_outstanding(
    symbols: Sequence[str],
    start: dt.date,
    end: dt.date,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> pd.DataFrame:
    """Wide `DataFrame` of shares outstanding: one row per date, one column per symbol in
    `symbols` -- the `so_frame` shape `app.modules.gex.scan.flows.compute_flows` expects."""
    return _read_universe_wide(
        symbols, start, end, EtfSharesOutstanding.shares, session_factory=session_factory
    )


def read_universe_nav(
    symbols: Sequence[str],
    start: dt.date,
    end: dt.date,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> pd.DataFrame:
    """Wide `DataFrame` of NAV per share: one row per date, one column per symbol in
    `symbols` -- the `nav_frame` shape `app.modules.gex.scan.flows.compute_flows` expects."""
    return _read_universe_wide(
        symbols, start, end, EtfSharesOutstanding.nav, session_factory=session_factory
    )
