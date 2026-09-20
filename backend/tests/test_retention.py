"""Tests for `app/modules/gex/jobs/retention.py` (T32).

The acceptance criterion this file exists to prove, from
`plans/continuous-feed/01-capture-integrity.md`: with rows spanning 60 days of mixed `is_eod`,
one run deletes exactly the non-EOD strike rows older than the cutoff, leaves every EOD strike
row, every `gex_levels` row, every `snapshots` row and every Parquet file untouched, and
re-running is a no-op.

Offline: a temp-directory SQLite `session_factory`, the same isolation pattern used throughout
this suite.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.jobs.retention import prune_intraday_strike_detail
from app.modules.gex.models.db import Base, GexByStrike, GexLevel, Snapshot

NOW = dt.datetime(2026, 9, 11, 21, 0, tzinfo=dt.UTC)
RETENTION_DAYS = 30


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'retention.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def _add_snapshot(
    session, *, days_ago: int, is_eod: bool, strikes: int = 3, minutes: int = 0
) -> int:
    """One snapshot with a levels row and `strikes` strike rows, `days_ago` days before NOW.

    `minutes` shifts the timestamp further back, which callers need whenever two snapshots
    share a day: T71's `uq_snapshots_underlying_captured_at` forbids repeating the pair, so an
    EOD and an intraday row for the same instant cannot both exist. That is the constraint
    working as intended, not a fixture inconvenience.
    """
    captured_at = NOW - dt.timedelta(days=days_ago, minutes=minutes)
    row = Snapshot(
        underlying="SPX",
        captured_at=captured_at,
        source="cboe",
        spot=6500.0,
        contract_count=100,
        parquet_path=f"chains/SPX/d{days_ago}{'e' if is_eod else 'i'}.parquet",
        is_eod=is_eod,
    )
    session.add(row)
    session.commit()
    session.add(
        GexLevel(snapshot_id=row.id, filter="ALL", net_gex=1.0, computed_at=captured_at)
    )
    for n in range(strikes):
        session.add(
            GexByStrike(
                snapshot_id=row.id,
                filter="ALL",
                strike=6500.0 + n,
                call_gex=1.0,
                put_gex=-1.0,
                net_gex=0.0,
            )
        )
    session.commit()
    return row.id


@pytest.fixture
def sixty_days(session_factory):
    """Mixed EOD/intraday snapshots spanning 60 days, straddling the 30-day cutoff."""
    ids: dict[str, int] = {}
    with session_factory() as session:
        ids["old_intraday"] = _add_snapshot(session, days_ago=60, is_eod=False)
        ids["old_intraday_2"] = _add_snapshot(session, days_ago=45, is_eod=False)
        ids["old_eod"] = _add_snapshot(session, days_ago=60, is_eod=True, minutes=5)
        ids["fresh_intraday"] = _add_snapshot(session, days_ago=5, is_eod=False)
        ids["fresh_eod"] = _add_snapshot(session, days_ago=5, is_eod=True, minutes=5)
    return session_factory, ids


def _strike_owners(session_factory) -> set[int]:
    with session_factory() as session:
        return set(session.execute(select(GexByStrike.snapshot_id)).scalars().all())


def test_prunes_only_old_intraday_strike_detail(sixty_days):
    factory, ids = sixty_days

    result = prune_intraday_strike_detail(
        session_factory=factory, retention_days=RETENTION_DAYS, now=NOW
    )

    assert result.snapshots_pruned == 2
    assert result.rows_deleted == 6
    assert _strike_owners(factory) == {
        ids["old_eod"],
        ids["fresh_intraday"],
        ids["fresh_eod"],
    }


def test_leaves_levels_snapshots_and_paths_untouched(sixty_days):
    """The summary rows are what keep a pruned day legible in history views and T20's
    timeline, so they must survive along with the index row and its Parquet pointer."""
    factory, ids = sixty_days

    prune_intraday_strike_detail(
        session_factory=factory, retention_days=RETENTION_DAYS, now=NOW
    )

    with factory() as session:
        assert len(session.execute(select(GexLevel)).scalars().all()) == 5
        snapshots = session.execute(select(Snapshot).order_by(Snapshot.id)).scalars().all()
        assert len(snapshots) == 5
        assert all(row.parquet_path for row in snapshots)
        pruned = session.get(Snapshot, ids["old_intraday"])
        assert pruned is not None
        assert pruned.parquet_path == "chains/SPX/d60i.parquet"


def test_rerunning_is_a_noop(sixty_days):
    factory, _ = sixty_days

    first = prune_intraday_strike_detail(
        session_factory=factory, retention_days=RETENTION_DAYS, now=NOW
    )
    second = prune_intraday_strike_detail(
        session_factory=factory, retention_days=RETENTION_DAYS, now=NOW
    )

    assert first.rows_deleted == 6
    assert second.snapshots_pruned == 0
    assert second.rows_deleted == 0


def test_zero_disables_pruning_entirely(sixty_days):
    """For a user who would rather buy disk than lose detail."""
    factory, _ = sixty_days
    before = _strike_owners(factory)

    result = prune_intraday_strike_detail(session_factory=factory, retention_days=0, now=NOW)

    assert result.disabled is True
    assert result.cutoff is None
    assert _strike_owners(factory) == before


def test_negative_retention_also_disables_rather_than_deleting_everything(sixty_days):
    """A misconfigured negative value must fail safe. Computing a cutoff in the *future* from
    it would delete every intraday strike row in the database."""
    factory, _ = sixty_days
    before = _strike_owners(factory)

    result = prune_intraday_strike_detail(session_factory=factory, retention_days=-5, now=NOW)

    assert result.disabled is True
    assert _strike_owners(factory) == before


def test_cutoff_boundary_is_exclusive_on_the_old_side(session_factory):
    """A snapshot exactly at the cutoff is kept; one a minute older goes. Pinned down because
    an off-by-one here silently deletes a day nobody asked to lose."""
    with session_factory() as session:
        at_cutoff = _add_snapshot(session, days_ago=RETENTION_DAYS, is_eod=False)
        just_past = Snapshot(
            underlying="SPX",
            captured_at=NOW - dt.timedelta(days=RETENTION_DAYS, minutes=1),
            source="cboe",
            spot=6500.0,
            contract_count=100,
            parquet_path="chains/SPX/edge.parquet",
            is_eod=False,
        )
        session.add(just_past)
        session.commit()
        session.add(
            GexByStrike(
                snapshot_id=just_past.id,
                filter="ALL",
                strike=6500.0,
                call_gex=1.0,
                put_gex=-1.0,
                net_gex=0.0,
            )
        )
        session.commit()
        just_past_id = just_past.id

    prune_intraday_strike_detail(
        session_factory=session_factory, retention_days=RETENTION_DAYS, now=NOW
    )

    owners = _strike_owners(session_factory)
    assert at_cutoff in owners
    assert just_past_id not in owners


def test_empty_database_is_not_an_error(session_factory):
    result = prune_intraday_strike_detail(
        session_factory=session_factory, retention_days=RETENTION_DAYS, now=NOW
    )
    assert result.snapshots_pruned == 0
    assert result.rows_deleted == 0
    assert result.disabled is False


def test_prunes_across_more_snapshots_than_one_chunk(session_factory):
    """The chunked commit loop must cover every stale snapshot, not just the first batch --
    the case the first run after enabling T18 will actually hit."""
    from app.modules.gex.jobs.retention import _CHUNK_SIZE

    count = _CHUNK_SIZE * 2 + 7
    with session_factory() as session:
        for n in range(count):
            _add_snapshot(session, days_ago=40, is_eod=False, strikes=1, minutes=n)

    result = prune_intraday_strike_detail(
        session_factory=session_factory, retention_days=RETENTION_DAYS, now=NOW
    )

    assert result.snapshots_pruned == count
    assert _strike_owners(session_factory) == set()
