"""Re-score recorded decisions after a fix to the scorer itself (T115).

The nightly job (`app.modules.gex.jobs.decisions`) scores `pending` rows only, so a resolved
outcome is final the day it is reached -- which is right for the market and wrong for a bug.
When `app.modules.gex.scan.outcomes.evaluate` changes, the rows it already resolved still carry
what the old scorer said. This re-runs the current scorer over every row of one setup and
writes back the rows whose outcome changed.

Only the outcome columns move. Entry, stop, target and the payload are what the engine
committed to on the day, and nothing here touches them.

**Dry run by default.** It prints every row whose `outcome` or `result_r` would change, and
counts the ones where only the excursions or the note would; `--apply` writes them::

    uv run python -m app.modules.gex.jobs.rescore --setup fade
    uv run python -m app.modules.gex.jobs.rescore --setup fade --apply

Read the diff before applying. The bars are re-read too, so a row can also move because its
daily bars were revised since it was scored -- that row is listed like any other, and is worth
a look before it is written.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from app.modules.gex.scan.outcomes import Outcome, evaluate
from app.modules.gex.storage import decisions_repository as repo
from app.modules.gex.storage.bars_repository import read_bars

__all__ = ["Change", "rescore"]

#: Outcome columns compared, as (record attribute, `Outcome` attribute).
_FIELDS = (
    ("outcome", "outcome"),
    ("fill", "fill"),
    ("triggered_on", "triggered_on"),
    ("resolved_on", "resolved_on"),
    ("bars_held", "bars_held"),
    ("result_r", "result_r"),
    ("mark_r", "mark_r"),
    ("mfe_r", "mfe_r"),
    ("mae_r", "mae_r"),
    ("outcome_note", "note"),
)


def _same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9)
    return a == b


@dataclass(frozen=True, slots=True)
class Change:
    record: repo.DecisionRecord
    new: Outcome
    fields: tuple[str, ...]

    @property
    def scored_differently(self) -> bool:
        """The outcome or the R moved -- a change to the track record, not just its detail."""
        return "outcome" in self.fields or "result_r" in self.fields


def rescore(
    setup: str,
    *,
    apply: bool = False,
    session_factory: sessionmaker[Session] | None = None,
    bars_session_factory: sessionmaker[Session] | None = None,
) -> list[Change]:
    """Re-evaluate every `setup` row; return the ones that differ, and write them if `apply`."""
    changes: list[Change] = []
    for record in repo.by_setup(setup, session_factory=session_factory):
        bars = read_bars(
            record.underlying,
            start=record.decided_on + dt.timedelta(days=1),
            session_factory=bars_session_factory,
        )
        new = evaluate(record.spec, bars)
        moved = tuple(
            name for name, attr in _FIELDS if not _same(getattr(record, name), getattr(new, attr))
        )
        # A pending row's mark and note move every session by design; the nightly job owns those.
        if record.outcome == "pending" and new.outcome == "pending":
            continue
        if moved:
            changes.append(Change(record, new, moved))
    if apply:
        for change in changes:
            repo.apply_outcome(change.record.id, change.new, session_factory=session_factory)
    return changes


def _r(value: float | None) -> str:
    return "·" if value is None else f"{value:+.2f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--setup", required=True, help="setup to rescore, e.g. fade")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args(argv)

    changes = rescore(args.setup, apply=args.apply)
    scored = [c for c in changes if c.scored_differently]
    for c in scored:
        rec = c.record
        print(
            f"{rec.id:>5} {rec.underlying:<5} {rec.key:<15} {rec.decided_on}  "
            f"{rec.outcome:>11} {_r(rec.result_r):>6} -> {c.new.outcome:>11} {_r(c.new.result_r):>6}"
            f"  mark {_r(c.new.mark_r)}  | {c.new.note}"
        )
    print(
        f"{len(scored)} rescored, {len(changes) - len(scored)} with only excursions or note changed"
        f" -- {'applied' if args.apply else 'dry run, nothing written'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
