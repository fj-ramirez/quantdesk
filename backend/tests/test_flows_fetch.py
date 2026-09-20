"""Tests for `app/modules/gex/flows_fetch.py` (T52 CLI). Offline: `update_flows_job` itself is
monkeypatched, so this only exercises argument parsing and exit-code plumbing -- the job's own
behavior is covered by `test_flows_job.py`. Mirrors `test_bars_backfill.py`'s scope split
(if one exists) / `app.modules.gex.bars_backfill.main`'s own test style otherwise.
"""

from __future__ import annotations

from app.modules.gex import flows_fetch
from app.modules.gex.jobs.flows import FlowsFamilyResult


async def _fake_success(*args, **kwargs):
    return [FlowsFamilyResult(family="spdr", ok=True, inserted=1, skipped=0)]


async def _fake_failure(*args, **kwargs):
    return [FlowsFamilyResult(family="spdr", ok=False, error="upstream down")]


async def _fake_empty(*args, **kwargs):
    return []


def test_main_returns_zero_on_success(monkeypatch, capsys):
    monkeypatch.setattr(flows_fetch, "update_flows_job", _fake_success)
    monkeypatch.setattr(flows_fetch, "get_session_factory", lambda: None)

    exit_code = flows_fetch.main(["--symbols", "XLK"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "spdr" in out
    assert "inserted=1" in out


def test_main_returns_nonzero_on_family_failure(monkeypatch, capsys):
    monkeypatch.setattr(flows_fetch, "update_flows_job", _fake_failure)
    monkeypatch.setattr(flows_fetch, "get_session_factory", lambda: None)

    exit_code = flows_fetch.main(["--symbols", "XLK"])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAILED" in out


def test_main_reports_when_no_family_matches_the_requested_symbols(monkeypatch, capsys):
    monkeypatch.setattr(flows_fetch, "update_flows_job", _fake_empty)
    monkeypatch.setattr(flows_fetch, "get_session_factory", lambda: None)

    exit_code = flows_fetch.main(["--symbols", "NOPEXYZ"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "nothing fetched" in out


def test_main_parses_comma_separated_symbols(monkeypatch):
    seen = {}

    async def fake_update_flows_job(symbols=None, **kwargs):
        seen["symbols"] = symbols
        return []

    monkeypatch.setattr(flows_fetch, "update_flows_job", fake_update_flows_job)
    monkeypatch.setattr(flows_fetch, "get_session_factory", lambda: None)

    flows_fetch.main(["--symbols", "xlk, iwm"])

    assert seen["symbols"] == ["XLK", "IWM"]


def test_main_defaults_to_none_symbols_when_not_given(monkeypatch):
    seen = {}

    async def fake_update_flows_job(symbols=None, **kwargs):
        seen["symbols"] = symbols
        return []

    monkeypatch.setattr(flows_fetch, "update_flows_job", fake_update_flows_job)
    monkeypatch.setattr(flows_fetch, "get_session_factory", lambda: None)

    flows_fetch.main([])

    assert seen["symbols"] is None
