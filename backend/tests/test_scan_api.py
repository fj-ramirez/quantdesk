"""Tests for `app/modules/gex/api/scan.py`. Offline: `get_session_factory` is monkeypatched at its import
site in `app.modules.gex.api.scan`, no real Postgres -- same pattern as `test_bars_api.py`. Timing against
the live, populated database (T43 acceptance: "returns within 2 s for 45 symbols x 500 bars")
is measured separately, outside pytest, and recorded in `app/modules/gex/api/scan.py`'s module docstring
and in the T43 report -- it cannot be an automated test without violating "every test must be
offline" (T43 task brief).
"""

from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.api.scan import router
from app.modules.gex.models.bars import DailyBar as DailyBarIn
from app.modules.gex.models.chain import ChainSnapshot, Underlying
from app.modules.gex.models.db import Base, GexByStrike, GexLevel, Snapshot
from app.modules.gex.providers.cboe import CboeProvider
from app.modules.gex.storage.bars_repository import upsert_bars
from app.modules.gex.storage.parquet import write_snapshot
from app.modules.gex.storage.repository import SnapshotRepository

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "cboe"


@pytest.fixture
def client():
    app = FastAPI()
    # T75: `/api/gex`, matching how `app/main.py` mounts `app.modules.gex.router` -- these
    # per-router mini-apps exist to keep the tests offline, not to serve a different URL space.
    app.include_router(router, prefix="/api/gex")
    return TestClient(app)


@pytest.fixture
def session_factory(tmp_path, monkeypatch):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    monkeypatch.setattr("app.modules.gex.api.scan.get_session_factory", lambda: factory)
    yield factory
    engine.dispose()


@pytest.fixture
def gex_session_factory(session_factory, monkeypatch):
    """T45's IV lookup uses a separate cached session factory (`app.modules.gex.jobs.capture
    .get_session_factory`, imported into `app.modules.gex.api.scan` as `get_gex_session_factory`) from the
    one `app.modules.gex.storage.bars_repository` uses for bars -- both point at the same underlying
    engine in production, and in tests both are monkeypatched onto the *same* SQLite engine
    `session_factory` already created (`Base.metadata.create_all` already made every table,
    `snapshots` included), so a `Snapshot` row written through this fixture and a bars row
    written through `session_factory` are visible to the same in-memory database.
    """
    monkeypatch.setattr("app.modules.gex.api.scan.get_gex_session_factory", lambda: session_factory)
    return session_factory


def _fixture_snapshot(symbol: str, filename: str) -> ChainSnapshot:
    """Replays a committed Cboe JSON fixture through the real `CboeProvider` -- the same
    pattern `test_report_api.py` uses -- to get a genuine `ChainSnapshot` with real IV values,
    without any network access.
    """
    payload = (FIXTURES_DIR / filename).read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    async def fetch() -> ChainSnapshot:
        transport_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await CboeProvider(client=transport_client).fetch_chain(symbol)
        finally:
            await transport_client.aclose()

    return asyncio.run(fetch())


def _index_snapshot(tmp_path, gex_session_factory, snapshot: ChainSnapshot) -> int:
    """Write `snapshot` to a `tmp_path` Parquet file and index it -- same pattern
    `test_report_api.py`'s `indexed_gld` fixture uses. No `DATA_DIR` monkeypatching needed:
    `write_snapshot(..., data_dir=tmp_path)` returns an absolute path outside
    `settings.DATA_DIR`, `SnapshotRepository.add` stores it as-is (`to_data_dir_relative_path`'s
    documented fallback for a path that isn't under the given base), and
    `resolve_snapshot_path` resolves an absolute stored path directly -- see that function's
    own docstring for why this round-trips correctly without touching global settings.
    """
    path = write_snapshot(snapshot, data_dir=tmp_path)
    with gex_session_factory() as session:
        return SnapshotRepository(session).add(snapshot, path, is_eod=True).id


def _universe(monkeypatch, symbols: list[str]) -> None:
    from app.core import config

    monkeypatch.setattr(config.settings, "SCAN_UNIVERSE", ",".join(symbols))


