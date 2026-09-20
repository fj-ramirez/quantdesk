"""Tests for T30: `Snapshot.parquet_path` must genuinely mean "relative to DATA_DIR", not
whatever `write_snapshot` happened to return, and every reader must resolve it through the one
shared helper (`app.modules.gex.storage.parquet.resolve_snapshot_path`) rather than reinventing the join.

The centerpiece scenario is the one the task exists to fix: a row written while `DATA_DIR`
pointed at one directory must still resolve after `DATA_DIR` (or the caller's `data_dir`
override) points somewhere else entirely -- simulating a host-vs-container move. A second
group of tests covers the Windows-vs-posix separator hazard directly: `pathlib.Path.as_posix()`
only rewrites the separator its own flavor recognizes, so a Windows-produced backslash path
would previously survive untouched through `Path(...).as_posix()` on a POSIX host.
"""

from __future__ import annotations

import datetime as dt
import shutil

import pytest

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.models.chain import ChainSnapshot, Underlying
from app.modules.gex.models.db import Base, Snapshot
from app.modules.gex.storage.parquet import (
    read_snapshot,
    resolve_snapshot_path,
    to_data_dir_relative_path,
    write_snapshot,
)
from app.modules.gex.storage.repository import SnapshotRepository


def make_snapshot(**kw) -> ChainSnapshot:
    base = {
        "underlying": Underlying.SPX,
        "spot": 7709.52,
        "captured_at": dt.datetime(2026, 9, 4, 18, 5, 33, tzinfo=dt.UTC),
        "source": "cboe",
        "delayed_minutes": 15,
        "contracts": (),
    }
    return ChainSnapshot(**(base | kw))


