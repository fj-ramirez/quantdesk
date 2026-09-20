"""One-shot import of xactx's DuckDB store into the `terminal` schema (T79).

    uv run python -m scripts.migrate_xactx --duckdb ../market-terminal/data/xactx.duckdb
    uv run python -m scripts.migrate_xactx --duckdb <path> --dry-run

Run with `-m` from `backend/`: this imports `app.*`, and only the module form puts the backend
root on `sys.path`.

**Deliberately not an Alembic revision**, for the same reasons as the research registry import:
one-shot, minutes long, from a file that exists on one laptop, and it must be independently
re-runnable and verifiable. Idempotent via `ON CONFLICT DO NOTHING`; the DuckDB file is opened
read-only and never written.

What it verifies, because "218,915 rows arrived" is a much weaker claim than "the point-in-time
store survived":

1. **Row counts** per table, both sides.
2. **No vintage was lost.** For every `(series_id, value_date)`, the number of distinct `as_of`
   values must be identical before and after. This is the check that matters: a migration that
   deduplicated on `(series_id, value_date)` would look like a success, would halve the table,
   and would silently destroy the only thing this module has that a price feed does not -- the
   ability to say what was knowable at a past moment. Row counts alone would not catch a
   subtler variant of it; this does.
3. **Values are bit-identical.** Compared with `==`, never `isclose`. The plan's warning is
   precise: migrating through CSV or `str()` loses low bits and shifts a z-score in the fourth
   decimal, which is enough to flip a board colour and impossible to spot afterwards. Values
   move as Python floats through the binary protocol, and this asserts that they did.
4. **Timestamps compare as instants.** DuckDB hands `as_of` back tagged with a pytz zone
   (observed: `America/Santo_Domingo`, i.e. UTC-4) rather than UTC. That is a labelling
   difference, not a value one -- but it means every comparison here converts to UTC first, and
   a check that compared wall-clock fields would report thousands of false differences.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from collections import Counter
from pathlib import Path

import psycopg

from app.core.schemas import SCHEMA_TERMINAL
from app.modules.terminal.store.db import connect

log = logging.getLogger("migrate_xactx")

#: Ordered so a reader sees the shape of the store. `observations` is the only large one.
TABLES: dict[str, tuple[str, ...]] = {
    "series_metadata": (
        "series_id", "display_name", "source", "source_code", "asset_class", "category",
        "unit", "frequency", "default_transform", "revisable", "vintage_source",
        "snapshot_tz", "snapshot_local_time", "notes",
    ),
    "ingest_batches": (
        "source_batch", "started_at", "finished_at", "adapter", "args", "status", "note",
    ),
    "observations": (
        "series_id", "value_date", "as_of", "value", "as_of_basis", "source_batch",
    ),
    "releases": (
        "release_id", "series_id", "scheduled_at", "consensus", "consensus_as_of",
        "prior", "actual", "actual_as_of",
    ),
    "edge_definitions": (
        "from_series", "to_series", "expected_sign", "typical_lag_days", "chain", "note",
    ),
    "edge_stats": (
        "from_series", "to_series", "as_of", "value_date", "beta", "beta_window",
        "beta_t_stat", "r_squared", "corr", "corr_percentile", "corr_history_n",
        "sign_conflict", "significant", "n_obs", "source_batch",
    ),
}

#: Primary keys, used for the conflict clause. `observations`' three columns are the invariant.
CONFLICT_KEYS: dict[str, tuple[str, ...]] = {
    "series_metadata": ("series_id",),
    "ingest_batches": ("source_batch",),
    "observations": ("series_id", "value_date", "as_of"),
    "releases": ("release_id",),
    "edge_definitions": ("from_series", "to_series"),
    "edge_stats": ("from_series", "to_series", "as_of"),
}

BATCH = 5000


def _as_utc(value):
    """Any tz-aware datetime -> UTC. Leaves everything else alone.

    DuckDB returns `as_of` tagged with a pytz zone rather than UTC; Postgres stores and returns
    UTC. Same instant, different label. Every comparison in this script goes through here so a
    labelling difference is never mistaken for a data difference.
    """
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.UTC)
    return value


def _read_duckdb(path: Path) -> dict[str, list[tuple]]:
    import duckdb

    if not path.exists():
        raise SystemExit(f"no such database: {path}")
    conn = duckdb.connect(str(path), read_only=True)
    try:
        out: dict[str, list[tuple]] = {}
        for table, columns in TABLES.items():
            rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            out[table] = [tuple(_as_utc(v) for v in row) for row in rows]
            log.info("read %s: %d rows", table, len(out[table]))
        return out
    finally:
        conn.close()


def _vintage_shape(rows: list[tuple]) -> Counter:
    """(series_id, value_date) -> how many distinct vintages it holds.

    The structure that must survive the move. Built the same way from both sides so the
    comparison is of the store's shape, not of two different queries' opinions.
    """
    seen: dict[tuple, set] = {}
    for series_id, value_date, as_of, *_ in rows:
        seen.setdefault((series_id, value_date), set()).add(_as_utc(as_of))
    return Counter({k: len(v) for k, v in seen.items()})


def _insert(pg: psycopg.Connection, table: str, columns: tuple[str, ...], rows: list[tuple]):
    if not rows:
        return
    placeholders = ", ".join(["%s"] * len(columns))
    conflict = ", ".join(CONFLICT_KEYS[table])
    sql = (
        f"INSERT INTO {SCHEMA_TERMINAL}.{table} ({', '.join(columns)}) "
        f"VALUES ({placeholders}) ON CONFLICT ({conflict}) DO NOTHING"
    )
    with pg.cursor() as cur:
        for i in range(0, len(rows), BATCH):
            cur.executemany(sql, rows[i : i + BATCH])
            log.info("%s: %d/%d", table, min(i + BATCH, len(rows)), len(rows))


def _verify(pg: psycopg.Connection, source: dict[str, list[tuple]]) -> None:
    problems: list[str] = []

    # 1. Row counts.
    for table in TABLES:
        with pg.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {SCHEMA_TERMINAL}.{table}")
            dest_n = cur.fetchone()[0]
        src_n = len(source[table])
        log.info("counts %-18s %6d -> %6d", table, src_n, dest_n)
        if dest_n != src_n:
            problems.append(f"{table}: {src_n} in DuckDB, {dest_n} in Postgres")

    # 2. No vintage lost -- the check this whole script exists for.
    with pg.cursor() as cur:
        cur.execute(
            f"SELECT series_id, value_date, as_of FROM {SCHEMA_TERMINAL}.observations"
        )
        dest_rows = [(r[0], r[1], r[2], None, None, None) for r in cur.fetchall()]
    src_shape = _vintage_shape(source["observations"])
    dest_shape = _vintage_shape(dest_rows)
    if src_shape != dest_shape:
        missing = {k: v for k, v in src_shape.items() if dest_shape.get(k) != v}
        problems.append(
            f"{len(missing)} (series_id, value_date) pair(s) changed vintage count; "
            f"first: {list(missing.items())[:3]}"
        )
    else:
        multi = sum(1 for v in src_shape.values() if v > 1)
        log.info(
            "vintages: %d (series, date) pairs, %d of them revised, all preserved",
            len(src_shape), multi,
        )

    # 3. Values bit-identical, on every observation (not a sample -- it is only 219k rows and
    #    this is the one chance to check).
    with pg.cursor() as cur:
        cur.execute(
            f"SELECT series_id, value_date, as_of, value FROM {SCHEMA_TERMINAL}.observations"
        )
        dest_values = {(r[0], r[1], _as_utc(r[2])): r[3] for r in cur.fetchall()}
    mismatched = 0
    for series_id, value_date, as_of, value, *_ in source["observations"]:
        key = (series_id, value_date, _as_utc(as_of))
        if key not in dest_values:
            problems.append(f"missing observation {key}")
        elif dest_values[key] != value:  # exact, never isclose
            mismatched += 1
            if mismatched <= 3:
                problems.append(f"value changed for {key}: {value!r} -> {dest_values[key]!r}")
    if mismatched:
        problems.append(f"{mismatched} observation value(s) differ")
    else:
        log.info("values: all %d observations bit-identical", len(source["observations"]))

    if problems:
        for p in problems[:20]:
            log.error("VERIFY: %s", p)
        raise SystemExit(f"verification failed with {len(problems)} problem(s); see above")
    log.info("verification passed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--duckdb",
        type=Path,
        default=Path("../../market-terminal/data/xactx.duckdb"),
        help="path to xactx.duckdb (read-only; never modified)",
    )
    ap.add_argument("--dry-run", action="store_true", help="import, verify, then roll back")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    source = _read_duckdb(args.duckdb.resolve())

    store_conn = connect()
    pg = store_conn.raw
    pg.autocommit = False
    try:
        for table, columns in TABLES.items():
            _insert(pg, table, columns, source[table])
        _verify(pg, source)
        if args.dry_run:
            pg.rollback()
            log.info("--dry-run: rolled back, nothing was written")
        else:
            pg.commit()
            log.info("committed")
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