def _bar(symbol: str, date: dt.date, close: float, high=None, low=None, volume=1_000) -> DailyBarIn:
    return DailyBarIn(
        symbol=symbol,
        date=date,
        open=close,
        high=high if high is not None else close + 0.5,
        low=low if low is not None else close - 0.5,
        close=close,
        volume=volume,
        source="test",
    )


def _seed_monotone(
    session_factory,
    symbol: str,
    n_days: int,
    *,
    start: dt.date = dt.date(2026, 1, 5),
    start_close: float = 100.0,
) -> None:
    """A strictly increasing daily-close series -- every recorded event on it continues."""
    bars = [
        _bar(symbol, start + dt.timedelta(days=i), start_close + i, high=start_close + i + 0.5, low=start_close + i - 1.5)
        for i in range(n_days)
    ]
    upsert_bars(bars, session_factory=session_factory)


def _seed_flat_then_breakout(
    session_factory,
    symbol: str,
    *,
    n: int,
    baseline_days: int,
    trailing_days: int,
    start: dt.date = dt.date(2026, 1, 5),
) -> None:
    """`baseline_days` (>= n) flat bars establishing a clean range, one breakout bar, then
    `trailing_days` more bars -- used to control exactly how many bars have elapsed since the
    breakout (e.g. fewer than k, so it stays `pending`).
    """
    bars = []
    d = start
    for _ in range(baseline_days):
        bars.append(_bar(symbol, d, 100.0, high=100.5, low=99.5))
        d += dt.timedelta(days=1)
    bars.append(_bar(symbol, d, 110.0, high=110.5, low=109.5))  # the breakout bar
    d += dt.timedelta(days=1)
    for _ in range(trailing_days):
        bars.append(_bar(symbol, d, 111.0, high=111.5, low=110.5))
        d += dt.timedelta(days=1)
    upsert_bars(bars, session_factory=session_factory)


# --- GET /api/gex/scan/breakouts -----------------------------------------------------------------


def test_get_breakouts_returns_a_summary_per_universe_symbol(client, session_factory, monkeypatch):
    _seed_monotone(session_factory, "SPY", 60)
    _seed_monotone(session_factory, "QQQ", 60)
    _universe(monkeypatch, ["SPY", "QQQ"])

    response = client.get("/api/gex/scan/breakouts", params={"n": 20, "k": 5, "lookback": 40})

    assert response.status_code == 200
    body = response.json()
    assert body["n"] == 20
    assert body["k"] == 5
    assert body["lookback"] == 40
    symbols = {row["symbol"] for row in body["summaries"]}
    assert symbols == {"SPY", "QQQ"}
    for row in body["summaries"]:
        assert row["events"] >= 0
        assert row["status"] in (None, "continued", "failed", "pending")


def test_get_breakouts_default_params_match_the_plan_defaults(client, session_factory, monkeypatch):
    _seed_monotone(session_factory, "SPY", 60)
    _universe(monkeypatch, ["SPY"])

    response = client.get("/api/gex/scan/breakouts")
    assert response.status_code == 200
    body = response.json()
    assert body["n"] == 20
    assert body["k"] == 5
    assert body["lookback"] == 126


def test_get_breakouts_rejects_invalid_n(client, session_factory, monkeypatch):
    _universe(monkeypatch, ["SPY"])
    response = client.get("/api/gex/scan/breakouts", params={"n": 21})
    assert response.status_code == 422


def test_get_breakouts_rejects_invalid_k(client, session_factory, monkeypatch):
    _universe(monkeypatch, ["SPY"])
    response = client.get("/api/gex/scan/breakouts", params={"k": 4})
    assert response.status_code == 422


