"""The research module's tables, in the `research` schema (T77, extended T83).

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

__all__ = ["Base", "PaperCandidate", "PaperScore", "Trial"]

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


class PaperScore(Base):
    """One forward-performance measurement of one paper candidate, at one moment.

    **The gap this closes.** Invariant 9 calls the paper watchlist the only genuinely
    out-of-sample evidence EdgeLab has, and `paper.forward_stats` has always computed it
    correctly -- signals warmed up on full history, performance sliced to bars strictly after
    `promoted_at`. But the result went into a generated HTML report and nowhere else. The
    database held promotion-time gates only, so the API and the MCP connector could describe
    what a candidate looked like on the day it was promoted and nothing about what happened
    next. A watchlist that is promoted and then never scored is exactly the authoritative-
    looking artifact the module's own honesty rules exist to prevent.

    **Append-only, one row per scoring run.** `(hash, scored_at)` is the primary key and a row
    is never rewritten -- the same rule as `terminal.observations` (invariant 10) and the GEX
    decision log, for the same reason. The single fact worth knowing about a forward record is
    its *trajectory*: a candidate promoted at OOS Sharpe 2.8 that reads 1.1 after two months is
    telling you the edge is decaying, and an UPDATE that kept only the latest number would
    erase precisely that. Storing the history also means a score can never be quietly restated
    after the fact.

    **Nulls are not zeros.** A candidate whose promotion is a week old has too few bars to have
    a Sharpe at all, and that is `None` -- unknown -- not `0.0`, which would read as "flat" and
    drag an honest table toward a conclusion the data does not support. Same rule invariant 3
    states for open interest, and what the MCP connector promises its callers.
    """

    __tablename__ = "paper_scores"

    #: The candidate scored. Not a ForeignKey: `paper_candidates` is written by a separate
    #: process under `ON CONFLICT DO NOTHING`, and a constraint here would make a scoring run
    #: fail on a candidate row that is merely not committed yet. The join is by hash either way.
    hash: Mapped[str] = mapped_column(String(24), primary_key=True)

    #: When this measurement was taken -- *not* the last bar it covers. Two scores of the same
    #: candidate differ by the bars that arrived between them, so this is the axis the
    #: trajectory is read along.
    scored_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime(timezone=True), primary_key=True
    )

    #: Calendar days since `promoted_at`, and bars actually traded since. Both are real counts,
    #: so `0` here means zero and is honest. They are the first thing to read: every float
    #: below is noise until these are large enough to mean something.
    fwd_days: Mapped[int | None] = mapped_column(Integer)
    fwd_bars: Mapped[int | None] = mapped_column(Integer)

    #: Forward performance on bars strictly after promotion. Nullable, and null whenever there
    #: were fewer than two forward bars to compute from.
    fwd_sharpe: Mapped[float | None] = mapped_column(Float)
    fwd_return: Mapped[float | None] = mapped_column(Float)
    fwd_max_dd: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        # The two queries that exist: "latest score per candidate" (DISTINCT ON (hash) ORDER BY
        # hash, scored_at DESC) and "this candidate's trajectory". Both are served by the hash
        # prefix; the descending `scored_at` matches the order both read in.
        Index("ix_paper_scores_hash_scored_at", "hash", scored_at.desc()),
    )
