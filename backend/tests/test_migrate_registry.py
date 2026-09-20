"""T77: the one-shot SQLite -> Postgres registry import.

The real import ran once, against the real 134,377-row `registry.db`, and verified itself (see
the plan file's Result section). What these tests hold is the *verification logic itself* and
the parsing rules around it -- because a verifier that cannot fail is worth nothing, and the
one-shot script will not be run again on a database anyone is watching.

Built on a synthetic SQLite registry with the original's exact DDL, so the shapes under test
are the shapes the real file had.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.research.models.db import Base, PaperCandidate, Trial
from app.modules.research.registry import trial_hash
from scripts import migrate_registry

#: The original's DDL, verbatim from `edgelab/registry.py` -- `TEXT` dates, `REAL` floats and
#: `params` as a JSON string. Spelled out rather than built from the new models on purpose:
#: this is the schema the migration actually meets, and the new models are what it must *not*
#: be assumed to match.
_SQLITE_DDL = """
CREATE TABLE trials (
    hash TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    params TEXT NOT NULL,
    is_sharpe REAL, is_cagr REAL, is_max_dd REAL, is_fills INTEGER,
    oos_sharpe REAL, oos_cagr REAL, oos_max_dd REAL, oos_fills INTEGER,
    oos_exposure REAL, oos_bars INTEGER, oos_years REAL
);
CREATE TABLE paper_candidates (
    hash TEXT PRIMARY KEY,
    promoted_at TEXT NOT NULL,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    params TEXT NOT NULL,
    promoted_oos_sharpe REAL,
    sharpe_2x REAL,
    neighbor_med REAL,
    wf_pos INTEGER,
    wf_active INTEGER,
    wf_med REAL,
    corr_max REAL
);
"""


def _make_sqlite_registry(path, trials):
    conn = sqlite3.connect(path)
    conn.executescript(_SQLITE_DDL)
    for t in trials:
        conn.execute(
            "INSERT INTO trials VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t["hash"],
                t["run_date"],
                t["market"],
                t["strategy"],
                t["symbol"],
                t["timeframe"],
                t["params"],
                1.5, 0.2, -0.1, 40,
                t.get("oos_sharpe", 0.9), 0.11, -0.15, 25, 0.4, 5000, 3.2,
            ),
        )
    conn.commit()
    conn.close()


def _trial(symbol="BTC/USDT", params=None, run_date="2026-09-19", **kw):
    params = params or {"lookback": 20, "threshold": 1.5}
    return {
        "hash": trial_hash("crypto", "donchian", symbol, "1h", params),
        "run_date": run_date,
        "market": "crypto",
        "strategy": "donchian",
        "symbol": symbol,
        "timeframe": "1h",
        "params": json.dumps(params, sort_keys=True),
        **kw,
    }


@pytest.fixture
def postgres_side(tmp_path, monkeypatch):
    """Stand in for the destination database with a SQLite one carrying the new models."""
    engine = get_engine(f"sqlite:///{tmp_path / 'dest.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    monkeypatch.setattr(migrate_registry, "get_engine", lambda: engine)
    monkeypatch.setattr(migrate_registry, "get_sessionmaker", lambda _e: factory)
    yield factory
    engine.dispose()


def test_a_clean_import_moves_every_row_and_verifies(tmp_path, postgres_side):
    src = tmp_path / "registry.db"
    _make_sqlite_registry(src, [_trial(symbol=f"SYM{i}") for i in range(5)])

    assert migrate_registry.main(["--sqlite", str(src), "--seed", "1"]) == 0

    with postgres_side() as session:
        assert session.query(Trial).count() == 5
        row = session.get(Trial, trial_hash("crypto", "donchian", "SYM0", "1h",
                                            {"lookback": 20, "threshold": 1.5}))
        # `params` arrives as a real object, not a string -- that is the JSONB change.
        assert row.params == {"lookback": 20, "threshold": 1.5}
        assert row.run_date == dt.date(2026, 9, 19)


def test_rerunning_the_import_adds_nothing(tmp_path, postgres_side):
    """Idempotent: `ON CONFLICT (hash) DO NOTHING`, so an interrupted run is simply repeated."""
    src = tmp_path / "registry.db"
    _make_sqlite_registry(src, [_trial(symbol=f"SYM{i}") for i in range(5)])

    migrate_registry.main(["--sqlite", str(src), "--seed", "1"])
    migrate_registry.main(["--sqlite", str(src), "--seed", "1"])

    with postgres_side() as session:
        assert session.query(Trial).count() == 5


def test_dry_run_writes_nothing(tmp_path, postgres_side):
    src = tmp_path / "registry.db"
    _make_sqlite_registry(src, [_trial()])

    assert migrate_registry.main(["--sqlite", str(src), "--dry-run", "--seed", "1"]) == 0

    with postgres_side() as session:
        assert session.query(Trial).count() == 0


def test_the_source_database_is_never_modified(tmp_path, postgres_side):
    """`registry.db` stays as it was -- that is the only rollback anyone needs."""
    src = tmp_path / "registry.db"
    _make_sqlite_registry(src, [_trial()])
    before = src.read_bytes()

    migrate_registry.main(["--sqlite", str(src), "--seed", "1"])

    assert src.read_bytes() == before


def test_verification_catches_a_params_field_that_was_mangled(tmp_path, postgres_side):
    """The check that matters most, proved to actually fail.

    A row whose stored `hash` does not match what `trial_hash` recomputes from its *migrated*
    params is exactly the silent disaster the whole verification exists for: counts would match,
    nothing would error, and the registry's never-repeat-work guarantee would be dead. Here the
    source is seeded with a hash that does not correspond to its params, which is what a mangled
    migration would look like from the far side.
    """
    src = tmp_path / "registry.db"
    bad = _trial()
    bad["hash"] = "0" * 24  # not the hash of these params
    _make_sqlite_registry(src, [bad])

    with pytest.raises(SystemExit, match="verification failed"):
        migrate_registry.main(["--sqlite", str(src), "--seed", "1"])


def test_an_unparseable_run_date_fails_loudly_and_writes_nothing(tmp_path, postgres_side):
    """Never coerce to NULL.

    `run_date` is `TEXT` in SQLite and holds whatever the writer of the day used. A null here
    would silently corrupt `trials_on()` and the report's "trials today" health line, and would
    be invisible until someone wondered why the number looked wrong.
    """
    src = tmp_path / "registry.db"
    _make_sqlite_registry(src, [_trial(run_date="not a date")])

    with pytest.raises(ValueError, match="cannot parse run_date"):
        migrate_registry.main(["--sqlite", str(src), "--seed", "1"])

    with postgres_side() as session:
        assert session.query(Trial).count() == 0


def test_parsing_happens_before_any_write(tmp_path, postgres_side):
    """One bad row in the middle must leave the destination untouched, not two-thirds full."""
    src = tmp_path / "registry.db"
    rows = [_trial(symbol=f"SYM{i}") for i in range(5)]
    rows[3]["run_date"] = "garbage"
    _make_sqlite_registry(src, rows)

    with pytest.raises(ValueError):
        migrate_registry.main(["--sqlite", str(src), "--seed", "1"])

    with postgres_side() as session:
        assert session.query(Trial).count() == 0


def test_promoted_at_becomes_tz_aware_utc(tmp_path, postgres_side):
    """Invariant 4: no module gets to keep a string timestamp."""
    src = tmp_path / "registry.db"
    _make_sqlite_registry(src, [_trial()])
    conn = sqlite3.connect(src)
    conn.execute(
        "INSERT INTO paper_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("cand1", "2026-09-19T21:00:00+00:00", "crypto", "donchian", "BTC/USDT", "1h",
         json.dumps({"lookback": 20}), 1.4, 0.8, 0.5, 5, 6, 0.7, 0.3),
    )
    conn.commit()
    conn.close()

    migrate_registry.main(["--sqlite", str(src), "--seed", "1"])

    with postgres_side() as session:
        cand = session.get(PaperCandidate, "cand1")
        assert cand.promoted_at == dt.datetime(2026, 9, 19, 21, 0, tzinfo=dt.UTC)
        assert cand.promoted_at.tzinfo is not None


def test_a_missing_source_file_is_a_clean_error(tmp_path, postgres_side):
    with pytest.raises(SystemExit, match="no such registry"):
        migrate_registry.main(["--sqlite", str(tmp_path / "nope.db")])