def test_get_breakouts_open_breakouts_panel_lists_pending_events(client, session_factory, monkeypatch):
    _seed_flat_then_breakout(
        session_factory, "SPY", n=20, baseline_days=20, trailing_days=3
    )  # 3 bars elapsed, k=10 -> still pending
    _universe(monkeypatch, ["SPY"])

    response = client.get("/api/gex/scan/breakouts", params={"n": 20, "k": 10, "lookback": 126})
    assert response.status_code == 200
    body = response.json()

    assert len(body["open_breakouts"]) == 1
    open_row = body["open_breakouts"][0]
    assert open_row["symbol"] == "SPY"
    assert open_row["direction"] == "up"
    assert open_row["bars_elapsed"] == 3

    spy_summary = next(row for row in body["summaries"] if row["symbol"] == "SPY")
    assert spy_summary["pending"] == 1
    assert spy_summary["status"] == "pending"


def test_get_breakouts_excludes_a_symbol_with_a_gappy_history(client, session_factory, monkeypatch):
    # A 2026 Monday start (a year `app.modules.gex.jobs.calendar` actually covers) with rows only every 3
    # calendar days -- roughly half the expected trading days in the span are missing, well
    # over the 2% exclusion threshold.
    start = dt.date(2026, 1, 5)
    bars = [_bar("GAPPY", start + dt.timedelta(days=3 * i), 100.0 + i) for i in range(10)]
    upsert_bars(bars, session_factory=session_factory)
    _universe(monkeypatch, ["GAPPY"])

    response = client.get("/api/gex/scan/breakouts", params={"lookback": 10})
    assert response.status_code == 200
    body = response.json()

    assert body["summaries"] == []
    assert body["open_breakouts"] == []
    assert len(body["excluded"]) == 1
    assert body["excluded"][0]["symbol"] == "GAPPY"
    assert "missing" in body["excluded"][0]["reason"]


def test_get_breakouts_symbol_with_no_bars_contributes_an_empty_summary(client, session_factory, monkeypatch):
    _universe(monkeypatch, ["NEWLY_ADDED"])
    response = client.get("/api/gex/scan/breakouts")
    assert response.status_code == 200
    body = response.json()
    assert len(body["summaries"]) == 1
    row = body["summaries"][0]
    assert row["symbol"] == "NEWLY_ADDED"
    assert row["events"] == 0
    assert row["rate"] is None
    assert row["last_event"] is None
    assert row["status"] is None


# --- GET /api/gex/scan/breakouts/{symbol} -----------------------------------------------------


def test_get_symbol_breakouts_returns_event_list(client, session_factory, monkeypatch):
    _seed_monotone(session_factory, "SPY", 60)
    response = client.get("/api/gex/scan/breakouts/SPY", params={"n": 20, "k": 5, "lookback": 40})
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "SPY"
    assert body["n"] == 20
    assert body["k"] == 5
    assert isinstance(body["events"], list)
    assert len(body["events"]) > 0
    for event in body["events"]:
        assert event["direction"] == "up"
        assert set(event) >= {
            "date",
            "direction",
            "level",
            "close",
            "outcome",
            "resolved_at",
            "bars_elapsed",
            "follow_through_atr",
            "excursion_atr",
            "mfe_atr",
            "mae_atr",
        }


def test_get_symbol_breakouts_no_bars_returns_clean_empty_result(client, session_factory):
    response = client.get("/api/gex/scan/breakouts/NOPE")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "NOPE"
    assert body["events"] == []


def test_get_symbol_breakouts_is_case_insensitive_and_normalizes_symbol(client, session_factory):
    _seed_monotone(session_factory, "SPY", 60)
    response = client.get("/api/gex/scan/breakouts/spy")
    assert response.status_code == 200
    assert response.json()["symbol"] == "SPY"


def test_get_symbol_breakouts_percent_encoded_caret_symbol(client, session_factory):
    _seed_monotone(session_factory, "^VIX", 60)
    response = client.get("/api/gex/scan/breakouts/%5EVIX")
    assert response.status_code == 200
    assert response.json()["symbol"] == "^VIX"


def test_get_symbol_breakouts_rejects_invalid_n(client, session_factory):
    response = client.get("/api/gex/scan/breakouts/SPY", params={"n": 7})
    assert response.status_code == 422


