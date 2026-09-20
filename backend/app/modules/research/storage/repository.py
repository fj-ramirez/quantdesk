"""Read queries behind `/api/research/*` (T78).

Separate from `registry.py` on purpose. `Registry` is built for a *cycle*: it holds one session
open for minutes, and its `seen()` loads all 134k hashes into memory because the search asks it
a hundred thousand questions. A web request wants none of that -- it wants one short query, a
`LIMIT`, and a session that closes. Reusing `Registry` here would make every page load pay for
machinery it does not use, and would eventually tempt someone to add pagination to the search's
hot path.

Same shape as `app/modules/gex/storage/*_repository.py`: plain functions taking an explicit
`session_factory`, so a test passes a SQLite one and nothing reaches for a global.

**The noise ceiling is computed here, never client-side.** It is the number that decides whether
a row means anything, it depends on the total trial count (which the browser has no reason to
know), and a frontend that computed it slightly differently would produce a leaderboard that
looked authoritative and was not. See `plans/quantdesk/02-research-module.md`.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.core.db import get_session_factory
from app.modules.research.backtest import noise_ceiling
from app.modules.research.models.db import PaperCandidate, PaperScore, Trial

__all__ = [
    "LeaderboardFilters",
    "LeaderboardPage",
    "RegistryStatus",
    "leaderboard_page",
    "paper_candidates",
    "paper_forward_scores",
    "registry_status",
    "trial_by_hash",
]


@dataclass(frozen=True)
class LeaderboardFilters:
    """What the filter bar can narrow by.

    `min_trades_oos` and `min_exposure` default to the same values `config/research.yaml`'s
    `filters` block uses for the static report, so the page and the HTML agree out of the box --
    an acceptance criterion of this task, and a disagreement would mean one of them is wrong
    rather than that they are two opinions.
    """

    market: str | None = None
    strategy: str | None = None
    timeframe: str | None = None
    min_trades_oos: int = 20
    min_exposure: float = 0.02


@dataclass(frozen=True)
class LeaderboardRow:
    hash: str
    market: str
    strategy: str
    symbol: str
    timeframe: str
    params: dict[str, Any]
    is_sharpe: float | None
    oos_sharpe: float | None
    oos_cagr: float | None
    oos_max_dd: float | None
    oos_fills: int | None
    oos_exposure: float | None
    oos_years: float | None
    run_date: dt.date
    #: This row's own ceiling, at its own OOS span -- not the headline one. A three-year row and
    #: a fifteen-year row are held to very different standards, and `report.py` has always
    #: highlighted against a per-row ceiling for exactly that reason.
    noise_ceiling: float
    above_ceiling: bool


@dataclass(frozen=True)
class LeaderboardPage:
    rows: list[LeaderboardRow]
    #: Rows matching the filters, before `limit`/`offset` -- what the pager counts.
    total: int
    #: Every trial in the registry, losers included. The ceiling's denominator, and the reason
    #: losing trials are never deleted.
    total_trials: int
    #: The headline ceiling, at the median OOS span across the *matching* rows.
    noise_ceiling: float
    median_oos_years: float
    limit: int
    offset: int


@dataclass(frozen=True)
class RegistryStatus:
    total_trials: int
    paper_candidates: int
    #: Distinct `run_date` values: how many days a cycle has recorded anything.
    cycles: int
    last_run_date: dt.date | None
    trials_last_cycle: int
    markets: list[str]
    strategies: list[str]
    timeframes: list[str]


def _factory(session_factory: sessionmaker[Session] | None) -> sessionmaker[Session]:
    return session_factory or get_session_factory()


def _filtered(stmt, f: LeaderboardFilters):
    """The filter clause, applied identically to the count and the page.

    One function rather than two copies: a pager whose count and page disagree shows a
    "1-40 of 900" that does not survive clicking through, and that is exactly the kind of bug
    that gets noticed months later.
    """
    stmt = stmt.where(
        Trial.oos_fills >= f.min_trades_oos,
        Trial.oos_exposure >= f.min_exposure,
        # Unchanged from the SQLite leaderboard: a row that lost money in-sample is not an edge
        # that decayed, it is a row that never worked.
        Trial.oos_sharpe > 0,
        Trial.is_sharpe > 0,
    )
    if f.market:
        stmt = stmt.where(Trial.market == f.market)
    if f.strategy:
        stmt = stmt.where(Trial.strategy == f.strategy)
    if f.timeframe:
        stmt = stmt.where(Trial.timeframe == f.timeframe)
    return stmt


def leaderboard_page(
    filters: LeaderboardFilters | None = None,
    *,
    limit: int = 40,
    offset: int = 0,
    session_factory: sessionmaker[Session] | None = None,
) -> LeaderboardPage:
    """One page of the ranked leaderboard, with the ceiling that gives it meaning."""
    f = filters or LeaderboardFilters()

    with _factory(session_factory)() as session:
        total = session.scalar(_filtered(select(func.count()).select_from(Trial), f)) or 0
        # The denominator is the *whole* registry, never the filtered set. Narrowing to one
        # market does not make you have run fewer experiments, and a ceiling that fell when you
        # picked a filter would let anyone filter their way to a green row.
        total_trials = session.scalar(select(func.count()).select_from(Trial)) or 0

        rows = session.execute(
            _filtered(select(Trial), f).order_by(Trial.oos_sharpe.desc()).limit(limit).offset(offset)
        ).scalars().all()

        # Median over the returned page, matching what `report.py` prints in its ceiling
        # sentence ("at the median OOS span of N years").
        spans = [r.oos_years for r in rows if r.oos_years]
        median_years = statistics.median(spans) if spans else 0.0

        out: list[LeaderboardRow] = []
        for r in rows:
            row_ceiling = noise_ceiling(total_trials, r.oos_years or 0.0)
            out.append(
                LeaderboardRow(
                    hash=r.hash,
                    market=r.market,
                    strategy=r.strategy,
                    symbol=r.symbol,
                    timeframe=r.timeframe,
                    params=r.params,
                    is_sharpe=r.is_sharpe,
                    oos_sharpe=r.oos_sharpe,
                    oos_cagr=r.oos_cagr,
                    oos_max_dd=r.oos_max_dd,
                    oos_fills=r.oos_fills,
                    oos_exposure=r.oos_exposure,
                    oos_years=r.oos_years,
                    run_date=r.run_date,
                    noise_ceiling=row_ceiling,
                    above_ceiling=bool(r.oos_sharpe and r.oos_sharpe > row_ceiling),
                )
            )

    return LeaderboardPage(
        rows=out,
        total=total,
        total_trials=total_trials,
        noise_ceiling=noise_ceiling(total_trials, median_years),
        median_oos_years=median_years,
        limit=limit,
        offset=offset,
    )


def trial_by_hash(
    trial_hash: str, *, session_factory: sessionmaker[Session] | None = None
) -> Trial | None:
    with _factory(session_factory)() as session:
        trial = session.get(Trial, trial_hash)
        if trial is not None:
            session.expunge(trial)
        return trial


def paper_candidates(
    *, session_factory: sessionmaker[Session] | None = None
) -> list[PaperCandidate]:
    """The watchlist, oldest promotion first.

    Ordered by `promoted_at` rather than by performance on purpose: this is a forward record,
    and sorting it by how well a candidate has done since would quietly turn the one honest
    table in the app into another leaderboard.
    """
    with _factory(session_factory)() as session:
        rows = list(
            session.execute(
                select(PaperCandidate).order_by(PaperCandidate.promoted_at)
            ).scalars().all()
        )
        for r in rows:
            session.expunge(r)
        return rows


def paper_forward_scores(
    *, session_factory: sessionmaker[Session] | None = None
) -> dict[str, PaperScore]:
    """The latest forward score per candidate, keyed by hash (T83).

    A separate query rather than a join onto `paper_candidates`, so that a watchlist with no
    scores yet -- every deployment, until the next cycle runs -- still renders. A candidate
    missing from this mapping has not been measured, which the API reports as `null` rather
    than as a zero.

    `DISTINCT ON` on Postgres; the SQLite path the offline tests take has no such clause, so it
    correlates a `max(scored_at)` subquery instead. Both use `ix_paper_scores_hash_scored_at`.
    """
    with _factory(session_factory)() as session:
        if session.bind is not None and session.bind.dialect.name == "postgresql":
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
        rows = list(session.execute(stmt).scalars().all())
        for r in rows:
            session.expunge(r)
        return {r.hash: r for r in rows}


def registry_status(*, session_factory: sessionmaker[Session] | None = None) -> RegistryStatus:
    """The status strip: how much has been tried, and when the search last did anything."""
    with _factory(session_factory)() as session:
        total_trials = session.scalar(select(func.count()).select_from(Trial)) or 0
        n_paper = session.scalar(select(func.count()).select_from(PaperCandidate)) or 0
        cycles = session.scalar(select(func.count(func.distinct(Trial.run_date)))) or 0
        last_run = session.scalar(select(func.max(Trial.run_date)))
        trials_last = (
            session.scalar(
                select(func.count()).select_from(Trial).where(Trial.run_date == last_run)
            )
            or 0
            if last_run
            else 0
        )

        def _distinct(column) -> list[str]:
            return sorted(v for v in session.scalars(select(column).distinct()).all() if v)

        return RegistryStatus(
            total_trials=total_trials,
            paper_candidates=n_paper,
            cycles=cycles,
            last_run_date=last_run,
            trials_last_cycle=trials_last,
            markets=_distinct(Trial.market),
            strategies=_distinct(Trial.strategy),
            timeframes=_distinct(Trial.timeframe),
        )
