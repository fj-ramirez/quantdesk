"""Trial registry, backed by the `research` Postgres schema (T77).

Every backtest ever run is recorded here -- including the losers. The total trial count is what
lets the report estimate how good the best result would look under pure noise (multiple-testing
honesty), and the unique hash keeps the search from re-testing combinations it has already
tried, across nights and across machines.

**The interface did not change.** `search.py`, `paper.py` and `report.py` call the same methods
with the same signatures as they did against SQLite; only the internals moved. That is the same
rule GEX's invariant 6 states about providers -- a storage swap is not an edit to callers -- and
it is why the port touched none of the science modules.

Three things about this implementation are load-bearing:

**There is no SQLite fallback, and that is the point.** If `DATABASE_URL` is unset or the
database is unreachable, this raises. The failure mode it prevents is not an error, it is
silence: with a fallback, a laptop that could not reach Postgres would quietly start a fresh
local `registry.db`, the two histories would diverge, and the never-repeat-work guarantee -- the
entire value of 134,377 recorded trials -- would die without anything going red. A loud failure
on a laptop that is off the network is the correct trade, and `run_nightly.ps1` says so.

**`seen()` is answered from memory, not the network.** `search.py` calls it before every single
trial. Against a local SQLite file that was free; against Postgres over the LAN -- and much more
so from the Windows host over Tailscale -- a per-trial round trip would dominate a cycle that is
otherwise pure CPU. The hash set is loaded once on first use (one query, ~134k rows of 24-char
strings, a few MB) and `record` keeps it current. The cost moves from O(trials) round trips to
exactly one.

**Two writers against one database is fine and intended.** `hash` is the primary key and
`record` is `ON CONFLICT DO NOTHING`, so the Windows scheduled task and the `research-search`
worker can both run: the worst case is that both spend CPU on the same combination and the
second insert is discarded. What must never happen is two writers against two *stores*, which
is what the no-fallback rule above forbids.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, aliased, sessionmaker
from sqlalchemy.sql import Insert

from app.core.db import get_engine, get_sessionmaker
from app.modules.research.models.db import PaperCandidate, PaperScore, Trial

log = logging.getLogger(__name__)

__all__ = ["Registry", "trial_hash"]


def trial_hash(market: str, strategy: str, symbol: str, timeframe: str, params: dict) -> str:
    """Stable identity for one combination. **Unchanged from the SQLite original, deliberately.**

    Every one of the 134,377 migrated hashes was computed by this exact function, so changing
    it -- the separator, the truncation, the `sort_keys`, anything -- would silently invalidate
    the whole registry: `seen()` would miss, the search would redo years of work, and nothing
    would report an error. `params` is hashed as JSON *before* storage, which is also why moving
    the column to `JSONB` is safe: the hash never round-trips through the database.
    """
    key = f"{market}|{strategy}|{symbol}|{timeframe}|{json.dumps(params, sort_keys=True)}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _insert_ignore(
    table, dialect_name: str, index_elements: list[str] | None = None
) -> Insert:
    """`ON CONFLICT (...) DO NOTHING`, spelled for whichever backend is in front of us.

    The original's `INSERT OR IGNORE` is SQLite-only syntax. Postgres and SQLite both support
    the `ON CONFLICT` form through SQLAlchemy, but from different dialect modules, so the choice
    has to be made per engine rather than once at import.

    `index_elements` defaults to `["hash"]`, which is the key of both original tables. T83's
    `paper_scores` is keyed on `(hash, scored_at)` -- it stores a history, so hash alone is
    deliberately not unique there -- and passes its own. Naming the conflict target rather than
    assuming it is what keeps a second measurement of the same candidate from being silently
    swallowed as a duplicate.
    """
    targets = index_elements or ["hash"]
    if dialect_name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert(table).on_conflict_do_nothing(index_elements=targets)
    return pg_insert(table).on_conflict_do_nothing(index_elements=targets)


class Registry:
    """The trial store. Construct one per cycle; it holds a session open for its lifetime."""

    def __init__(
        self,
        session_factory: sessionmaker[Session] | None = None,
        engine: Engine | None = None,
    ):
        """`session_factory` and `engine` are injectable for tests only.

        Production passes neither and gets `settings.DATABASE_URL`. There is deliberately no
        `path=` argument: the SQLite constructor took one, and leaving it would be an open
        invitation to the fork this class exists to prevent.
        """
        if session_factory is None:
            engine = engine or get_engine()
            session_factory = get_sessionmaker(engine)
        self._engine = engine
        self._session: Session = session_factory()
        self._dialect = self._session.get_bind().dialect.name
        # Loaded lazily by `seen()`: a `--report-only` run never needs it, and paying 134k rows
        # to print a leaderboard would be pure waste.
        self._seen: set[str] | None = None

    # -- the hot path ---------------------------------------------------------------------

    def seen(self, h: str) -> bool:
        """Has this combination been tried? Answered from the in-memory hash set."""
        if self._seen is None:
            self._load_seen()
        return h in self._seen

    def _load_seen(self) -> None:
        self._seen = set(self._session.scalars(select(Trial.hash)).all())
        log.info("registry: loaded %d known trial hashes", len(self._seen))

    # -- writes ---------------------------------------------------------------------------

    def record(
        self,
        h: str,
        run_date: str | dt.date,
        market: str,
        strategy: str,
        symbol: str,
        timeframe: str,
        params: dict,
        is_m: dict,
        oos_m: dict,
    ) -> None:
        """Record one completed trial. Silently ignores a hash that is already stored.

        `run_date` accepts the ISO string the original passed (`date.today().isoformat()`) as
        well as a real `date`, so `search.py` needed no edit.
        """
        if isinstance(run_date, str):
            run_date = dt.date.fromisoformat(run_date)

        self._session.execute(
            _insert_ignore(Trial, self._dialect).values(
                hash=h,
                run_date=run_date,
                market=market,
                strategy=strategy,
                symbol=symbol,
                timeframe=timeframe,
                params=params,
                is_sharpe=is_m["sharpe"],
                is_cagr=is_m["cagr"],
                is_max_dd=is_m["max_drawdown"],
                is_fills=is_m["n_fills"],
                oos_sharpe=oos_m["sharpe"],
                oos_cagr=oos_m["cagr"],
                oos_max_dd=oos_m["max_drawdown"],
                oos_fills=oos_m["n_fills"],
                oos_exposure=oos_m["exposure"],
                oos_bars=oos_m["n_bars"],
                oos_years=oos_m["years"],
            )
        )
        # Keep the in-memory set honest even when it was never loaded: a later `seen()` will
        # load from the database, which by then includes this row.
        if self._seen is not None:
            self._seen.add(h)

    def commit(self) -> None:
        self._session.commit()

    # -- reads ----------------------------------------------------------------------------

    def total_trials(self) -> int:
        return self._session.scalar(select(func.count()).select_from(Trial)) or 0

    def family_rows(self) -> list[tuple]:
        """(market, strategy, symbol, timeframe, oos_sharpe) for every trial."""
        return [
            tuple(row)
            for row in self._session.execute(
                select(
                    Trial.market, Trial.strategy, Trial.symbol, Trial.timeframe, Trial.oos_sharpe
                )
            ).all()
        ]

    def trials_on(self, run_date: str | dt.date) -> int:
        if isinstance(run_date, str):
            run_date = dt.date.fromisoformat(run_date)
        return (
            self._session.scalar(
                select(func.count()).select_from(Trial).where(Trial.run_date == run_date)
            )
            or 0
        )

    def leaderboard(self, min_trades_oos: int, min_exposure: float, top_n: int) -> list[dict]:
        """The ranked rows, with the same filters and ordering the SQLite version used.

        `params` comes back as a dict from `JSONB` where SQLite returned a JSON string. The
        callers (`report.py`, `paper.py`) both `json.loads` it, so it is re-serialised here
        rather than edited at four call sites -- keeping the promise that a storage swap is not
        an edit to callers. `report.py` renders it and `paper.py` re-hashes it, and re-hashing
        needs exactly the `sort_keys=True` form `trial_hash` produced.
        """
        rows = self._session.execute(
            select(
                Trial.market,
                Trial.strategy,
                Trial.symbol,
                Trial.timeframe,
                Trial.params,
                Trial.is_sharpe,
                Trial.oos_sharpe,
                Trial.oos_cagr,
                Trial.oos_max_dd,
                Trial.oos_fills,
                Trial.oos_exposure,
                Trial.oos_years,
                Trial.run_date,
            )
            .where(
                Trial.oos_fills >= min_trades_oos,
                Trial.oos_exposure >= min_exposure,
                Trial.oos_sharpe > 0,
                Trial.is_sharpe > 0,
            )
            .order_by(Trial.oos_sharpe.desc())
            .limit(top_n)
        ).all()
        return [_as_legacy_dict(row._mapping) for row in rows]

    # -- paper candidates -----------------------------------------------------------------

    def paper_count(self) -> int:
        return self._session.scalar(select(func.count()).select_from(PaperCandidate)) or 0

    def paper_has(self, h: str) -> bool:
        return (
            self._session.scalar(select(PaperCandidate.hash).where(PaperCandidate.hash == h))
            is not None
        )

    def paper_add(
        self,
        h: str,
        promoted_at: str | dt.datetime,
        market: str,
        strategy: str,
        symbol: str,
        timeframe: str,
        params: str | dict,
        oos_sharpe: float,
        sharpe_2x: float,
        neighbor_med: float,
        wf_pos: int,
        wf_active: int,
        wf_med: float,
        corr_max: float,
    ) -> None:
        """Promote one row. `paper.py` passes `params` already JSON-encoded; both forms work."""
        if isinstance(params, str):
            params = json.loads(params)
        self._session.execute(
            _insert_ignore(PaperCandidate, self._dialect).values(
                hash=h,
                promoted_at=_as_utc(promoted_at),
                market=market,
                strategy=strategy,
                symbol=symbol,
                timeframe=timeframe,
                params=params,
                promoted_oos_sharpe=oos_sharpe,
                sharpe_2x=sharpe_2x,
                neighbor_med=neighbor_med,
                wf_pos=wf_pos,
                wf_active=wf_active,
                wf_med=wf_med,
                corr_max=corr_max,
            )
        )

    def paper_all(self) -> list[dict]:
        rows = self._session.execute(
            select(
                PaperCandidate.hash,
                PaperCandidate.promoted_at,
                PaperCandidate.market,
                PaperCandidate.strategy,
                PaperCandidate.symbol,
                PaperCandidate.timeframe,
                PaperCandidate.params,
                PaperCandidate.promoted_oos_sharpe,
                PaperCandidate.sharpe_2x,
                PaperCandidate.neighbor_med,
                PaperCandidate.wf_pos,
                PaperCandidate.wf_active,
                PaperCandidate.wf_med,
                PaperCandidate.corr_max,
            ).order_by(PaperCandidate.promoted_at)
        ).all()
        return [_as_legacy_dict(row._mapping) for row in rows]

    def paper_score_add(
        self,
        h: str,
        scored_at: str | dt.datetime,
        fwd_days: int | None,
        fwd_bars: int | None,
        fwd_sharpe: float | None,
        fwd_return: float | None,
        fwd_max_dd: float | None,
    ) -> None:
        """Record one forward measurement. Append-only; a re-score is a new row (T83).

        `ON CONFLICT DO NOTHING` on `(hash, scored_at)` for the same reason `record` has it:
        the worker and the Windows scheduled task may both run a cycle, and two scores computed
        at the same instant are the same measurement, not an integrity error. The row that
        already landed wins, which keeps the history immutable rather than last-writer-wins.

        Every float is passed through as-is, `None` included. A candidate too young to have a
        Sharpe stores `NULL`, never `0.0` -- see the model docstring.
        """
        self._session.execute(
            _insert_ignore(PaperScore, self._dialect, ["hash", "scored_at"]).values(
                hash=h,
                scored_at=_as_utc(scored_at),
                fwd_days=fwd_days,
                fwd_bars=fwd_bars,
                fwd_sharpe=fwd_sharpe,
                fwd_return=fwd_return,
                fwd_max_dd=fwd_max_dd,
            )
        )

    def paper_scores_latest(self) -> dict[str, dict]:
        """The most recent score per candidate, keyed by hash.

        `DISTINCT ON` on Postgres; the SQLite tests take the correlated-subquery path because
        SQLite has no `DISTINCT ON`. Both are served by `ix_paper_scores_hash_scored_at`.
        """
        if self._dialect == "postgresql":
            stmt = (
                select(PaperScore)
                .distinct(PaperScore.hash)
                .order_by(PaperScore.hash, PaperScore.scored_at.desc())
            )
        else:
            inner = aliased(PaperScore)
            newest = (
                select(func.max(inner.scored_at))
                .where(inner.hash == PaperScore.hash)
                .scalar_subquery()
            )
            stmt = select(PaperScore).where(PaperScore.scored_at == newest)
        out: dict[str, dict] = {}
        for row in self._session.execute(stmt).scalars():
            out[row.hash] = {
                "scored_at": row.scored_at,
                "fwd_days": row.fwd_days,
                "fwd_bars": row.fwd_bars,
                "fwd_sharpe": row.fwd_sharpe,
                "fwd_return": row.fwd_return,
                "fwd_max_dd": row.fwd_max_dd,
            }
        return out

    def paper_score_history(self, h: str) -> list[dict]:
        """One candidate's full trajectory, oldest first -- the point of storing history."""
        rows = self._session.execute(
            select(PaperScore).where(PaperScore.hash == h).order_by(PaperScore.scored_at)
        ).scalars()
        return [
            {
                "scored_at": r.scored_at,
                "fwd_days": r.fwd_days,
                "fwd_bars": r.fwd_bars,
                "fwd_sharpe": r.fwd_sharpe,
                "fwd_return": r.fwd_return,
                "fwd_max_dd": r.fwd_max_dd,
            }
            for r in rows
        ]

    def close(self) -> None:
        self._session.close()


def _as_utc(value: str | dt.datetime) -> dt.datetime:
    """Invariant 4 at this module's boundary: what goes in is tz-aware UTC or it is rejected.

    `paper.py` stamps `datetime.now(timezone.utc).isoformat()`, so the string branch is the
    everyday one. A naive datetime is treated as UTC rather than refused, matching what
    `UTCDateTime` itself does one layer down.
    """
    if isinstance(value, str):
        value = dt.datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _as_legacy_dict(mapping) -> dict[str, Any]:
    """Row -> the dict shape the SQLite `cursor.description` path produced.

    Two fixups keep the callers untouched: `params` goes back to the sorted-key JSON string they
    `json.loads`, and `run_date`/`promoted_at` go back to ISO strings, because `report.py`
    interpolates them straight into HTML and `paper.py` compares them as text.
    """
    out = dict(mapping)
    if isinstance(out.get("params"), dict):
        out["params"] = json.dumps(out["params"], sort_keys=True)
    for key in ("run_date", "promoted_at"):
        if isinstance(out.get(key), (dt.date, dt.datetime)):
            out[key] = out[key].isoformat()
    return out