@pytest.fixture
def session(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    with factory() as s:
        yield s
    engine.dispose()


def test_row_written_under_one_data_dir_resolves_after_data_dir_changes(tmp_path, session):
    """The load-bearing scenario: capture writes under DATA_DIR A (e.g. the host's ./data);
    the whole tree is then relocated to DATA_DIR B (e.g. the container's /data, or the host's
    directory after a move) with no other bookkeeping. Because the stored value is relative to
    DATA_DIR rather than whatever `write_snapshot` returned, re-pointing `data_dir` at B is
    enough for `resolve_snapshot_path` to find the file again -- no re-indexing needed.
    """
    data_dir_a = tmp_path / "host_data"
    data_dir_b = tmp_path / "container_data"  # a distinct root standing in for "DATA_DIR moved"

    snapshot = make_snapshot()
    written_path = write_snapshot(snapshot, data_dir=data_dir_a)
    row = SnapshotRepository(session).add(snapshot, written_path, is_eod=True, data_dir=data_dir_a)

    # The stored value carries no trace of `data_dir_a` -- that's what makes it portable.
    assert not row.parquet_path.startswith(str(data_dir_a).replace("\\", "/"))
    assert row.parquet_path == "chains/SPX/2026/09/20260904T180533000000Z.parquet"

    # Simulate the move: the same tree, copied wholesale under a different root. No file is
    # rewritten and the index row is untouched -- only `data_dir` changes.
    shutil.copytree(data_dir_a, data_dir_b)

    resolved = resolve_snapshot_path(row, data_dir=data_dir_b)
    assert resolved == data_dir_b / "chains" / "SPX" / "2026" / "09" / written_path.name
    assert resolved.exists()
    assert read_snapshot(resolved) == snapshot

    # And resolving against the *original* base still works too -- moving DATA_DIR doesn't
    # retroactively break the old location if it's still there.
    assert resolve_snapshot_path(row, data_dir=data_dir_a).exists()


def test_resolve_snapshot_path_defaults_to_settings_data_dir(tmp_path, session, monkeypatch):
    """No explicit `data_dir` at either end -- both `write_snapshot` and `resolve_snapshot_path`
    must independently fall back to `settings.DATA_DIR`, and still agree."""
    from app.core import config

    monkeypatch.setattr(config.settings, "DATA_DIR", str(tmp_path))
    snapshot = make_snapshot()
    path = write_snapshot(snapshot)  # no data_dir
    row = SnapshotRepository(session).add(snapshot, path, is_eod=False)  # no data_dir either

    resolved = resolve_snapshot_path(row)  # no data_dir either
    assert resolved == path
    assert resolved.exists()


def test_to_data_dir_relative_path_strips_absolute_docker_style_prefix():
    """Docker's `DATA_DIR=/data` makes `write_snapshot` return an absolute path -- verify the
    relativizer strips exactly that prefix, posix-separated."""
    rel = to_data_dir_relative_path(
        "/data/chains/SPX/2026/09/20260904T185217000000Z.parquet", "/data"
    )
    assert rel == "chains/SPX/2026/09/20260904T185217000000Z.parquet"


def test_to_data_dir_relative_path_strips_default_cwd_relative_prefix():
    """The default `DATA_DIR=./data` makes `write_snapshot` return a CWD-relative path -- the
    live-verified case from the T30 brief (`data/chains/SPX/2026/09/....parquet`)."""
    rel = to_data_dir_relative_path(
        "data/chains/SPX/2026/09/20260904T185217000000Z.parquet", "./data"
    )
    assert rel == "chains/SPX/2026/09/20260904T185217000000Z.parquet"


def test_to_data_dir_relative_path_leaves_unrelated_path_unchanged():
    """A path that isn't actually under `data_dir` at all must not be mangled -- it's returned
    posix-ified, not corrupted into a bogus relative path with leading `..` segments."""
    rel = to_data_dir_relative_path("chains/SPX/2026/09/x.parquet", "./data")
    assert rel == "chains/SPX/2026/09/x.parquet"


# --- Windows-vs-posix separator coverage ---------------------------------------------------


def test_windows_backslash_path_normalizes_regardless_of_host_os():
    """`pathlib.Path.as_posix()` only rewrites the separator its own flavor recognizes as one
    -- on a POSIX host, `\\` is just an ordinary character, so a naive `Path(...).as_posix()`
    would let a Windows-produced backslash path survive into storage untouched. This must not
    depend on which OS runs the test.
    """
    rel = to_data_dir_relative_path(r"C:\data\chains\SPX\2026\09\x.parquet", r"C:\data")
    assert rel == "chains/SPX/2026/09/x.parquet"
    assert "\\" not in rel


def test_resolve_snapshot_path_normalizes_a_backslash_stored_value(tmp_path, session):
    """Simulates a row that (pre-T30, or from a hand-built fixture) has backslashes baked into
    `parquet_path` -- `resolve_snapshot_path` must still produce a path `pathlib` can use to
    find the file on *this* host, not silently keep the backslashes as literal characters."""
    snapshot = make_snapshot()
    path = write_snapshot(snapshot, data_dir=tmp_path)
    rel = path.relative_to(tmp_path).as_posix()

    row = Snapshot(
        underlying="SPX",
        captured_at=snapshot.captured_at,
        source="cboe",
        spot=snapshot.spot,
        contract_count=0,
        parquet_path=rel.replace("/", "\\"),  # force backslashes, as if written on Windows
        is_eod=False,
    )
    session.add(row)
    session.commit()

    resolved = resolve_snapshot_path(row, data_dir=tmp_path)
    assert resolved.exists()
    assert read_snapshot(resolved) == snapshot


# --- Legacy-row fallback (the migration decision, see the module docstring in
# app/modules/gex/storage/parquet.py) -------------------------------------------------------------------


def test_resolve_snapshot_path_falls_back_for_a_legacy_absolute_row(tmp_path, session):
    """A row indexed before T30 normalized the column stores the raw, already-`DATA_DIR`-joined
    value `write_snapshot` returned (absolute here, standing in for Docker's `DATA_DIR=/data`).
    As long as `DATA_DIR` hasn't changed since it was written, `resolve_snapshot_path` must
    still find it -- this is the documented migration strategy (normalize-on-read, not a
    destructive rewrite of existing values) and this test is what proves no legacy row is
    silently left resolving to nothing.
    """
    snapshot = make_snapshot()
    absolute_path = write_snapshot(snapshot, data_dir=tmp_path)  # pre-T30 write behavior

    row = Snapshot(
        underlying="SPX",
        captured_at=snapshot.captured_at,
        source="cboe",
        spot=snapshot.spot,
        contract_count=0,
        parquet_path=str(absolute_path).replace("\\", "/"),  # unstripped, exactly as pre-T30
        is_eod=False,
    )
    session.add(row)
    session.commit()

    resolved = resolve_snapshot_path(row, data_dir=tmp_path)
    assert resolved.exists()
    assert read_snapshot(resolved) == snapshot


def test_resolve_snapshot_path_raises_naming_the_expected_path_when_truly_missing(tmp_path, session):
    """Neither candidate exists on disk: the function must not silently return a bogus path
    that then fails somewhere unrelated -- callers get back the `data_dir`-joined candidate, so
    a subsequent `FileNotFoundError` from `read_snapshot` names the path this function actually
    expected, not an arbitrary fallback."""
    row = Snapshot(
        underlying="SPX",
        captured_at=dt.datetime(2026, 9, 4, 18, 5, 33, tzinfo=dt.UTC),
        source="cboe",
        spot=1.0,
        contract_count=0,
        parquet_path="chains/SPX/2026/09/does-not-exist.parquet",
        is_eod=False,
    )
    session.add(row)
    session.commit()

    resolved = resolve_snapshot_path(row, data_dir=tmp_path)
    assert resolved == tmp_path / "chains/SPX/2026/09/does-not-exist.parquet"
    with pytest.raises(FileNotFoundError):
        read_snapshot(resolved)