def test_get_symbol_breakouts_not_excluded_for_gaps_unlike_universe_route(client, session_factory):
    """The single-symbol lookup is an explicit request, not a scan aggregate -- a gappy series
    still returns whatever events it has rather than being silently dropped."""
    start = dt.date(2026, 1, 5)
    bars = [_bar("GAPPY", start + dt.timedelta(days=3 * i), 100.0 + i) for i in range(30)]
    upsert_bars(bars, session_factory=session_factory)
    response = client.get("/api/gex/scan/breakouts/GAPPY", params={"lookback": 10})
    assert response.status_code == 200
    assert response.json()["symbol"] == "GAPPY"


# --- GET /api/gex/scan/trend (T45) -----------------------------------------------------------------


@pytest.fixture(scope="module")
def spy_snapshot() -> ChainSnapshot:
    return _fixture_snapshot("SPY", "spy.json")


def test_get_trend_returns_iv30_none_for_no_chain_and_a_number_for_spy(
    client, session_factory, gex_session_factory, tmp_path, monkeypatch, spy_snapshot
):
    """Plan's own acceptance criterion, verbatim: "`GET /api/gex/scan/trend` returns `iv30=None`
    for a symbol without a chain and a number for SPY"."""
    _seed_monotone(session_factory, "SPY", 150)
    _seed_monotone(session_factory, "NOCHAIN", 150)
    _universe(monkeypatch, ["SPY", "NOCHAIN"])
    _index_snapshot(tmp_path, gex_session_factory, spy_snapshot)

    response = client.get("/api/gex/scan/trend")
    assert response.status_code == 200
    body = response.json()
    rows = {row["symbol"]: row for row in body["rows"]}
    assert set(rows) == {"SPY", "NOCHAIN"}

    assert rows["NOCHAIN"]["iv30"] is None
    assert rows["NOCHAIN"]["iv_rv_ratio"] is None

    assert isinstance(rows["SPY"]["iv30"], float)
    assert rows["SPY"]["iv30"] > 0.0
    assert rows["SPY"]["iv_rv_ratio"] is not None


def test_get_trend_returns_iv30_none_for_a_covered_underlying_never_captured(
    client, session_factory, gex_session_factory, monkeypatch
):
    """`QQQ` is one of the 28 `Underlying`-covered symbols but has no indexed `Snapshot` row in
    this test's database -- a different reason for `iv30=None` than "not option-covered at
    all," and `_lookup_iv30` must degrade the same way for both.
    """
    _seed_monotone(session_factory, "QQQ", 150)
    _universe(monkeypatch, ["QQQ"])

    response = client.get("/api/gex/scan/trend")
    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["symbol"] == "QQQ"
    assert row["iv30"] is None


def test_get_trend_includes_symbol_with_insufficient_history_as_none_row(
    client, session_factory, gex_session_factory, monkeypatch
):
    """Acceptance item 4 applied at the API layer: a symbol with too little history is still a
    row (not excluded the way `/breakouts` excludes a gappy symbol), with `None` components.
    """
    bars = [
        _bar("NEWSYM", dt.date(2026, 1, 5) + dt.timedelta(days=i), 100.0 + i) for i in range(5)
    ]
    upsert_bars(bars, session_factory=session_factory)
    _universe(monkeypatch, ["NEWSYM"])

    response = client.get("/api/gex/scan/trend")
    assert response.status_code == 200
    body = response.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["symbol"] == "NEWSYM"
    assert row["adx14"] is None
    assert row["er20"] is None
    assert row["chop14"] is None
    assert row["composite"] is None


def test_get_trend_rows_carry_percentiles_and_composite_across_universe(
    client, session_factory, gex_session_factory, monkeypatch
):
    _seed_monotone(session_factory, "TRENDY", 150)  # strictly increasing -> high composite
    n = 150
    cum = [100.0]
    for i in range(1, n):
        cum.append(cum[-1] + (1.0 if i % 2 == 0 else -1.0))
    choppy_bars = [
        _bar("CHOPPY", dt.date(2026, 1, 5) + dt.timedelta(days=i), cum[i], high=cum[i] + 0.1, low=cum[i] - 0.1)
        for i in range(n)
    ]
    upsert_bars(choppy_bars, session_factory=session_factory)
    _universe(monkeypatch, ["TRENDY", "CHOPPY"])

    response = client.get("/api/gex/scan/trend")
    assert response.status_code == 200
    rows = {row["symbol"]: row for row in response.json()["rows"]}

    assert rows["TRENDY"]["composite"] > rows["CHOPPY"]["composite"]
    assert rows["TRENDY"]["adx_pct"] == 1.0
    assert rows["CHOPPY"]["adx_pct"] == 0.0


