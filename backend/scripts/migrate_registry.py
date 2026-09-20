"""One-shot import of EdgeLab's SQLite registry into the `research` schema (T77).

    uv run python -m scripts.migrate_registry --sqlite ../research/results/registry.db
    uv run python -m scripts.migrate_registry --sqlite <path> --dry-run

Run with `-m` from `backend/`, not as a file path: this imports `app.*`, and only the module
form puts the backend root on `sys.path` (the project sets `package = false`, so nothing is
installed into the environment).

**Deliberately not an Alembic revision.** It is a one-time import from a file that exists on
exactly one laptop; it takes minutes; and it needs to be re-runnable and independently
verifiable. A migration that did nothing at all on every other host, and could not be checked
without being run again, would be the worse design.

**Idempotent.** Every insert is `ON CONFLICT (hash) DO NOTHING`, so a re-run adds nothing and a
run interrupted halfway can simply be repeated. The source database is opened read-only and is
never written to: `registry.db` stays exactly as it was, which is the only rollback anyone
needs.

What it verifies, because "the migration ran" is not the same claim as "the registry survived":

1. **Row counts** match on both tables, before and after.
2. **Hash fidelity** -- a random sample of migrated trials is re-hashed with the ported
   `trial_hash` from their migrated `market`, `strategy`, `symbol`, `timeframe` and `params`,
   and must reproduce the stored hash exactly. This is the check that matters most. The entire
   value of 134,377 recorded trials is that the search never repeats work, and that guarantee
   is precisely the claim that a stored hash equals what the running code computes today. If
   `params` had been mangled in transit -- reordered keys, ints turned to floats, a JSON string
   double-encoded -- counts would still match and the registry would be quietly worthless.
3. **Float exactness** -- a sample of Sharpes must compare equal, not almost-equal. SQLite's
   `REAL` is already a 64-bit float, so anything other than exact equality means something
   coerced on the way in.

Anything it cannot parse is a hard failure, never a `NULL`. `run_date` is `TEXT` in SQLite and
holds whatever format the writer used; coercing an unparseable one to null would silently
corrupt `trials_on()` and the "trials today" health line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import random
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.core.db import get_engine, get_sessionmaker
from app.modules.research.models.db import PaperCandidate, Trial
from app.modules.research.registry import _insert_ignore, trial_hash

log = logging.getLogger("migrate_registry")

#: How many migrated trials to re-hash. 100 is the plan's number; it is a sample, not a proof,
#: but a systematic mangling of `params` would have to be astronomically unlucky to miss 100
#: rows drawn at random.
HASH_SAMPLE = 100

#: Rows per `executemany`. Large enough that 134k rows is ~135 round trips, small enough that
#: one statement's parameter list stays well inside Postgres's 65535-parameter limit
#: (18 columns x 1000 = 18,000).
BATCH = 1000


def _parse_date(value: str | None, *, column: str, row_hash: str) -> dt.date:
    """`TEXT` -> `date`, or a loud failure naming the row."""
    if not value:
        raise ValueError(f"{column} is empty for trial {row_hash!r}")
    try:
        # `fromisoformat` handles both "2026-09-19" and a full timestamp, which is what makes
        # this tolerant of however the writer of the day spelled it -- without being tolerant
        # of nonsense.
        return dt.date.fromisoformat(value[:10])
    except ValueError as exc:
        raise ValueError(f"cannot parse {column}={value!r} for trial {row_hash!r}") from exc


def _parse_timestamp(value: str | None, *, column: str, row_hash: str) -> dt.datetime:
    """`TEXT` -> tz-aware UTC datetime. Invariant 4 applies to every module."""
    if not value:
        raise ValueError(f"{column} is empty for candidate {row_hash!r}")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"cannot parse {column}={value!r} for candidate {row_hash!r}") from exc
    if parsed.tzinfo is None:
        # EdgeLab wrote `datetime.now(timezone.utc).isoformat()`, so this branch should never
        # fire. If it does, UTC is the only defensible reading -- and it is recorded here
        # rather than assumed silently.
        log.warning("%s for %r had no timezone; reading it as UTC", column, row_hash)
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _parse_params(value: str, *, row_hash: str) -> dict:
    """The JSON string SQLite stored -> the dict `JSONB` will hold.

    Must round-trip exactly: `trial_hash` is computed over `json.dumps(params, sort_keys=True)`,
    so a value that does not re-serialise identically would break the hash check downstream --
    which is the point of running it.
    """
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"params is not JSON for {row_hash!r}: {value!r}") from exc
    if not isinstance(parsed, dict):
        raise TypeError(f"params is not an object for {row_hash!r}: {value!r}")
    return parsed


def _read_sqlite(path: Path) -> tuple[list[dict], list[dict]]:
    """Read both tables. Opened read-only -- `registry.db` is never modified."""
    if not path.exists():
        raise SystemExit(f"no such registry: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        trials = [dict(r) for r in conn.execute("SELECT * FROM trials")]
        paper = [dict(r) for r in conn.execute("SELECT * FROM paper_candidates")]
    finally:
        conn.close()
    return trials, paper


def _trial_values(row: dict) -> dict:
    h = row["hash"]
    return {
        "hash": h,
        "run_date": _parse_date(row["run_date"], column="run_date", row_hash=h),
        "market": row["market"],
        "strategy": row["strategy"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "params": _parse_params(row["params"], row_hash=h),
        "is_sharpe": row["is_sharpe"],
        "is_cagr": row["is_cagr"],
        "is_max_dd": row["is_max_dd"],
        "is_fills": row["is_fills"],
        "oos_sharpe": row["oos_sharpe"],
        "oos_cagr": row["oos_cagr"],
        "oos_max_dd": row["oos_max_dd"],
        "oos_fills": row["oos_fills"],
        "oos_exposure": row["oos_exposure"],
        "oos_bars": row["oos_bars"],
        "oos_years": row["oos_years"],
    }


def _paper_values(row: dict) -> dict:
    h = row["hash"]
    return {
        "hash": h,
        "promoted_at": _parse_timestamp(row["promoted_at"], column="promoted_at", row_hash=h),
        "market": row["market"],
        "strategy": row["strategy"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "params": _parse_params(row["params"], row_hash=h),
        "promoted_oos_sharpe": row["promoted_oos_sharpe"],
        "sharpe_2x": row["sharpe_2x"],
        "neighbor_med": row["neighbor_med"],
        "wf_pos": row["wf_pos"],
        "wf_active": row["wf_active"],
        "wf_med": row["wf_med"],
        "corr_max": row["corr_max"],
    }


def _verify(session, source_trials: list[dict], source_paper: list[dict]) -> None:
    """The three checks. Raises `SystemExit` on any failure -- this is not advisory."""
    problems: list[str] = []

    dest_trials = session.scalar(select(func.count()).select_from(Trial)) or 0
    dest_paper = session.scalar(select(func.count()).select_from(PaperCandidate)) or 0
    log.info("counts: trials %d -> %d, paper_candidates %d -> %d",
             len(source_trials), dest_trials, len(source_paper), dest_paper)
    if dest_trials != len(source_trials):
        problems.append(f"trials: {len(source_trials)} in SQLite, {dest_trials} in Postgres")
    if dest_paper != len(source_paper):
        problems.append(
            f"paper_candidates: {len(source_paper)} in SQLite, {dest_paper} in Postgres"
        )

    # Hash fidelity, on the *migrated* rows -- re-hashing the source would only prove the
    # source is self-consistent, which was never in doubt.
    sample = random.sample(source_trials, min(HASH_SAMPLE, len(source_trials)))
    checked = mismatched = 0
    for src in sample:
        stored = session.get(Trial, src["hash"])
        if stored is None:
            problems.append(f"trial {src['hash']!r} is missing from Postgres")
            continue
        recomputed = trial_hash(
            stored.market, stored.strategy, stored.symbol, stored.timeframe, stored.params
        )
        checked += 1
        if recomputed != stored.hash:
            mismatched += 1
            problems.append(
                f"hash mismatch: stored {stored.hash!r}, recomputed {recomputed!r} "
                f"from params {stored.params!r}"
            )
        # Exact float equality, not approximate: SQLite REAL is already a 64-bit float, so any
        # difference at all means a type coerced somewhere in transit.
        if src["oos_sharpe"] is not None and stored.oos_sharpe != src["oos_sharpe"]:
            problems.append(
                f"oos_sharpe changed for {stored.hash!r}: "
                f"{src['oos_sharpe']!r} -> {stored.oos_sharpe!r}"
            )
    log.info("re-hashed %d sampled trials, %d mismatched", checked, mismatched)

    if problems:
        for p in problems[:20]:
            log.error("VERIFY: %s", p)
        raise SystemExit(f"verification failed with {len(problems)} problem(s); see above")
    log.info("verification passed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sqlite",
        type=Path,
        default=Path("../../research/results/registry.db"),
        help="path to EdgeLab's registry.db (read-only; never modified)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="read and parse everything, report what would be written, then roll back",
    )
    ap.add_argument("--seed", type=int, default=None, help="fix the verification sample")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.seed is not None:
        random.seed(args.seed)

    source = args.sqlite.resolve()
    log.info("reading %s", source)
    source_trials, source_paper = _read_sqlite(source)
    log.info("read %d trials, %d paper candidates", len(source_trials), len(source_paper))

    # Parse everything up front, before writing anything. A file with one unparseable
    # `run_date` in the middle should fail having written nothing, not two thirds of the rows.
    trial_rows = [_trial_values(r) for r in source_trials]
    paper_rows = [_paper_values(r) for r in source_paper]
    log.info("parsed cleanly")

    session = get_sessionmaker(get_engine())()
    dialect = session.get_bind().dialect.name
    try:
        for i in range(0, len(trial_rows), BATCH):
            session.execute(_insert_ignore(Trial, dialect), trial_rows[i : i + BATCH])
            log.info("trials: %d/%d", min(i + BATCH, len(trial_rows)), len(trial_rows))
        if paper_rows:
            session.execute(_insert_ignore(PaperCandidate, dialect), paper_rows)

        session.flush()
        _verify(session, source_trials, source_paper)

        if args.dry_run:
            session.rollback()
            log.info("--dry-run: rolled back, nothing was written")
        else:
            session.commit()
            log.info("committed")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
