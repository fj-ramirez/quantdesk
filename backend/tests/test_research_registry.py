"""T77: the Postgres-backed trial registry.

`Registry`'s whole contract is that it behaves exactly like the SQLite one it replaced, because
`search.py`, `paper.py` and `report.py` were not allowed to change. These tests hold that
contract at the boundary the callers actually touch: method names, argument shapes, and the
*types* that come back out -- which is where a storage swap does its silent damage.

Offline, on SQLite, via the same `schema_translate_map` arrangement as every other test here
(`app.core.db.get_engine`). That works because the registry's only Postgres-specific piece is
`ON CONFLICT`, and `_insert_ignore` already spells it per dialect -- which is itself worth a
test, since `INSERT OR IGNORE` was the original's SQLite-only syntax.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from sqlalchemy import select

from app.core.db import get_engine, get_sessionmaker
from app.modules.research.models.db import Base, PaperCandidate, Trial
from app.modules.research.registry import Registry, trial_hash

IS_METRICS = {"sharpe": 1.5, "cagr": 0.2, "max_drawdown": -0.1, "n_fills": 40}
OOS_METRICS = {
    "sharpe": 0.9,
    "cagr": 0.11,
    "max_drawdown": -0.15,
    "n_fills": 25,
    "exposure": 0.4,
    "n_bars": 5000,
    "years": 3.2,
}


@pytest.fixture
def registry(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'research.db'}")
    Base.metadata.create_all(engine)
    reg = Registry(session_factory=get_sessionmaker(engine))
    yield reg
    reg.close()
    engine.dispose()


def _record(reg: Registry, *, symbol="BTC/USDT", params=None, h=None, run_date="2026-09-19"):
    params = params or {"lookback": 20, "threshold": 1.5}
    h = h or trial_hash("crypto", "donchian", symbol, "1h", params)
    reg.record(h, run_date, "crypto", "donchian", symbol, "1h", params, IS_METRICS, OOS_METRICS)
    return h


# --- the hash: the one thing that must never change ----------------------------------------


def test_trial_hash_is_unchanged_from_the_sqlite_original():
    """A frozen expectation, not a recomputation.

    134,377 migrated hashes were produced by this function. Changing the separator, the
    truncation or the `sort_keys` would invalidate every one of them -- `seen()` would start
    missing, the search would silently redo years of work, and nothing would go red. This value
    was computed by running the original `edgelab.registry.trial_hash` in the standalone
    repo's own virtualenv and pasting the result: a test that recomputes the expectation
    from the code under test cannot catch this.
    """
    h = trial_hash("crypto", "donchian", "BTC/USDT", "1h", {"lookback": 20, "threshold": 1.5})
    assert h == "4974b321697a13288a21fc65"
    assert len(h) == 24


def test_trial_hash_ignores_dict_ordering():
    """`sort_keys=True` -- the same params in a different order are the same trial."""
    a = trial_hash("crypto", "donchian", "BTC/USDT", "1h", {"lookback": 20, "threshold": 1.5})
    b = trial_hash("crypto", "donchian", "BTC/USDT", "1h", {"threshold": 1.5, "lookback": 20})
    assert a == b


# --- seen(): the hot path ------------------------------------------------------------------


def test_seen_is_false_before_and_true_after_recording(registry):
    h = trial_hash("crypto", "donchian", "BTC/USDT", "1h", {"lookback": 20})
    assert registry.seen(h) is False
    _record(registry, params={"lookback": 20}, h=h)
    assert registry.seen(h) is True


def test_seen_is_answered_from_memory_after_one_query(registry):
    """The port's performance contract.

    `search.py` calls `seen()` before every trial. Against SQLite that was free; against
    Postgres over Tailscale a per-trial round trip would dominate a cycle that is otherwise
    pure CPU. The hash set is loaded once and maintained by `record`. If this ever regresses to
    a query per call, a cycle gets slower by orders of magnitude and nothing fails.
    """
    _record(registry, symbol="ETH/USDT")
    registry.seen("whatever")  # forces the one load
    loaded = registry._seen
    assert loaded is not None

    for _ in range(100):
        registry.seen("not-present")
    # Same object, never reloaded.
    assert registry._seen is loaded


def test_recording_keeps_the_in_memory_set_current(registry):
    """A trial recorded during a cycle must be `seen()` immediately, without a reload."""
    registry.seen("force-the-load")
    h = _record(registry, symbol="SOL/USDT")
    assert h in registry._seen
    assert registry.seen(h) is True


# --- record(): ON CONFLICT DO NOTHING -------------------------------------------------------


def test_recording_the_same_hash_twice_is_ignored_not_an_error(registry):
    """`INSERT OR IGNORE` became `ON CONFLICT (hash) DO NOTHING`.

    This is what makes two writers against one database harmless -- the Windows scheduled task
    and the worker can both run, and the worst case is duplicated CPU rather than an integrity
    error that kills a cycle.
    """
    h = _record(registry)
    _record(registry, h=h)
    registry.commit()

    assert registry.total_trials() == 1


def test_record_accepts_an_iso_string_run_date(registry):
    """`search.py` passes `date.today().isoformat()` and was not allowed to change."""
    _record(registry, run_date="2026-09-19")
    registry.commit()
    assert registry.trials_on("2026-09-19") == 1
    assert registry.trials_on(dt.date(2026, 9, 19)) == 1
    assert registry.trials_on("2026-09-18") == 0


# --- the shapes the callers depend on -------------------------------------------------------


def test_leaderboard_returns_params_as_a_json_string(registry):
    """`report.py` does `html.escape(r['params'])` and `paper.py` does `json.loads(...)`.

    `JSONB` gives back a dict, so the registry re-serialises it. Getting this wrong would
    `TypeError` deep inside the report rather than here. The re-serialisation must also use
    `sort_keys=True`, because `paper.py` re-hashes the row and must reproduce `trial_hash`.
    """
    params = {"threshold": 1.5, "lookback": 20}
    h = _record(registry, params=params)
    registry.commit()

    rows = registry.leaderboard(min_trades_oos=1, min_exposure=0.0, top_n=10)
    assert len(rows) == 1
    assert isinstance(rows[0]["params"], str)
    assert json.loads(rows[0]["params"]) == params
    # The round trip a promotion depends on.
    assert (
        trial_hash(
            rows[0]["market"],
            rows[0]["strategy"],
            rows[0]["symbol"],
            rows[0]["timeframe"],
            json.loads(rows[0]["params"]),
        )
        == h
    )


def test_leaderboard_returns_run_date_as_an_iso_string(registry):
    """`report.py` interpolates it straight into a table cell."""
    _record(registry, run_date="2026-09-19")
    registry.commit()
    rows = registry.leaderboard(min_trades_oos=1, min_exposure=0.0, top_n=10)
    assert rows[0]["run_date"] == "2026-09-19"


def test_leaderboard_applies_the_same_filters_and_ordering(registry):
    """Filters and `ORDER BY oos_sharpe DESC`, unchanged from the SQL the original ran."""
    for i, (sharpe, fills, exposure) in enumerate(
        [(2.0, 50, 0.5), (3.0, 50, 0.5), (1.0, 5, 0.5), (4.0, 50, 0.001)]
    ):
        reg_oos = OOS_METRICS | {"sharpe": sharpe, "n_fills": fills, "exposure": exposure}
        registry.record(
            f"hash{i}",
            "2026-09-19",
            "crypto",
            "donchian",
            f"SYM{i}",
            "1h",
            {"lookback": i},
            IS_METRICS,
            reg_oos,
        )
    registry.commit()

    rows = registry.leaderboard(min_trades_oos=20, min_exposure=0.02, top_n=10)
    # The 5-fill row and the 0.001-exposure row are filtered out; the rest rank by Sharpe.
    assert [r["oos_sharpe"] for r in rows] == [3.0, 2.0]


def test_family_rows_returns_plain_tuples(registry):
    """`report.py` unpacks these positionally: `for market, strategy, symbol, tf, oos in ...`."""
    _record(registry)
    registry.commit()
    rows = registry.family_rows()
    assert len(rows) == 1
    market, strategy, symbol, timeframe, oos = rows[0]
    assert (market, strategy, symbol, timeframe) == ("crypto", "donchian", "BTC/USDT", "1h")
    assert oos == OOS_METRICS["sharpe"]


# --- paper candidates -----------------------------------------------------------------------


def test_paper_add_accepts_the_json_string_paper_py_passes(registry):
    """`paper.py` calls `paper_add(..., r["params"], ...)` with the leaderboard's JSON string."""
    registry.paper_add(
        "cand1",
        "2026-09-19T21:00:00+00:00",
        "crypto",
        "donchian",
        "BTC/USDT",
        "1h",
        json.dumps({"lookback": 20}, sort_keys=True),
        1.4,
        0.8,
        0.5,
        5,
        6,
        0.7,
        0.3,
    )
    registry.commit()

    assert registry.paper_count() == 1
    assert registry.paper_has("cand1") is True