# --- GET /api/gex/scan/trend/{symbol} (T45) -------------------------------------------------------


def test_get_symbol_trend_returns_current_and_history(client, session_factory, gex_session_factory):
    _seed_monotone(session_factory, "SPY", 150)
    response = client.get("/api/gex/scan/trend/SPY")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "SPY"
    assert body["current"]["adx14"] is not None
    assert body["current"]["iv30"] is None  # no indexed snapshot in this test
    assert len(body["history"]) == 126  # TREND_LOOKBACK
    for point in body["history"]:
        assert set(point) == {"date", "adx14", "er20", "chop14", "rv20"}


def test_get_symbol_trend_no_bars_returns_clean_empty_result(client, session_factory, gex_session_factory):
    response = client.get("/api/gex/scan/trend/NOPE")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "NOPE"
    assert body["history"] == []
    assert all(v is None for v in body["current"].values())


def test_get_symbol_trend_is_case_insensitive(client, session_factory, gex_session_factory):
    _seed_monotone(session_factory, "SPY", 150)
    response = client.get("/api/gex/scan/trend/spy")
    assert response.status_code == 200
    assert response.json()["symbol"] == "SPY"


# --- GET /api/gex/scan/rotation (T50) -------------------------------------------------------------


def test_get_rotation_rejects_unknown_group_422(client):
    """Acceptance item 5: the API rejects an unknown group with 422. No bars need to be
    seeded -- `_validate_group` runs before any I/O, so this never touches the (unmocked, in
    this test) real session factory at all.
    """
    response = client.get("/api/gex/scan/rotation", params={"group": "nope"})
    assert response.status_code == 422


def test_get_rotation_rejects_unknown_benchmark_422(client):
    response = client.get("/api/gex/scan/rotation", params={"benchmark": "NOPE"})
    assert response.status_code == 422


def test_get_rotation_rejects_weeks_out_of_range_422(client):
    response = client.get("/api/gex/scan/rotation", params={"weeks": 0})
    assert response.status_code == 422
    response = client.get("/api/gex/scan/rotation", params={"weeks": 27})
    assert response.status_code == 422


def test_get_rotation_returns_full_shape_for_seeded_and_unseeded_symbols(
    client, session_factory, monkeypatch
):
    """One seeded sector (`XLK`) and the default benchmark (`SPY`) get real, non-`None` trail
    points once warm-up completes; an unseeded sector (`XLF`) still appears in `symbols` (every
    member of the requested group gets a row -- the same "never silently drop a universe
    member" contract `/trend` already establishes) with a trail of `None`s rather than being
    omitted or erroring.
    """
    anchor = dt.date(2026, 6, 5)  # a Friday -- arbitrary, just fixed so the test is deterministic
    monkeypatch.setattr("app.modules.gex.api.scan._today", lambda: anchor)

    start = anchor - dt.timedelta(days=299)
    _seed_monotone(session_factory, "XLK", 300, start=start, start_close=50.0)
    _seed_monotone(session_factory, "SPY", 300, start=start, start_close=400.0)

    response = client.get("/api/gex/scan/rotation", params={"group": "sectors", "weeks": 5})
    assert response.status_code == 200
    body = response.json()

    assert body["group"] == "sectors"
    assert body["benchmark"] == "SPY"
    assert body["weeks"] == 5
    assert body["w"] == 14
    assert "approx" in body["note"].lower()
    assert body["breadth"]["label"] == "sector-level breadth"

    symbols = {row["symbol"] for row in body["symbols"]}
    from app.modules.gex.scan.groups import SECTORS

    assert symbols == set(SECTORS)

    xlk = next(row for row in body["symbols"] if row["symbol"] == "XLK")
    assert len(xlk["trail"]) == 5
    # ~300 days is comfortably more than the ~38-week warm-up (2*14 weeks for the z-score
    # stack, plus the 5-week trail) this route's own lookback is sized for.
    last_point = xlk["trail"][-1]
    assert last_point["rs_ratio_approx"] is not None
    assert last_point["rs_momentum_approx"] is not None
    assert all(key in last_point for key in ("date", "rs_ratio_approx", "rs_momentum_approx"))

    xlf = next(row for row in body["symbols"] if row["symbol"] == "XLF")
    assert len(xlf["trail"]) == 5
    assert all(p["rs_ratio_approx"] is None for p in xlf["trail"])
    assert all(p["rs_momentum_approx"] is None for p in xlf["trail"])
    assert xlf["return_5"] is None
    assert xlf["return_20"] is None
    assert xlf["return_65"] is None


