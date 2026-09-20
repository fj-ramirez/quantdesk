"""T83: the paper watchlist's forward scores, stored rather than rendered and thrown away.

`paper.forward_stats` always computed forward performance correctly -- signals warmed up on the
full history, performance sliced to bars strictly after `promoted_at`. The result went into a
generated HTML report and nowhere else, so the database held promotion-time gates only and the
API could describe a commitment but never its outcome. Invariant 9 calls that forward record
the only genuinely out-of-sample evidence EdgeLab has; a watchlist promoted and then never
scored is exactly the authoritative-looking artifact the invariant exists to prevent.

Two properties carry the weight here, and both are about honesty rather than plumbing:

* **A score is appended, never overwritten.** The trajectory is the evidence -- a candidate
  promoted at Sharpe 2.8 reading 1.1 two months later is a decaying edge, and last-writer-wins
  would erase that silently.
* **A null is not a zero.** A candidate too young to have a Sharpe stores `NULL`, not `0.0`,
  which would render as a flat forward record and read as a measured result.

Offline on SQLite, same `schema_translate_map` arrangement as the rest of the suite.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.core.db import get_engine, get_sessionmaker
from app.modules.research.models.db import Base, PaperScore
from app.modules.research.registry import Registry

PROMOTED = "2026-07-03T12:44:54+00:00"


@pytest.fixture
def registry(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'research.db'}")
    Base.metadata.create_all(engine)
    reg = Registry(session_factory=get_sessionmaker(engine))
    yield reg
    reg.close()
    engine.dispose()


def _promote(reg: Registry, h: str = "abc123") -> str:
    reg.paper_add(
        h, PROMOTED, "futures", "rsi_meanrev", "NQ=F", "1h",
        {"n": 4, "regime": "low_vol"}, 2.807, 2.244, 1.829, 5, 6, 1.151, 0.16,
    )
    return h


# --- append-only ----------------------------------------------------------------------------


def test_scoring_twice_keeps_both_measurements(registry: Registry) -> None:
    """The trajectory is the point. Two runs leave two rows, not one overwritten one."""
    h = _promote(registry)
    registry.paper_score_add(h, "2026-08-01T00:00:00+00:00", 29, 120, 2.1, 0.04, -0.02)
    registry.paper_score_add(h, "2026-09-01T00:00:00+00:00", 60, 260, 1.1, 0.05, -0.06)
    registry.commit()

    history = registry.paper_score_history(h)
    assert [r["fwd_sharpe"] for r in history] == [2.1, 1.1], (
        "history must be oldest-first and must keep every measurement -- an edge decaying "
        "from 2.1 to 1.1 is the single most useful thing this table can show."
    )


def test_rescoring_at_the_same_instant_is_not_an_error(registry: Registry) -> None:
    """Two writers, one cycle. `ON CONFLICT DO NOTHING` on the real key, not on hash alone.

    The worker and the Windows scheduled task may both run a cycle. Two scores computed at the
    same instant are the same measurement; the row that landed first wins, which keeps the
    history immutable rather than last-writer-wins.
    """
    h = _promote(registry)
    registry.paper_score_add(h, "2026-08-01T00:00:00+00:00", 29, 120, 2.1, 0.04, -0.02)
    registry.paper_score_add(h, "2026-08-01T00:00:00+00:00", 29, 120, 9.9, 0.99, -0.01)
    registry.commit()

    history = registry.paper_score_history(h)
    assert len(history) == 1
    assert history[0]["fwd_sharpe"] == 2.1, "the first write wins; a re-score must not restate"


def test_hash_alone_is_not_the_conflict_target(registry: Registry) -> None:
    """The bug this guards: `_insert_ignore` defaulted to `["hash"]` for every table.

    `paper_scores` is keyed on `(hash, scored_at)` precisely so one candidate can have many
    rows. Had the default been kept, the second and every later measurement would have been
    swallowed as a duplicate and the table would hold exactly one score per candidate --
    looking like it worked, while storing no trajectory at all.
    """
    h = _promote(registry)
    for day, sharpe in ((1, 3.0), (8, 2.0), (15, 1.0)):
        registry.paper_score_add(
            h, f"2026-08-{day:02d}T00:00:00+00:00", day, day * 10, sharpe, 0.01, -0.01
        )
    registry.commit()
    assert len(registry.paper_score_history(h)) == 3


# --- nulls are not zeros --------------------------------------------------------------------


def test_an_unmeasured_candidate_stores_null_not_zero(registry: Registry) -> None:
    """`None` means "too few bars to know"; `0.0` would mean "measured, and flat"."""
    h = _promote(registry)
    registry.paper_score_add(h, "2026-07-04T00:00:00+00:00", 1, 0, None, None, None)
    registry.commit()

    row = registry.paper_score_history(h)[0]
    assert row["fwd_bars"] == 0, "zero bars traded is a real count, and zero is honest there"
    assert row["fwd_sharpe"] is None
    assert row["fwd_return"] is None
    assert row["fwd_max_dd"] is None


def test_null_survives_the_round_trip_to_the_database(registry: Registry) -> None:
    """Guards the column itself, not just the dict the registry hands back."""
    h = _promote(registry)
    registry.paper_score_add(h, "2026-07-04T00:00:00+00:00", 1, 0, None, None, None)
    registry.commit()

    stored = registry._session.execute(
        select(PaperScore.fwd_sharpe).where(PaperScore.hash == h)
    ).scalar_one()
    assert stored is None


# --- the latest-score query -----------------------------------------------------------------


def test_latest_score_takes_the_newest_row_per_candidate(registry: Registry) -> None:
    a, b = _promote(registry, "aaa111"), _promote(registry, "bbb222")
    registry.paper_score_add(a, "2026-08-01T00:00:00+00:00", 29, 120, 2.1, 0.04, -0.02)
    registry.paper_score_add(a, "2026-09-01T00:00:00+00:00", 60, 260, 1.1, 0.05, -0.06)
    registry.paper_score_add(b, "2026-08-15T00:00:00+00:00", 43, 200, 0.4, 0.01, -0.03)
    registry.commit()

    latest = registry.paper_scores_latest()
    assert latest[a]["fwd_sharpe"] == 1.1, "newest measurement, not the first or the best"
    assert latest[b]["fwd_sharpe"] == 0.4
    assert latest[a]["fwd_days"] == 60


def test_an_unscored_candidate_is_absent_rather_than_zeroed(registry: Registry) -> None:
    """Absence is the honest answer for a candidate promoted since the last cycle.

    The API turns this into nulls across the forward columns; what it must never do is invent
    a zero, which would put an unmeasured candidate on the same footing as one measured flat.
    """
    _promote(registry, "never_scored")
    registry.commit()
    assert registry.paper_scores_latest() == {}


# --- what the API serves --------------------------------------------------------------------


def test_scored_at_is_stored_as_tz_aware_utc(registry: Registry) -> None:
    """Invariant 4 at the DB boundary, same as `promoted_at`."""
    h = _promote(registry)
    registry.paper_score_add(h, "2026-08-01T00:00:00+00:00", 29, 120, 2.1, 0.04, -0.02)
    registry.commit()

    scored_at = registry.paper_score_history(h)[0]["scored_at"]
    assert scored_at.tzinfo is not None
    assert scored_at.utcoffset() == dt.timedelta(0)
