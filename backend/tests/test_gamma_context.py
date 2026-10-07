"""Tests for `app/modules/gex/storage/gamma_context.py` (T138). Offline SQLite."""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.db import Base, GexLevel, Snapshot
from app.modules.gex.storage.gamma_context import GammaContext, gamma_context, monthly_opex

UTC = dt.UTC


@pytest.fixture
def factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'g.db'}")
    Base.metadata.create_all(engine)
    yield get_sessionmaker(engine)
    engine.dispose()


def _capture(factory, underlying, captured_at, session, net, spot=250.0, flip=240.0):
    with factory() as s:
        snap = Snapshot(
            underlying=underlying, captured_at=captured_at, source="test", spot=spot, contract_count=100,
            parquet_path="x.parquet", is_eod=True, session_date=session,
        )
        s.add(snap)
        s.flush()
        s.add(GexLevel(snapshot_id=snap.id, filter="ALL", net_gex=net, spot=spot, flip_point=flip, computed_at=captured_at))
        s.commit()


def test_monthly_opex_is_the_third_friday():
    assert monthly_opex(dt.date(2026, 10, 7)) == dt.date(2026, 10, 16)
    assert monthly_opex(dt.date(2026, 9, 1)) == dt.date(2026, 9, 18)


def test_reads_the_latest_capture_at_or_before_the_bar_never_after(factory):
    _capture(factory, "IWM", dt.datetime(2026, 10, 5, 20, 20, tzinfo=UTC), dt.date(2026, 10, 5), -3e8)
    _capture(factory, "IWM", dt.datetime(2026, 10, 6, 20, 20, tzinfo=UTC), dt.date(2026, 10, 6), 5e8)
    before = gamma_context("IWM", dt.datetime(2026, 10, 6, 15, tzinfo=UTC), session_factory=factory)
    assert before.session_date == dt.date(2026, 10, 5) and before.net_gex == -3e8
    after = gamma_context("IWM", dt.datetime(2026, 10, 7, 11, tzinfo=UTC), session_factory=factory)
    assert after.known and after.net_gex == 5e8
    assert after.describe() == "dealer gamma (IWM, 10-06 capture): LONG (dampens moves), spot 250 above flip 240"


def test_a_passed_monthly_opex_voids_the_read(factory):
    # captured on opex Friday 2026-09-18; the next Monday the expired book is gone
    _capture(factory, "SPY", dt.datetime(2026, 9, 18, 20, 20, tzinfo=UTC), dt.date(2026, 9, 18), 2e9)
    same_day = gamma_context("SPY", dt.datetime(2026, 9, 18, 20, 30, tzinfo=UTC), session_factory=factory)
    assert same_day.known
    monday = gamma_context("SPY", dt.datetime(2026, 9, 21, 14, tzinfo=UTC), session_factory=factory)
    assert not monday.known and monday.net_gex is None
    assert monday.note == "monthly opex 2026-09-18 passed since the 2026-09-18 capture"


def test_missing_and_old_captures_are_unknown_not_zero(factory):
    none = gamma_context("QQQ", dt.datetime(2026, 10, 7, tzinfo=UTC), session_factory=factory)
    assert none == GammaContext("QQQ", note="no capture before this bar")
    assert none.describe() == "dealer gamma (QQQ): unknown, no capture before this bar"
    _capture(factory, "DIA", dt.datetime(2026, 9, 1, 20, 20, tzinfo=UTC), dt.date(2026, 9, 1), 1e8)
    old = gamma_context("DIA", dt.datetime(2026, 9, 9, tzinfo=UTC), session_factory=factory)
    assert not old.known and "too old" in old.note
