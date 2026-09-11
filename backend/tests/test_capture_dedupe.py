"""Tests for T71's content fingerprint and the capture path's two duplicate keys.

Companion to `test_capture.py`, which already covers the `(underlying, captured_at)` key. What
is exercised here is the case that key cannot see and that T18's 15-minute polling introduces:
a chain arriving under a **fresh** vendor timestamp with **identical** content, because Cboe's
`timestamp` is payload-generation time and keeps advancing while the quotes are frozen (T34).

Offline throughout, using `test_capture.py`'s own stub provider and SQLite session factory.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest
from sqlalchemy import select

from app.jobs.capture import capture_snapshot
from app.models.chain import ChainSnapshot, Underlying
from app.models.db import Base, Snapshot, get_engine, get_sessionmaker
from app.storage.fingerprint import _token, chain_fingerprint

from .test_capture import StubProvider, make_snapshot


@pytest.fixture
def session_factory(tmp_path):
    """Declared here rather than imported from `test_capture`: importing a fixture makes every
    test that names it as a parameter shadow the imported symbol. Each test module in this
    suite owns its own copy of this four-line fixture -- the established pattern here."""
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def _with_captured_at(snapshot: ChainSnapshot, captured_at: dt.datetime) -> ChainSnapshot:
    """Same chain, later vendor timestamp -- exactly what a poll into a stalled feed returns."""
    return snapshot.model_copy(update={"captured_at": captured_at})


# --- the fingerprint itself -----------------------------------------------------------------


def test_fingerprint_is_stable_across_calls():
    snapshot = make_snapshot()
    assert chain_fingerprint(snapshot) == chain_fingerprint(snapshot)


def test_fingerprint_ignores_captured_at():
    """The whole premise: a moving vendor clock over an unmoved chain is not a content change."""
    snapshot = make_snapshot()
    later = _with_captured_at(snapshot, snapshot.captured_at + dt.timedelta(minutes=15))
    assert chain_fingerprint(later) == chain_fingerprint(snapshot)


def test_fingerprint_ignores_contract_order():
    snapshot = make_snapshot(n_contracts=4)
    shuffled = snapshot.model_copy(update={"contracts": tuple(reversed(snapshot.contracts))})
    assert chain_fingerprint(shuffled) == chain_fingerprint(snapshot)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bid", 1.25),
        ("ask", 2.5),
        ("iv", 0.21),
        ("open_interest", 101),
    ],
)
def test_fingerprint_changes_when_a_priced_field_moves(field, value):
    snapshot = make_snapshot()
    changed_first = snapshot.contracts[0].model_copy(update={field: value})
    changed = snapshot.model_copy(
        update={"contracts": (changed_first, *snapshot.contracts[1:])}
    )
    assert chain_fingerprint(changed) != chain_fingerprint(snapshot)


def test_fingerprint_changes_when_spot_moves():
    snapshot = make_snapshot()
    moved = snapshot.model_copy(update={"spot": snapshot.spot + 0.01})
    assert chain_fingerprint(moved) != chain_fingerprint(snapshot)


def test_fingerprint_distinguishes_unknown_open_interest_from_zero():
    """Invariant 3, applied to hashing. `None` excludes the contract from GEX and `0` includes
    it, so a chain whose OI resolves from unknown to zero has materially changed and must not
    read as a duplicate of itself."""
    snapshot = make_snapshot()
    unknown = snapshot.model_copy(
        update={"contracts": (snapshot.contracts[0].model_copy(update={"open_interest": None}),)}
    )
    zero = snapshot.model_copy(
        update={"contracts": (snapshot.contracts[0].model_copy(update={"open_interest": 0}),)}
    )
    assert chain_fingerprint(unknown) != chain_fingerprint(zero)


def test_token_tags_none_zero_int_and_zero_float_distinctly():
    """The encoding that the test above depends on, checked directly so a regression names the
    cause rather than only its symptom."""
    assert len({_token(None), _token(0), _token(0.0)}) == 3


def test_fingerprint_ignores_vendor_greeks():
    """The engine computes its own greeks from IV (PLAN.md §2), so a vendor greek refresh over
    unchanged quotes is not a reason to store another snapshot."""
    snapshot = make_snapshot()
    regreeked = snapshot.model_copy(
        update={
            "contracts": (
                snapshot.contracts[0].model_copy(update={"gamma": 0.002, "delta": 0.55}),
                *snapshot.contracts[1:],
            )
        }
    )
    assert chain_fingerprint(regreeked) == chain_fingerprint(snapshot)


# --- the capture path -----------------------------------------------------------------------


async def test_capture_skips_identical_content_under_a_fresh_timestamp(
    tmp_path, session_factory
):
    """The T18 case. One row, one Parquet file, and the result says *why* it was skipped."""
    first_snapshot = make_snapshot()
    stalled = _with_captured_at(first_snapshot, first_snapshot.captured_at + dt.timedelta(minutes=15))

    first = await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=first_snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    second = await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=stalled),
        session_factory=session_factory,
        data_dir=tmp_path,
    )

    assert first.skipped_duplicate is False
    assert first.duplicate_reason is None
    assert second.skipped_duplicate is True
    assert second.duplicate_reason == "content"
    assert second.snapshot_id == first.snapshot_id

    with session_factory() as session:
        assert len(session.execute(select(Snapshot)).scalars().all()) == 1
    assert len(list(tmp_path.rglob("*.parquet"))) == 1


async def test_capture_stores_a_changed_chain_under_a_fresh_timestamp(tmp_path, session_factory):
    """The counterpart: a real 15-minute move must still be captured."""
    first_snapshot = make_snapshot()
    moved = _with_captured_at(
        first_snapshot.model_copy(update={"spot": first_snapshot.spot + 5.0}),
        first_snapshot.captured_at + dt.timedelta(minutes=15),
    )

    await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=first_snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    second = await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=moved),
        session_factory=session_factory,
        data_dir=tmp_path,
    )

    assert second.skipped_duplicate is False
    with session_factory() as session:
        assert len(session.execute(select(Snapshot)).scalars().all()) == 2


async def test_content_duplicate_promotes_is_eod(tmp_path, session_factory):
    """16:15 intraday poll, then the 16:20 EOD job over a chain that is frozen after the close.

    Skipping without promoting would leave the day with no `is_eod` row at all, which is what
    T29's catch-up and `GET /api/health/capture` key on -- an invisible permanent hole for a day
    whose data is on disk.
    """
    poll = make_snapshot()
    eod = _with_captured_at(poll, poll.captured_at + dt.timedelta(minutes=5))

    intraday = await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=poll),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    promoted = await capture_snapshot(
        "SPX",
        is_eod=True,
        provider=StubProvider(snapshot=eod),
        session_factory=session_factory,
        data_dir=tmp_path,
    )

    assert promoted.skipped_duplicate is True
    assert promoted.duplicate_reason == "content"
    assert promoted.snapshot_id == intraday.snapshot_id

    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert len(rows) == 1
        assert rows[0].is_eod is True


async def test_content_duplicate_never_demotes_is_eod(tmp_path, session_factory):
    """Promotion is monotonic -- a later non-EOD poll over the same frozen chain must not
    clear the flag the EOD capture set."""
    eod = make_snapshot()
    later_poll = _with_captured_at(eod, eod.captured_at + dt.timedelta(minutes=15))

    await capture_snapshot(
        "SPX",
        is_eod=True,
        provider=StubProvider(snapshot=eod),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=later_poll),
        session_factory=session_factory,
        data_dir=tmp_path,
    )

    with session_factory() as session:
        rows = session.execute(select(Snapshot)).scalars().all()
        assert len(rows) == 1
        assert rows[0].is_eod is True


async def test_capture_stores_the_fingerprint_on_the_row(tmp_path, session_factory):
    snapshot = make_snapshot()
    await capture_snapshot(
        "SPX",
        is_eod=True,
        provider=StubProvider(snapshot=snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    with session_factory() as session:
        row = session.execute(select(Snapshot)).scalar_one()
        assert row.content_hash == chain_fingerprint(snapshot)


async def test_content_check_only_compares_against_the_most_recent_row(
    tmp_path, session_factory
):
    """A chain returning to an earlier state is a genuine observation, not a duplicate capture.

    Captures A, then B (different), then A's content again under a third timestamp. Only the
    immediately preceding row is compared, so the third capture is stored rather than
    suppressed -- suppressing it would silently lose a real reading.
    """
    first_snapshot = make_snapshot()
    moved = _with_captured_at(
        first_snapshot.model_copy(update={"spot": first_snapshot.spot + 5.0}),
        first_snapshot.captured_at + dt.timedelta(minutes=15),
    )
    returned = _with_captured_at(
        first_snapshot, first_snapshot.captured_at + dt.timedelta(minutes=30)
    )

    for payload in (first_snapshot, moved, returned):
        await capture_snapshot(
            "SPX",
            is_eod=False,
            provider=StubProvider(snapshot=payload),
            session_factory=session_factory,
            data_dir=tmp_path,
        )

    with session_factory() as session:
        assert len(session.execute(select(Snapshot)).scalars().all()) == 3


async def test_a_row_without_a_fingerprint_never_matches(tmp_path, session_factory):
    """Rows written before T71 have `content_hash = None`. That means *unknown*, not *empty*,
    so the check must skip comparison and let the capture proceed rather than guess."""
    snapshot = make_snapshot()
    await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=snapshot),
        session_factory=session_factory,
        data_dir=tmp_path,
    )
    with session_factory() as session:  # simulate a pre-migration row
        row = session.execute(select(Snapshot)).scalar_one()
        row.content_hash = None
        session.commit()

    stalled = _with_captured_at(snapshot, snapshot.captured_at + dt.timedelta(minutes=15))
    second = await capture_snapshot(
        "SPX",
        is_eod=False,
        provider=StubProvider(snapshot=stalled),
        session_factory=session_factory,
        data_dir=tmp_path,
    )

    assert second.skipped_duplicate is False
    with session_factory() as session:
        assert len(session.execute(select(Snapshot)).scalars().all()) == 2


async def test_fingerprints_are_compared_per_underlying(tmp_path, session_factory):
    """Two symbols whose most recent captures happen to be adjacent must not shadow each
    other -- the lookup is scoped by underlying, not just by recency."""
    spx = make_snapshot(underlying=Underlying.SPX)
    spy = make_snapshot(underlying=Underlying.SPY)

    for symbol, payload in (("SPX", spx), ("SPY", spy), ("SPX", spx)):
        result = await capture_snapshot(
            symbol,
            is_eod=False,
            provider=StubProvider(snapshot=payload),
            session_factory=session_factory,
            data_dir=tmp_path,
        )

    # The third capture is SPX's own exact duplicate, caught on `captured_at`, not shadowed by
    # SPY having been the most recent row overall.
    assert result.skipped_duplicate is True
    assert result.duplicate_reason == "captured_at"
    with session_factory() as session:
        assert len(session.execute(select(Snapshot)).scalars().all()) == 2


def test_duplicate_reason_appears_in_the_structured_log(tmp_path, session_factory, caplog):
    """`_log_result` serialises the dataclass wholesale, so the new field must survive into the
    JSON line operators actually read."""
    from app.jobs.capture import CaptureResult, _log_result

    result = CaptureResult(
        underlying="SPX", ok=True, skipped_duplicate=True, duplicate_reason="content"
    )
    assert "duplicate_reason" in dataclasses.asdict(result)
    with caplog.at_level("INFO", logger="app.jobs.capture"):
        _log_result(result)
    assert '"duplicate_reason": "content"' in caplog.text
