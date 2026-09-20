"""The research module's two tables, in the `research` schema (T77).

The SQLite registry these replace had two flat tables, no foreign keys and no views, which is
why the port is a type change rather than a redesign. Three things did change, each because
Postgres makes the better option free:

**`params` is `JSONB`, not `TEXT`.** It was a JSON string only because SQLite has no better
option. `JSONB` turns "every trial where lookback > 40" into a query instead of 134,377
`json.loads` calls. Critically this does **not** touch `trial_hash`, which is computed from
`json.dumps(params, sort_keys=True)` *before* storage and never derived from what came back --
so hashes stored under SQLite still match what the running code computes. The entire value of
the registry is that a cycle never repeats work, and that guarantee is exactly that equality.

**`run_date` and `promoted_at` are real dates and timestamps**, not `TEXT`. Invariant 4 is not
negotiable for a new module, so `promoted_at` is `UTCDateTime` (tz-aware UTC, enforced at the
boundary). `run_date` stays a `Date`: it is a calendar day stamped by the cycle --
`date.today()` -- and `trials_on(day)` counts by day. Widening it to a timestamp would invent a
precision the value never had.

**Floats are `DOUBLE PRECISION`.** SQLite's `REAL` is already a 64-bit float, so every value
transfers exactly. `NUMERIC` would have been the subtle disaster: it would round Sharpes, and
the leaderboard is *ranked* by Sharpe, so rounding reorders rows.

`hash` stays the primary key, which is what makes two writers against one database harmless --
the Windows scheduled task and the worker can both run, and a collision is `ON CONFLICT DO
NOTHING` rather than an integrity error. See `registry.py`.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, Float, Index, Integer, MetaData, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

from app.core.schemas import SCHEMA_RESEARCH
from app.modules.gex.models.db import UTCDateTime

__all__ = ["Base", "PaperCandidate", "Trial"]

#: `JSONB` on Postgres, plain `JSON` on SQLite. The tests run offline against SQLite (same
#: arrangement as gex -- see `app.core.db.get_engine`), and `JSONB` is a Postgres-only type that
#: SQLite's dialect cannot compile at all. `with_variant` is SQLAlchemy's designed way to say
#: "this type, except there", and keeps one column definition rather than two model classes.
_ParamsJSON = JSONB().with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    """Declarative base for the research module. Its schema is decided once, here.

    Same rule as `app.modules.gex.models.db.Base` and invariant 8: a table added later is
    `research.something` because this metadata says so, never because a model remembered a
    `__table_args__`. A separate `Base` -- and therefore a separate `MetaData` -- from gex's on
    purpose: one metadata per schema is what lets a test `create_all` one module without
    reaching across into another's tables.
    """

    metadata = MetaData(schema=SCHEMA_RESEARCH)


class Trial(Base):
    """One backtested combination: market x strategy x symbol x timeframe x params.

    Every trial ever run is here, **including the losers** -- that is not sloppiness, it is the
    denominator. `noise_ceiling(total_trials, years)` estimates the best OOS Sharpe pure luck
    would produce across this many attempts, and a leaderboard row only means something
    measured against it. Deleting losing trials would silently lower the ceiling and make every
    surviving row look better than it is.
    """

    __tablename__ = "trials"

    hash: Mapped[str] = mapped_column(String(24), primary_key=True)
    run_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    params: Mapped[dict] = mapped_column(_ParamsJSON, nullable=False)

    # In-sample. Nullable because a degenerate backtest (no fills at all) legitimately has no
    # Sharpe to report, and `None` must stay distinguishable from `0.0` -- the same rule
    # invariant 3 states for open interest.
    is_sharpe: Mapped[float | None] = mapped_column(Float)
    is_cagr: Mapped[float | None] = mapped_column(Float)
    is_max_dd: Mapped[float | None] = mapped_column(Float)
    is_fills: Mapped[int | None] = mapped_column(Integer)

    # Out-of-sample: the held-out segment. The search never consults these to decide what to
    # try next; selection happens at report time. That separation is the point of the split.
    oos_sharpe: Mapped[float | None] = mapped_column(Float)
    oos_cagr: Mapped[float | None] = mapped_column(Float)
    oos_max_dd: Mapped[float | None] = mapped_column(Float)
    oos_fills: Mapped[int | None] = mapped_column(Integer)
    oos_exposure: Mapped[float | None] = mapped_column(Float)
    oos_bars: Mapped[int | None] = mapped_column(Integer)
    oos_years: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        # The one index the SQLite registry had, carried over: `idx_trials_oos ON trials
        # (oos_sharpe DESC)`. It serves the leaderboard's `ORDER BY oos_sharpe DESC LIMIT n`,
        # which is the only query anyone runs interactively.
        Index("ix_trials_oos_sharpe", oos_sharpe.desc()),
        # T77 addition, and the one the original could not afford: the leaderboard filters on
        # `oos_fills >= n AND oos_exposure >= x` before ordering. On 134k rows SQLite scanned.
        Index("ix_trials_filters", "oos_fills", "oos_exposure"),
        Index("ix_trials_run_date", "run_date"),
    )


class PaperCandidate(Base):
    """A trial promoted to the forward-tracking watchlist -- the real gatekeeper.

    A row here cleared the noise ceiling by `min_fraction_of_ceiling`, stayed positive at
    **doubled** costs (`sharpe_2x`), survived the parameter-neighbourhood check
    (`neighbor_med`), passed the walk-forward consistency gate (`wf_*`), and was not too
    correlated with what is already on the list (`corr_max`). Promotion is the only moment
    EdgeLab commits to anything, and forward performance from `promoted_at` onward is the only
    number here that was never fitted.
    """

    __tablename__ = "paper_candidates"

    hash: Mapped[str] = mapped_column(String(24), primary_key=True)
    promoted_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(timezone=True), nullable=False)
    market: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False)
    params: Mapped[dict] = mapped_column(_ParamsJSON, nullable=False)

    promoted_oos_sharpe: Mapped[float | None] = mapped_column(Float)
    #: OOS Sharpe recomputed at `robustness.cost_multiplier` (2x) the configured costs.
    sharpe_2x: Mapped[float | None] = mapped_column(Float)
    #: Median OOS Sharpe of the adjacent parameter values -- a lone spike is overfitting.
    neighbor_med: Mapped[float | None] = mapped_column(Float)
    #: Walk-forward: windows profitable, windows the strategy actually traded in, median Sharpe.
    wf_pos: Mapped[int | None] = mapped_column(Integer)
    wf_active: Mapped[int | None] = mapped_column(Integer)
    wf_med: Mapped[float | None] = mapped_column(Float)
    #: Highest correlation with any existing watchlist member at promotion time.
    corr_max: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (Index("ix_paper_candidates_promoted_at", "promoted_at"),)