def test_get_rotation_accepts_rsp_benchmark(client, session_factory, monkeypatch):
    anchor = dt.date(2026, 6, 5)
    monkeypatch.setattr("app.modules.gex.api.scan._today", lambda: anchor)
    start = anchor - dt.timedelta(days=299)
    _seed_monotone(session_factory, "SPY", 300, start=start, start_close=400.0)
    _seed_monotone(session_factory, "RSP", 300, start=start, start_close=150.0)
    _seed_monotone(session_factory, "QQQ", 300, start=start, start_close=300.0)

    response = client.get(
        "/api/gex/scan/rotation", params={"group": "assets", "benchmark": "RSP"}
    )
    assert response.status_code == 200
    assert response.json()["benchmark"] == "RSP"


# --- GET /api/gex/scan/regime (T48, plans/continuation/03-regime-board.md) ------------------------

_NY = ZoneInfo("America/New_York")


def _ny(y, m, d, h, mi) -> dt.datetime:
    return dt.datetime(y, m, d, h, mi, tzinfo=_NY).astimezone(dt.UTC)


def _seed_regime_snapshot(
    gex_session_factory,
    underlying: str,
    *,
    captured_at: dt.datetime,
    spot: float = 100.0,
    is_eod: bool = True,
    source: str = "cboe",
) -> int:
    with gex_session_factory() as session:
        snap = Snapshot(
            underlying=underlying,
            captured_at=captured_at,
            source=source,
            spot=spot,
            contract_count=100,
            parquet_path=f"{underlying}/unused.parquet",  # never opened by /regime
            is_eod=is_eod,
        )
        session.add(snap)
        session.commit()
        return snap.id


def _seed_regime_levels(
    gex_session_factory,
    snapshot_id: int,
    filter_value: str,
    *,
    net_gex: float,
    call_wall: float | None,
    call_wall_gex: float | None,
    put_wall: float | None,
    put_wall_gex: float | None,
    flip_point: float | None,
    spot: float,
    computed_at: dt.datetime,
) -> None:
    with gex_session_factory() as session:
        session.add(
            GexLevel(
                snapshot_id=snapshot_id,
                filter=filter_value,
                net_gex=net_gex,
                call_wall=call_wall,
                call_wall_gex=call_wall_gex,
                put_wall=put_wall,
                put_wall_gex=put_wall_gex,
                max_abs_strike=call_wall,
                max_call_gex_strike=call_wall,
                max_put_gex_strike=put_wall,
                flip_point=flip_point,
                spot=spot,
                computed_at=computed_at,
            )
        )
        session.commit()


def _seed_regime_by_strike(
    gex_session_factory,
    snapshot_id: int,
    filter_value: str,
    rows: list[tuple[float, float, float]],
) -> None:
    with gex_session_factory() as session:
        for strike, call_gex, put_gex in rows:
            session.add(
                GexByStrike(
                    snapshot_id=snapshot_id,
                    filter=filter_value,
                    strike=strike,
                    call_gex=call_gex,
                    put_gex=put_gex,
                    net_gex=call_gex + put_gex,
                )
            )
        session.commit()


