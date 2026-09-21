"""The terminal CLI's contract with its settings facade and its store.

These tests exist because of a bug that ran silently for months. T79 removed `db_path` from
both `Settings` and `Store` -- deliberately, so no second store could be pointed at a stray
file -- but `cli.py`'s eleven call sites were never updated. Every one still passed
`settings.db_path`, so every CLI command raised `AttributeError` on its first line.

That would have been caught in a day if anything ran the CLI. Nothing did: the worker is the
only caller, it runs at 03:00, and `_run_sequence` logs a step's failure rather than raising
so one bad source cannot abandon the night. So the nightly ingest crashed, logged, and the
board kept serving migration-era data that looked entirely plausible.

The lesson is not "add a test for db_path". It is that `config.Settings` is a *facade* --
hand-maintained, with roughly 6,600 lines of ported code reading through it -- and a facade
that drifts from its readers fails at runtime, in a worker, where nobody is looking. So the
first test checks the whole surface statically, not one attribute.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.modules.terminal import cli
from app.modules.terminal.config import Settings, load_settings
from app.modules.terminal.store import Store


def _settings_attributes_read_by(module) -> set[str]:
    """Every `settings.<name>` / `_core.<name>` attribute the module's source reads.

    A static walk rather than an import-time or call-time check: the handlers each need a
    populated `argparse.Namespace` and most open a database, so actually calling them here
    would test the network and Postgres instead of the contract. The attribute names are
    right there in the syntax tree.
    """
    tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
        ):
            found.add(node.attr)
    return found


def test_cli_reads_no_setting_the_facade_does_not_provide() -> None:
    """The regression test proper: every `settings.X` in `cli.py` is a field on `Settings`.

    This is the check that fails on the original bug -- `db_path` is read but not provided --
    and it keeps failing for the next field someone removes from the facade without following
    it through to the readers.
    """
    provided = set(Settings.__dataclass_fields__)
    read = _settings_attributes_read_by(cli)

    missing = read - provided
    assert not missing, (
        f"cli.py reads settings attribute(s) the facade does not provide: {sorted(missing)}. "
        "Either add the field to app/modules/terminal/config.Settings or stop reading it."
    )


def test_every_subcommand_has_a_handler() -> None:
    """The parser and the dispatch table are two hand-maintained lists that must agree.

    A subcommand the parser accepts but `handlers` has no entry for raises `KeyError` inside
    the `try`, which catches only `XactxError` -- so it surfaces as a traceback from a cron
    job at 03:00, the same failure mode as the bug above.
    """
    source = Path(inspect.getfile(cli)).read_text(encoding="utf-8")
    tree = ast.parse(source)

    subcommands: set[str] = set()
    handler_keys: set[str] = set()
    for node in ast.walk(tree):
        # `subparsers.add_parser("name", ...)`
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            subcommands.add(node.args[0].value)
        # the `handlers = {...}` dict literal
        if isinstance(node, ast.Dict) and node.keys:
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            if len(keys) == len(node.keys) and "ingest" in keys:
                handler_keys.update(keys)

    assert subcommands, "no subcommands found -- the AST walk needs updating, not the CLI"
    assert subcommands == handler_keys, (
        f"parser and handlers disagree; "
        f"no handler for {sorted(subcommands - handler_keys)}, "
        f"no subcommand for {sorted(handler_keys - subcommands)}"
    )


def test_store_takes_no_path_argument() -> None:
    """`Store()` opens the one configured database and nothing else (T79).

    Locking in the removal, not just the current signature: a path-shaped argument is how the
    second-store fork gets reintroduced, and the module docstring rejects it for the same
    reason `modules/research`'s registry refuses a SQLite fallback.
    """
    params = inspect.signature(Store.__init__).parameters
    assert set(params) == {"self", "read_only"}, (
        f"Store.__init__ takes {sorted(set(params) - {'self'})}; a path argument here would "
        "let a caller open a database other than the configured one."
    )
    assert params["read_only"].kind is inspect.Parameter.KEYWORD_ONLY


def test_fomc_calendar_path_resolves_under_data_dir() -> None:
    """The one path the module still owns, and it is derived in exactly one place.

    The calendar is a fetched artifact rather than a series, so it has no row to live in. It
    used to be `db_path.parent / "fomc_calendar.json"`; with `db_path` gone it resolves under
    `DATA_DIR`, which is the same directory, so an existing cache is still found.
    """
    from app.core.config import settings as core_settings

    path = load_settings().fomc_calendar_path
    assert path.name == "fomc_calendar.json"
    assert path.parent == Path(core_settings.DATA_DIR)


@pytest.mark.parametrize("command", ["ingest", "derive", "edges"])
def test_worker_sequence_steps_are_real_subcommands(command: str) -> None:
    """The worker's `SEQUENCE` names must be commands the CLI actually dispatches.

    `_run_sequence` calls `cli.main([step])` by design -- so a hand-run debugging session runs
    exactly what the worker runs -- which makes the worker's tuple of step names part of the
    CLI's public surface.
    """
    from app.workers.terminal_ingest import SEQUENCE

    assert command in SEQUENCE


# --- T90: the nightly sequence must not be killable by a usage error ------------------------
#
# The bug these cover is the same shape as the one at the top of this file and was found the
# same way -- by looking at the database rather than at the code. `policy` was step 4 of the
# worker's sequence and takes a required positional settlement file, so `cli.main(["policy"])`
# raised `SystemExit(2)` from argparse. `SystemExit` is a `BaseException`, so `_run_sequence`'s
# `except Exception` did not catch it, and the night ended before `edges` ran. Every scheduled
# run since the step was added produced no transmission-graph refresh at all, while
# `edge_stats` kept serving rows from the last hand-run.


def test_cli_main_returns_a_code_for_a_usage_error_rather_than_raising() -> None:
    """A bad invocation is an exit code, not an exception, because callers are in-process.

    `main` is annotated `-> int` and the worker calls it directly. argparse's default of
    raising `SystemExit` makes that annotation a lie for exactly the inputs a caller is most
    likely to get wrong.
    """
    assert cli.main(["policy"]) == 2  # missing the required `settlements` positional
    assert cli.main(["no-such-command"]) == 2


def test_cli_main_still_reports_success_for_help() -> None:
    """`--help` also raises `SystemExit`, with code 0. It is not an error and must not read
    as one -- a worker that logged a failure for it would be crying wolf."""
    assert cli.main(["--help"]) == 0


def test_policy_is_not_in_the_worker_sequence() -> None:
    """`policy` cannot run unattended: CME's terms prohibit fetching the settlements it needs.

    Asserted rather than commented, because the failure mode of putting it back is silent --
    the sequence simply stops early and the only symptom is a stale `as_of` on a screen.
    """
    from app.workers.terminal_ingest import SEQUENCE, UNSCHEDULED_STEPS

    assert "policy" not in SEQUENCE
    assert "policy" in dict(UNSCHEDULED_STEPS)


def test_sequence_survives_a_step_that_raises_system_exit(monkeypatch, caplog) -> None:
    """Belt and braces for the next required argument someone adds.

    `cli.main` no longer raises `SystemExit`, but a library it calls still might, and the
    property `_run_sequence` documents -- every step is attempted -- has to hold regardless of
    which of the two a step chooses to fail with.
    """
    from app.workers import terminal_ingest

    attempted: list[str] = []

    def fake_main(argv: list[str]) -> int:
        attempted.append(argv[0])
        if argv[0] == "derive":
            raise SystemExit(2)
        return 0

    monkeypatch.setattr(cli, "main", fake_main)
    with caplog.at_level("INFO"):
        terminal_ingest._run_sequence()

    assert attempted == list(terminal_ingest.SEQUENCE), "a SystemExit truncated the sequence"
    assert "edges" in attempted, "the step the original bug skipped must still run"
    assert any("SystemExit" in r.getMessage() for r in caplog.records)


def test_unscheduled_steps_are_announced_once_per_run(monkeypatch, caplog) -> None:
    """The gap is stated, not inferred from an absence. That inference is what nobody made."""
    from app.workers import terminal_ingest

    monkeypatch.setattr(cli, "main", lambda argv: 0)
    with caplog.at_level("WARNING"):
        terminal_ingest._run_sequence()

    warnings = [r for r in caplog.records if "not scheduled" in r.getMessage()]
    assert len(warnings) == len(terminal_ingest.UNSCHEDULED_STEPS)
    assert "CME" in warnings[0].getMessage()
