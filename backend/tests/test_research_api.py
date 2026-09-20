"""T78: `/api/research/*`.

The tests that matter here are the ones about the **noise ceiling**, because it is the only
thing standing between this page and a leaderboard that lies. With 134,377 trials behind it,
pure luck produces a best OOS Sharpe around 5.6 -- so a row at 3.0 is below the noise floor and
means nothing, and a page that omitted or miscomputed the ceiling would present it as the best
edge found. Several tests below exist purely to make that impossible to break quietly.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_sessionmaker
from app.main import app
from app.modules.research.models.db import Base, PaperCandidate, PaperScore, Trial
from app.modules.research.storage import repository


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite:///{tmp_path / 'api.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    # The routers call the repository with no factory, which reaches for the process-wide one.
    monkeypatch.setattr(repository, "get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


@pytest.fixture
def client(session_factory):
    return TestClient(app)


def _trial(session, h, **kw):
    defaults = {
        "hash": h,
        "run_date": dt.date(2026, 9, 19),
        "market": "crypto",
        "strategy": "donchian_breakout",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "params": {"lookback": 20},
        "is_sharpe": 1.2,
        "is_cagr": 0.3,
        "is_max_dd": -0.1,
        "is_fills": 100,
        "oos_sharpe": 1.0,
        "oos_cagr": 0.2,
        "oos_max_dd": -0.2,
        "oos_fills": 50,
        "oos_exposure": 0.4,
        "oos_bars": 9000,
        "oos_years": 3.0,
    }
    session.add(Trial(**(defaults | kw)))


# --- the noise ceiling -----------------------------------------------------------------------


def test_the_leaderboard_always_ships_the_noise_ceiling(client, session_factory):
    """It is in the same payload as the rows, so nothing can render one without the other."""
    with session_factory() as s:
        _trial(s, "a")
        s.commit()

    body = client.get("/api/research/leaderboard").json()
    assert "noise_ceiling" in body
    assert "total_trials" in body
    assert body["noise_ceiling"] > 0
    assert body["rows"][0]["noise_ceiling"] > 0
    assert "above_ceiling" in body["rows"][0]


def test_a_row_below_its_own_ceiling_is_flagged_as_such(client, session_factory):
    """The case the whole module exists to make visible.

    A Sharpe of 1.0 over a 3-year OOS span, against a registry of a few trials, is not an edge.
    `above_ceiling` must say so -- and it is computed server-side precisely so a frontend
    refactor cannot drop the comparison and leave the number looking impressive.
    """
    with session_factory() as s:
        for i in range(50):
            _trial(s, f"h{i}", oos_sharpe=1.0, oos_years=3.0)
        s.commit()

    row = client.get("/api/research/leaderboard").json()["rows"][0]
    assert row["oos_sharpe"] == 1.0
    assert row["above_ceiling"] is False
    assert row["noise_ceiling"] > 1.0


def test_a_row_clearing_its_ceiling_is_flagged_above(client, session_factory):
    with session_factory() as s:
        _trial(s, "big", oos_sharpe=99.0, oos_years=30.0)
        s.commit()

    row = client.get("/api/research/leaderboard").json()["rows"][0]
    assert row["above_ceiling"] is True


def test_each_row_is_judged_against_its_own_oos_span(client, session_factory):
    """The ceiling scales as 1/sqrt(years): a short span is a much weaker claim.

    Two rows with the same Sharpe and different spans must not get the same ceiling. Using one
    headline ceiling for every row would flatter short-history results, which is exactly where
    overfitting hides.
    """
    with session_factory() as s:
        _trial(s, "short", oos_sharpe=2.0, oos_years=1.0)
        _trial(s, "long", oos_sharpe=2.0, oos_years=16.0)
        s.commit()

    rows = {r["hash"]: r for r in client.get("/api/research/leaderboard").json()["rows"]}
    assert rows["short"]["noise_ceiling"] > rows["long"]["noise_ceiling"]


def test_filtering_does_not_lower_the_ceiling(client, session_factory):
    """The denominator is the whole registry, never the filtered set.

    Narrowing to one market does not mean you ran fewer experiments. If the ceiling fell when a
    filter was applied, anyone could filter their way to a green row -- which is multiple-testing
    bias wearing a UI.
    """
    with session_factory() as s:
        for i in range(40):
            _trial(s, f"c{i}", market="crypto")
        for i in range(40):
            _trial(s, f"s{i}", market="stocks")
        s.commit()

    unfiltered = client.get("/api/research/leaderboard").json()
    filtered = client.get("/api/research/leaderboard?market=crypto").json()

    assert filtered["total"] < unfiltered["total"]
    assert filtered["total_trials"] == unfiltered["total_trials"] == 80
    assert filtered["rows"][0]["noise_ceiling"] == unfiltered["rows"][0]["noise_ceiling"]


def test_losing_trials_stay_in_the_denominator(client, session_factory):
    """They are filtered out of the *rows* and must remain in `total_trials`.

    Every trial ever run is the denominator; that is why losers are never deleted. Counting only
    the rows that survive the filters would lower the ceiling and make every surviving row look
    better than it is.
    """
    with session_factory() as s:
        _trial(s, "winner", oos_sharpe=2.0)
        for i in range(99):
            _trial(s, f"loser{i}", oos_sharpe=-1.0)
        s.commit()

    body = client.get("/api/research/leaderboard").json()
    assert len(body["rows"]) == 1
    assert body["total"] == 1
    assert body["total_trials"] == 100


# --- ranking, filters, pagination --------------------------------------------------------------


def test_rows_are_ranked_by_oos_sharpe_descending(client, session_factory):
    with session_factory() as s:
        for i, sharpe in enumerate([1.0, 3.0, 2.0]):
            _trial(s, f"h{i}", oos_sharpe=sharpe)
        s.commit()

    rows = client.get("/api/research/leaderboard").json()["rows"]
    assert [r["oos_sharpe"] for r in rows] == [3.0, 2.0, 1.0]


def test_default_filters_match_research_yaml(client, session_factory):
    """`min_trades_oos=20`, `min_exposure=0.02` -- the same values the static report uses.

    This is what makes "the page agrees with the HTML" checkable rather than hopeful. If these
    drift, the two disagree and one of them is wrong.
    """
    with session_factory() as s:
        _trial(s, "ok", oos_fills=20, oos_exposure=0.02)
        _trial(s, "too_few_fills", oos_fills=19, oos_exposure=0.5)
        _trial(s, "too_little_exposure", oos_fills=100, oos_exposure=0.01)
        s.commit()

    rows = client.get("/api/research/leaderboard").json()["rows"]
    assert [r["hash"] for r in rows] == ["ok"]


def test_in_sample_losers_are_excluded(client, session_factory):
    """Unchanged from the SQLite leaderboard: `is_sharpe > 0` as well as `oos_sharpe > 0`.

    A row that lost money in-sample is not an edge that decayed; it is a row that never worked
    and happened to get lucky out-of-sample.
    """
    with session_factory() as s:
        _trial(s, "good", is_sharpe=1.0)
        _trial(s, "is_loser", is_sharpe=-1.0)
        s.commit()

    rows = client.get("/api/research/leaderboard").json()["rows"]
    assert [r["hash"] for r in rows] == ["good"]


def test_market_strategy_and_timeframe_filters(client, session_factory):
    with session_factory() as s:
        _trial(s, "a", market="crypto", strategy="ema_cross", timeframe="1h")
        _trial(s, "b", market="stocks", strategy="ema_cross", timeframe="1h")
        _trial(s, "c", market="crypto", strategy="rsi_meanrev", timeframe="1d")
        s.commit()

    def hashes(query):
        return [r["hash"] for r in client.get(f"/api/research/leaderboard?{query}").json()["rows"]]

    assert sorted(hashes("market=crypto")) == ["a", "c"]
    assert sorted(hashes("strategy=ema_cross")) == ["a", "b"]
    assert hashes("timeframe=1d") == ["c"]
    assert hashes("market=crypto&timeframe=1h") == ["a"]


def test_pagination_reports_the_true_match_count(client, session_factory):
    """`total` counts matches, not the page -- "40 of 1,912" has to survive clicking through."""
    with session_factory() as s:
        # 1.0 .. 10.0, not 0.0 .. 9.0: a zero Sharpe is correctly excluded by the `oos_sharpe > 0`
        # filter, which would make this test about that rather than about the pager.
        for i in range(10):
            _trial(s, f"h{i}", oos_sharpe=float(i + 1))
        s.commit()

    body = client.get("/api/research/leaderboard?limit=3&offset=0").json()
    assert body["total"] == 10
    assert len(body["rows"]) == 3
    assert body["rows"][0]["oos_sharpe"] == 10.0

    page2 = client.get("/api/research/leaderboard?limit=3&offset=3").json()
    assert page2["total"] == 10
    assert page2["rows"][0]["oos_sharpe"] == 7.0


def test_an_empty_registry_returns_an_empty_page_not_an_error(client):
    """The state on a fresh database, before any cycle has run."""
    body = client.get("/api/research/leaderboard").json()
    assert body["rows"] == []
    assert body["total"] == 0
    assert body["total_trials"] == 0
    assert body["noise_ceiling"] == 0.0


def test_limit_is_bounded(client):
    assert client.get("/api/research/leaderboard?limit=100000").status_code == 422
    assert client.get("/api/research/leaderboard?limit=0").status_code == 422


# --- one trial ---------------------------------------------------------------------------------


def test_trial_detail_carries_both_sides_of_the_split(client, session_factory):
    """IS lives here and not on the leaderboard: it is what the search fitted.

    Next to OOS on a detail page the gap between them is informative; next to a ranking it would
    read as evidence.
    """
    with session_factory() as s:
        _trial(s, "detail", is_sharpe=3.0, oos_sharpe=0.5)
        s.commit()

    body = client.get("/api/research/trials/detail").json()
    assert body["is_sharpe"] == 3.0
    assert body["oos_sharpe"] == 0.5
    assert body["params"] == {"lookback": 20}
    assert body["oos_bars"] == 9000


def test_an_unknown_trial_hash_is_a_clean_404(client):
    response = client.get("/api/research/trials/nope")
    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


# --- paper candidates and status ---------------------------------------------------------------


def test_paper_candidates_are_ordered_by_promotion_date_not_performance(client, session_factory):
    """The one honest table in the module stays a forward record.

    Ordering it by how well each entry has done since promotion would quietly turn it into
    another leaderboard -- and the whole point is that nothing here was chosen after the fact.
    """
    with session_factory() as s:
        s.add(
            PaperCandidate(
                hash="second",
                promoted_at=dt.datetime(2026, 9, 2, tzinfo=dt.UTC),
                market="crypto",
                strategy="ema_cross",
                symbol="BTC/USDT",
                timeframe="1h",
                params={"a": 1},
                promoted_oos_sharpe=9.0,
            )
        )
        s.add(
            PaperCandidate(
                hash="first",
                promoted_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
                market="crypto",
                strategy="ema_cross",
                symbol="ETH/USDT",
                timeframe="1h",
                params={"a": 2},
                promoted_oos_sharpe=0.1,
            )
        )
        s.commit()

    rows = client.get("/api/research/paper").json()
    assert [r["hash"] for r in rows] == ["first", "second"]


def test_paper_payload_carries_every_gate_that_was_passed(client, session_factory):
    """The gates are the reason a candidate is on the list; the UI must be able to show them."""
    with session_factory() as s:
        s.add(
            PaperCandidate(
                hash="c",
                promoted_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
                market="crypto",
                strategy="ema_cross",
                symbol="BTC/USDT",
                timeframe="1h",
                params={"a": 1},
                promoted_oos_sharpe=1.4,
                sharpe_2x=0.8,
                neighbor_med=0.5,
                wf_pos=5,
                wf_active=6,
                wf_med=0.7,
                corr_max=0.3,
            )
        )
        s.commit()

    row = client.get("/api/research/paper").json()[0]
    assert row["sharpe_2x"] == 0.8
    assert row["neighbor_med"] == 0.5
    assert (row["wf_pos"], row["wf_active"]) == (5, 6)
    assert row["corr_max"] == 0.3


def test_status_reports_the_registry_and_the_filter_options(client, session_factory):
    with session_factory() as s:
        _trial(s, "a", market="crypto", run_date=dt.date(2026, 9, 18))
        _trial(s, "b", market="stocks", run_date=dt.date(2026, 9, 19))
        _trial(s, "c", market="stocks", run_date=dt.date(2026, 9, 19))
        s.commit()

    body = client.get("/api/research/status").json()
    assert body["total_trials"] == 3
    assert body["cycles"] == 2
    assert body["last_run_date"] == "2026-09-19"
    assert body["trials_last_cycle"] == 2
    assert body["markets"] == ["crypto", "stocks"]
    # Options come from the data, so a strategy family added later needs no frontend change.
    assert body["strategies"] == ["donchian_breakout"]


def test_status_on_an_empty_registry(client):
    body = client.get("/api/research/status").json()
    assert body == {
        "total_trials": 0,
        "paper_candidates": 0,
        "cycles": 0,
        "last_run_date": None,
        "trials_last_cycle": 0,
        "markets": [],
        "strategies": [],
        "timeframes": [],
    }


def test_every_research_route_lives_under_api_research(client):
    """Invariant from T75: no module is privileged at the root of `/api`."""
    # Read from the OpenAPI schema rather than `app.routes`: this FastAPI version keeps
    # included routers as lazy `_IncludedRouter` entries, so their routes are not in that list
    # at all. The schema is also the contract the frontend generates against, which makes it
    # the more honest thing to assert on.
    paths = [p for p in app.openapi()["paths"] if p.startswith("/api/research")]
    assert sorted(paths) == [
        "/api/research/leaderboard",
        "/api/research/paper",
        "/api/research/status",
        "/api/research/trials/{trial_hash}",
    ]


# --- the forward record (T83) ---------------------------------------------------------------


def _scored(s, h, promoted_at, **score):
    """A promoted candidate, optionally with one forward measurement."""
    s.add(
        PaperCandidate(
            hash=h,
            promoted_at=promoted_at,
            market="futures",
            strategy="rsi_meanrev",
            symbol="NQ=F",
            timeframe="1h",
            params={"n": 4},
            promoted_oos_sharpe=2.8,
            sharpe_2x=2.2,
            neighbor_med=1.8,
            wf_pos=5,
            wf_active=6,
            wf_med=1.1,
            corr_max=0.16,
        )
    )
    if score:
        s.add(PaperScore(hash=h, **score))


def test_paper_payload_carries_the_forward_record(client, session_factory):
    """Promotion-time gates describe a commitment; the `fwd_*` columns are its outcome.

    Before T83 the outcome was computed nightly and written only into a generated report, so
    this endpoint could say why a candidate was promoted and nothing about what happened next.
    """
    with session_factory() as s:
        _scored(
            s, "c", dt.datetime(2026, 7, 3, tzinfo=dt.UTC),
            scored_at=dt.datetime(2026, 9, 20, tzinfo=dt.UTC),
            fwd_days=79, fwd_bars=430, fwd_sharpe=1.1, fwd_return=0.06, fwd_max_dd=-0.04,
        )
        s.commit()

    row = client.get("/api/research/paper").json()[0]
    assert row["fwd_bars"] == 430
    assert row["fwd_sharpe"] == 1.1
    assert row["fwd_return"] == 0.06
    assert row["fwd_max_dd"] == -0.04
    assert row["fwd_days"] == 79
    assert row["scored_at"] is not None


def test_paper_serves_the_newest_measurement_of_a_candidate(client, session_factory):
    """`paper_scores` is append-only, so the read path has to pick the latest row itself."""
    with session_factory() as s:
        _scored(
            s, "c", dt.datetime(2026, 7, 3, tzinfo=dt.UTC),
            scored_at=dt.datetime(2026, 8, 1, tzinfo=dt.UTC),
            fwd_days=29, fwd_bars=120, fwd_sharpe=2.6, fwd_return=0.03, fwd_max_dd=-0.01,
        )
        s.add(
            PaperScore(
                hash="c", scored_at=dt.datetime(2026, 9, 20, tzinfo=dt.UTC),
                fwd_days=79, fwd_bars=430, fwd_sharpe=1.1,
                fwd_return=0.06, fwd_max_dd=-0.04,
            )
        )
        s.commit()

    row = client.get("/api/research/paper").json()[0]
    assert row["fwd_sharpe"] == 1.1, "the newest score, not the first and not the flattering one"


def test_an_unscored_candidate_reports_nulls_not_zeros(client, session_factory):
    """The distinction the whole module rests on: unmeasured is not the same as flat.

    A candidate promoted since the last cycle has no score row. Every forward field must come
    back null -- a `0.0` here would put it on the same footing as one measured at zero, which
    is exactly the kind of authoritative-looking number invariant 9 exists to prevent.
    """
    with session_factory() as s:
        _scored(s, "c", dt.datetime(2026, 9, 19, tzinfo=dt.UTC))
        s.commit()

    row = client.get("/api/research/paper").json()[0]
    assert row["scored_at"] is None
    assert row["fwd_sharpe"] is None
    assert row["fwd_bars"] is None
    assert row["fwd_return"] is None
    # The promotion-time half is unaffected -- the candidate is still fully described.
    assert row["promoted_oos_sharpe"] == 2.8