def test_get_regime_returns_a_row_per_underlying_with_none_for_uncaptured_symbols(
    client, session_factory, gex_session_factory
):
    """T48 acceptance item, verbatim: "the API returns a row per core and extended symbol with
    `None` where inputs are missing." Nothing is seeded at all here.
    """
    response = client.get("/api/gex/scan/regime")
    assert response.status_code == 200
    body = response.json()
    assert body["filter"] == "ALL"

    symbols = {row["underlying"] for row in body["rows"]}
    assert symbols == {u.value for u in Underlying}

    for row in body["rows"]:
        assert row["verdict"] is None
        assert row["positioning"] is None
        assert row["reasons"] == ["no snapshot captured yet for this symbol"]


def test_get_regime_computes_a_fresh_fade_row_for_a_seeded_symbol(
    client, session_factory, gex_session_factory
):
    """A fresh (at-the-close) EOD snapshot, walls/flip arranged like `test_scan_regime.py`'s
    own `test_verdict_fade_with_zero_dte_share_unavailable_drops_the_clause` fixture -- the
    ordinary case on real EOD data, where `ZERO_DTE` admits nothing.
    """
    # `_seed_monotone`'s bars have a constant true range of 2.0 (high=close+0.5, low=close-1.5,
    # so |high-prev_close|=1.5, |low-prev_close|=0.5, range=2.0 -- all three below the plain
    # `high-low=2.0`), so ATR14 settles at exactly 2.0 -- walls/flip below are sized in *that*
    # unit (not the 5.0 `test_scan_regime.py`'s hand-built fixtures use), since this test
    # exercises the real `atr()` computation off real bars, not a stand-in ATR value.
    captured_at = _ny(2026, 1, 5, 16, 20)  # outside the session -> clamps to 16:00+15 (fresh)
    snap_id = _seed_regime_snapshot(gex_session_factory, "SPY", captured_at=captured_at, spot=100.0)
    _seed_regime_levels(
        gex_session_factory,
        snap_id,
        "ALL",
        net_gex=5.0e9,
        call_wall=101.5,  # 1.5 away = 0.75 ATR, clears fade's "nearest wall within 1 ATR"
        call_wall_gex=8.0e9,
        put_wall=98.5,  # 1.5 away = 0.75 ATR
        put_wall_gex=-6.0e9,
        flip_point=94.0,  # 6.0 away = 3.0 ATR, clears fade's "flip more than 1 ATR below spot"
        spot=100.0,
        computed_at=captured_at,
    )
    _seed_regime_by_strike(
        gex_session_factory, snap_id, "ALL", [(98.5, 0.0, -6.0), (101.5, 8.0, 0.0)]
    )
    # ZERO_DTE: no rows at all -- the structural, live-measured (2026-09-09) EOD case.
    _seed_monotone(session_factory, "SPY", 60, start=dt.date(2025, 10, 1), start_close=90.0)

    response = client.get("/api/gex/scan/regime")
    assert response.status_code == 200
    body = response.json()
    spy = next(row for row in body["rows"] if row["underlying"] == "SPY")

    assert spy["stale"] is False
    assert spy["zero_dte_share"] is None
    assert spy["verdict"] == "fade"
    assert any("unavailable" in r and "dropped" in r for r in spy["reasons"])
    assert spy["atr14"] is not None  # bars were seeded, so ATR14 should compute


def test_get_regime_dia_fixture_never_yields_a_verdict(client, session_factory, gex_session_factory):
    """T48's hard acceptance item, verbatim: "a DIA fixture must never yield a verdict" --
    `docs/validation.md`'s own measured DIA figure, net GEX 0.9% of gross.
    """
    captured_at = _ny(2026, 1, 5, 16, 20)
    snap_id = _seed_regime_snapshot(gex_session_factory, "DIA", captured_at=captured_at, spot=400.0)
    _seed_regime_levels(
        gex_session_factory,
        snap_id,
        "ALL",
        net_gex=0.9,
        call_wall=404.0,
        call_wall_gex=50.0,
        put_wall=396.0,
        put_wall_gex=-49.0,
        flip_point=390.0,
        spot=400.0,
        computed_at=captured_at,
    )
    _seed_regime_by_strike(
        gex_session_factory, snap_id, "ALL", [(396.0, 0.0, -50.0), (404.0, 50.0, 0.0)]
    )

    response = client.get("/api/gex/scan/regime")
    assert response.status_code == 200
    dia = next(row for row in response.json()["rows"] if row["underlying"] == "DIA")

    assert dia["positioning"]["noise_dominated"] is True
    assert dia["verdict"] is None