def test_paper_all_returns_promoted_at_as_an_iso_string(registry):
    """`paper.py` slices it (`c["promoted_at"][:10]`) and feeds it to `pd.Timestamp`.

    A `datetime` here would make the slice return garbage rather than raise, which is the worst
    kind of breakage -- `forward_stats` would silently mis-date every candidate.
    """
    registry.paper_add(
        "cand1",
        "2026-09-19T21:00:00+00:00",
        "crypto",
        "donchian",
        "BTC/USDT",
        "1h",
        {"lookback": 20},
        1.4,
        0.8,
        0.5,
        5,
        6,
        0.7,
        0.3,
    )
    registry.commit()

    row = registry.paper_all()[0]
    assert isinstance(row["promoted_at"], str)
    assert row["promoted_at"][:10] == "2026-09-19"
    assert isinstance(row["params"], str)


def test_promoted_at_is_stored_as_tz_aware_utc(registry, tmp_path):
    """Invariant 4 applies to every module, including a naive value arriving at the boundary."""
    registry.paper_add(
        "cand1",
        dt.datetime(2026, 9, 19, 21, 0),  # naive  # noqa: DTZ001 - the point of the test
        "crypto",
        "donchian",
        "BTC/USDT",
        "1h",
        {"lookback": 20},
        1.4,
        0.8,
        0.5,
        5,
        6,
        0.7,
        0.3,
    )
    registry.commit()

    stored = registry._session.scalar(select(PaperCandidate.promoted_at))
    assert stored.tzinfo is not None
    assert stored == dt.datetime(2026, 9, 19, 21, 0, tzinfo=dt.UTC)


def test_the_registry_has_no_sqlite_path_argument():
    """The rule that keeps one registry from becoming two.

    The SQLite constructor took `path=`. Leaving anything like it would be an open invitation to
    the fork this class exists to prevent: a laptop that could not reach Postgres quietly
    starting a fresh local history, with nothing going red. `Registry` takes a session factory
    or an engine, and there is no file-shaped way in.
    """
    import inspect

    params = set(inspect.signature(Registry.__init__).parameters)
    assert params == {"self", "session_factory", "engine"}


def test_tables_live_in_the_research_schema():
    """Invariant 8, for this module."""
    assert Base.metadata.schema == "research"
    assert {t.schema for t in Base.metadata.tables.values()} == {"research"}
    assert Trial.__table__.schema == "research"