def test_get_regime_stale_snapshot_suppresses_verdict(client, session_factory, gex_session_factory):
    """T47's verified-facts case: an EOD-flagged row whose `captured_at` is well before the
    16:00 close (here, 11:39 ET -- XBI's own live figure) must not rank alongside a genuinely
    fresh chain. Same otherwise-fade inputs as the fresh test above.
    """
    captured_at = _ny(2026, 1, 5, 11, 39)  # inside the regular session -> NOT clamped forward
    snap_id = _seed_regime_snapshot(gex_session_factory, "XBI", captured_at=captured_at, spot=100.0)
    _seed_regime_levels(
        gex_session_factory,
        snap_id,
        "ALL",
        net_gex=5.0e9,
        call_wall=104.0,
        call_wall_gex=8.0e9,
        put_wall=96.0,
        put_wall_gex=-6.0e9,
        flip_point=90.0,
        spot=100.0,
        computed_at=captured_at,
    )
    _seed_regime_by_strike(
        gex_session_factory,
        snap_id,
        "ALL",
        [(96.0, 0.0, -6.0), (100.0, 1.0, -1.0), (104.0, 8.0, 0.0)],
    )

    response = client.get("/api/gex/scan/regime")
    assert response.status_code == 200
    xbi = next(row for row in response.json()["rows"] if row["underlying"] == "XBI")

    assert xbi["stale"] is True
    assert xbi["chain_age_minutes"] == pytest.approx(4 * 60 + 36, abs=1.0)  # 11:39 -> 16:15
    assert xbi["verdict"] is None
    assert any("stale" in r for r in xbi["reasons"])


def test_get_regime_rejects_unpersisted_filter_422(client, session_factory, gex_session_factory):
    response = client.get("/api/gex/scan/regime", params={"filter": "THIS_WEEK"})
    assert response.status_code == 422


def test_get_regime_accepts_zero_dte_filter(client, session_factory, gex_session_factory):
    """`ZERO_DTE` is a persisted filter (`app.modules.gex.gex.store.DEFAULT_FILTERS`) even though it is
    structurally empty on EOD data -- the endpoint must accept it (422 only on a filter that is
    never persisted at all, e.g. `THIS_WEEK`), returning rows with every GEX-derived field
    `None` for a symbol whose `ZERO_DTE` levels admitted nothing.
    """
    captured_at = _ny(2026, 1, 5, 16, 20)
    snap_id = _seed_regime_snapshot(gex_session_factory, "QQQ", captured_at=captured_at, spot=100.0)
    _seed_regime_levels(
        gex_session_factory,
        snap_id,
        "ALL",
        net_gex=5.0e9,
        call_wall=104.0,
        call_wall_gex=8.0e9,
        put_wall=96.0,
        put_wall_gex=-6.0e9,
        flip_point=90.0,
        spot=100.0,
        computed_at=captured_at,
    )
    _seed_regime_by_strike(gex_session_factory, snap_id, "ALL", [(104.0, 8.0, 0.0)])
    # No GexLevel/GexByStrike rows at all for ZERO_DTE -- exactly the live structural case.

    response = client.get("/api/gex/scan/regime", params={"filter": "ZERO_DTE"})
    assert response.status_code == 200
    qqq = next(row for row in response.json()["rows"] if row["underlying"] == "QQQ")
    # No `GexLevel` row for `ZERO_DTE` at all -- `_load_gex_inputs` returns `None` for this
    # filter, same "None where inputs are missing" contract as a never-captured symbol.
    assert qqq["verdict"] is None
    assert qqq["reasons"] == ["no snapshot captured yet for this symbol"]
