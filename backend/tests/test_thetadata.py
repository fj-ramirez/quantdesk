"""T26/T126: the ThetaData history client, the snapshot join, the history loader and the
live provider.

The CSV bodies below are **hand-built from ThetaData's v3 docs** (see the provider's module
docstring), not recorded traffic -- the terminal had not run when this was written. What they
pin is this repo's own rules (plan rules 2-4 in plans/thetadata/README.md), which hold whatever
the live headers turn out to be.
"""

from __future__ import annotations

import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from app.core.db import get_engine, get_sessionmaker
from app.modules.gex.gex.history import capture_window_open, load_history, pending_sessions
from app.modules.gex.models.chain import ChainSnapshot, OptionContract, Underlying
from app.modules.gex.models.db import Base, GexLevel, Snapshot
from app.modules.gex.providers.base import ProviderError, SymbolNotSupported, UpstreamUnavailable
from app.modules.gex.providers.thetadata import (
    ThetaDataClient,
    ThetaDataProvider,
    build_eod_snapshot,
    build_live_snapshot,
)

GREEKS_HEADER = (
    "symbol,expiration,strike,right,timestamp,open,high,low,close,volume,count,bid,ask,"
    "delta,gamma,theta,vega,rho,implied_vol,iv_error,underlying_price,underlying_timestamp\n"
)
OI_HEADER = "symbol,expiration,strike,right,timestamp,open_interest\n"


def greeks_csv(root: str, underlying_price: str = "500.0") -> str:
    return GREEKS_HEADER + "".join(
        f"{root},2026-12-18,{strike},{right},2026-09-04T17:15:00,1,1,1,{close},{vol},3,1.0,1.2,"
        f"0.5,0.01,-0.05,{vega},0.1,{iv},0.0,{underlying_price},2026-09-04T16:00:00\n"
        for strike, right, close, vol, vega, iv in [
            ("505.000", "CALL", "2.5", "100", "45.0", "0.2"),
            ("495.000", "PUT", "2.4", "80", "44.0", "0.21"),
            ("600.000", "CALL", "0", "0", "0", "0"),  # no IV: must stay None, not 0
        ]
    )


def oi_csv(root: str, oi: int = 1000) -> str:
    # 505 C has OI, 495 P is a real zero, 600 C has no row at all (unknown).
    return (
        OI_HEADER
        + f"{root},2026-12-18,505.000,CALL,2026-09-04T06:30:00,{oi}\n"
        + f"{root},2026-12-18,495.000,PUT,2026-09-04T06:30:00,0\n"
    )


def rows(root: str, body: str) -> list[tuple[str, dict[str, str]]]:
    import csv
    import io

    return [(root, r) for r in csv.DictReader(io.StringIO(body))]


SESSION = dt.date(2026, 9, 4)


def test_snapshot_joins_oi_and_keeps_unknown_distinct_from_zero():
    snap = build_eod_snapshot(Underlying.SPY, SESSION, rows("SPY", greeks_csv("SPY")), rows("SPY", oi_csv("SPY")))
    by_symbol = {c.occ_symbol: c for c in snap.contracts}
    assert by_symbol["SPY261218C00505000"].open_interest == 1000
    assert by_symbol["SPY261218P00495000"].open_interest == 0  # zero is zero
    assert by_symbol["SPY261218C00600000"].open_interest is None  # missing is unknown
    assert by_symbol["SPY261218C00600000"].iv is None
    assert by_symbol["SPY261218C00505000"].vega == pytest.approx(0.45)
    assert snap.spot == 500.0
    assert snap.source == "thetadata" and snap.delayed_minutes == 0
    # 16:00 New York on the session (EDT in September -> 20:00 UTC)
    assert snap.captured_at == dt.datetime(2026, 9, 4, 20, 0, tzinfo=dt.UTC)


def test_spx_merges_both_roots():
    greeks = rows("SPX", greeks_csv("SPX", "5000")) + rows("SPXW", greeks_csv("SPXW", "5000"))
    oi = rows("SPX", oi_csv("SPX")) + rows("SPXW", oi_csv("SPXW"))
    snap = build_eod_snapshot(Underlying.SPX, SESSION, greeks, oi)
    roots = {c.root for c in snap.contracts}
    assert roots == {"SPX", "SPXW"}
    assert len(snap.contracts) == 6


def test_no_underlying_price_raises_instead_of_inventing_spot():
    with pytest.raises(UpstreamUnavailable, match="underlying_price"):
        build_eod_snapshot(Underlying.SPX, SESSION, rows("SPX", greeks_csv("SPX", "")), [])


def test_client_without_url_names_the_setting():
    with pytest.raises(ProviderError, match="THETADATA_URL"):
        ThetaDataClient(base_url="")


def make_transport(seen: list[httpx.Request], *, fail_first: bool = False, same_every_day: bool = False):
    state = {"failed": not fail_first}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if not state["failed"]:
            state["failed"] = True
            return httpx.Response(503)
        root = request.url.params["symbol"]
        if request.url.path.endswith("/greeks/eod"):
            day = request.url.params["start_date"]
            if day == "20260907":  # Labor Day would never be asked; a no-data day is 472
                return httpx.Response(472, text="No data")
            return httpx.Response(200, text=greeks_csv(root))
        # Real OI moves every session; a chain identical to the last stored one is what T71's
        # content dedupe exists to refuse, so a fixture must vary unless that is the point.
        oi = 1000 if same_every_day else int(request.url.params["date"][-2:]) * 100
        return httpx.Response(200, text=oi_csv(root, oi))

    return httpx.MockTransport(handler)


async def test_oi_is_the_report_dated_on_the_session_not_the_next_day():
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=make_transport(seen)) as http:
        client = ThetaDataClient(base_url="http://theta", client=http, backoff_seconds=0)
        greeks, oi = await client.eod_rows(Underlying.SPY, SESSION)
    assert greeks and oi
    oi_request = next(r for r in seen if r.url.path.endswith("/open_interest"))
    assert oi_request.url.params["date"] == "20260904"
    assert oi_request.url.params["expiration"] == "*"


async def test_client_retries_then_succeeds_and_treats_472_as_empty():
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=make_transport(seen, fail_first=True)) as http:
        client = ThetaDataClient(base_url="http://theta", client=http, backoff_seconds=0)
        got = await client.get_rows("/v3/option/history/greeks/eod", {"symbol": "SPY", "start_date": "20260904"})
        empty = await client.get_rows("/v3/option/history/greeks/eod", {"symbol": "SPY", "start_date": "20260907"})
    assert len(got) == 3 and empty == []
    assert len(seen) == 3  # 503, retry, then the 472


@pytest.fixture
def session_factory(tmp_path):
    engine = get_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    yield factory
    engine.dispose()


def seed_cboe_eod(factory, tmp_path, day: dt.date) -> None:
    """An existing live EOD snapshot for ``day``, which the loader must leave alone."""
    from app.modules.gex.storage.parquet import write_snapshot
    from app.modules.gex.storage.repository import SnapshotRepository

    snap = ChainSnapshot(
        underlying=Underlying.SPY,
        spot=501.0,
        captured_at=dt.datetime.combine(day, dt.time(20, 20), tzinfo=dt.UTC),
        source="cboe",
        delayed_minutes=15,
        contracts=[OptionContract.from_occ("SPY261218C00505000", open_interest=5, iv=0.2)],
    )
    with factory() as session:
        SnapshotRepository(session).add(snap, write_snapshot(snap, data_dir=tmp_path), is_eod=True, data_dir=tmp_path)


async def test_history_loads_missing_sessions_skips_live_ones_and_resumes(tmp_path, session_factory):
    seed_cboe_eod(session_factory, tmp_path, dt.date(2026, 9, 3))
    seen: list[httpx.Request] = []
    start, end = dt.date(2026, 9, 2), dt.date(2026, 9, 8)  # Wed..Tue across Labor Day (9/7)
    async with httpx.AsyncClient(transport=make_transport(seen)) as http:
        client = ThetaDataClient(base_url="http://theta", client=http, backoff_seconds=0)
        first = await load_history(
            [Underlying.SPY], start, end, client=client, session_factory=session_factory, data_dir=tmp_path
        )
        second = await load_history(
            [Underlying.SPY], start, end, client=client, session_factory=session_factory, data_dir=tmp_path
        )

    # 9/2, 9/4, 9/8 -- 9/3 is Cboe's, 9/5-9/6 weekend, 9/7 Labor Day
    assert first == {"pending": 3, "loaded": 3, "failed": 0, "duplicate": 0}
    assert second["pending"] == 0 and second["loaded"] == 0

    with session_factory() as session:
        snaps = session.execute(select(Snapshot).order_by(Snapshot.session_date)).scalars().all()
        assert [(s.session_date, s.source) for s in snaps] == [
            (dt.date(2026, 9, 2), "thetadata"),
            (dt.date(2026, 9, 3), "cboe"),
            (dt.date(2026, 9, 4), "thetadata"),
            (dt.date(2026, 9, 8), "thetadata"),
        ]
        assert all(s.is_eod for s in snaps)
        theta_ids = [s.id for s in snaps if s.source == "thetadata"]
        levels = session.execute(select(GexLevel.snapshot_id).where(GexLevel.snapshot_id.in_(theta_ids))).scalars()
        assert set(levels) == set(theta_ids)  # levels computed by the live capture path
        assert pending_sessions(session, Underlying.SPY, start, end) == []


def test_capture_window_guard():
    ny = dt.timezone(dt.timedelta(hours=-4))
    assert capture_window_open(dt.datetime(2026, 9, 24, 16, 20, tzinfo=ny))
    assert not capture_window_open(dt.datetime(2026, 9, 24, 21, 0, tzinfo=ny))
    assert not capture_window_open(dt.datetime(2026, 9, 26, 16, 20, tzinfo=ny))  # Saturday


async def test_a_stale_report_identical_to_the_last_session_is_not_stored_twice(tmp_path, session_factory):
    """T71's content dedupe applies to history too: two sessions with a byte-identical chain
    mean the vendor served a stale report, and the second is skipped, not written."""
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=make_transport(seen, same_every_day=True)) as http:
        client = ThetaDataClient(base_url="http://theta", client=http, backoff_seconds=0)
        counts = await load_history(
            [Underlying.SPY], dt.date(2026, 9, 3), dt.date(2026, 9, 4),
            client=client, session_factory=session_factory, data_dir=tmp_path,
        )
    assert counts == {"pending": 2, "loaded": 1, "failed": 0, "duplicate": 1}


# --- T126: the live provider ------------------------------------------------------------------

LIVE_HEADER = (
    "symbol,expiration,strike,right,timestamp,bid,ask,delta,theta,vega,rho,epsilon,lambda,"
    "implied_vol,iv_error,underlying_timestamp,underlying_price\n"
)


def live_csv(root: str, expiry: str = "2026-09-24", stamps: tuple[str, str] = ("10:59:58.120", "11:00:01.500")) -> str:
    """``greeks/first_order`` rows: no ``close``, ``volume`` or ``gamma`` columns at all."""
    return LIVE_HEADER + "".join(
        f"{root},{expiry},{strike},{right},2026-09-24T{stamp},1.0,1.2,0.5,-0.9,5.0,0.01,0,0,"
        f"{iv},0.0,2026-09-24T11:00:00.000,500.0\n"
        for (strike, right, iv), stamp in zip(
            [("505.000", "CALL", "0.18"), ("495.000", "PUT", "0.2")], stamps, strict=True
        )
    )


def live_oi_csv(root: str, expiry: str = "2026-09-24") -> str:
    return (
        OI_HEADER
        + f"{root},{expiry},505.000,CALL,2026-09-24T06:30:00,4000\n"
        + f"{root},{expiry},495.000,PUT,2026-09-24T06:30:00,6000\n"
    )


NOW = dt.datetime(2026, 9, 24, 15, 5, tzinfo=dt.UTC)  # 11:05 ET


def test_live_snapshot_is_stamped_with_the_newest_quote_not_the_wall_clock():
    snap = build_live_snapshot(
        Underlying.SPY, rows("SPY", live_csv("SPY")), rows("SPY", live_oi_csv("SPY")), now=NOW
    )
    assert snap.captured_at == dt.datetime(2026, 9, 24, 15, 0, 1, 500000, tzinfo=dt.UTC)
    assert snap.delayed_minutes == 0 and snap.source == "thetadata" and snap.spot == 500.0
    call = next(c for c in snap.contracts if c.strike == 505)
    # Columns the live endpoint does not carry are unknown, never zero.
    assert call.last is None and call.volume is None and call.gamma is None
    assert call.open_interest == 4000 and call.iv == 0.18 and call.vega == 0.05


def test_live_snapshot_never_stamps_the_future_and_falls_back_to_now():
    ahead = live_csv("SPY", stamps=("11:30:00.000", "11:31:00.000"))  # terminal clock ahead
    snap = build_live_snapshot(Underlying.SPY, rows("SPY", ahead), [], now=NOW)
    assert snap.captured_at == NOW
    blank = live_csv("SPY").replace("2026-09-24T10:59:58.120", "").replace("2026-09-24T11:00:01.500", "")
    assert build_live_snapshot(Underlying.SPY, rows("SPY", blank), [], now=NOW).captured_at == NOW


def live_transport(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        root = request.url.params["symbol"]
        if request.url.path.endswith("/greeks/first_order"):
            return httpx.Response(200, text=live_csv(root))
        return httpx.Response(200, text=live_oi_csv(root))

    return httpx.MockTransport(handler)


async def test_fetch_expiry_asks_the_terminal_for_that_expiry_only():
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=live_transport(seen)) as http:
        provider = ThetaDataProvider(ThetaDataClient(base_url="http://theta", client=http, backoff_seconds=0))
        snap = await provider.fetch_expiry("spx", dt.date(2026, 9, 24))
    assert {r.url.path for r in seen} == {
        "/v3/option/snapshot/greeks/first_order",
        "/v3/option/snapshot/open_interest",
    }
    assert {r.url.params["expiration"] for r in seen} == {"20260924"}
    assert {r.url.params["symbol"] for r in seen} == {"SPX", "SPXW"}
    assert snap.roots == ("SPX", "SPXW")


async def test_fetch_chain_asks_for_every_expiry():
    seen: list[httpx.Request] = []
    async with httpx.AsyncClient(transport=live_transport(seen)) as http:
        provider = ThetaDataProvider(ThetaDataClient(base_url="http://theta", client=http, backoff_seconds=0))
        await provider.fetch_chain("SPY")
    assert {r.url.params["expiration"] for r in seen} == {"*"}


async def test_unknown_symbol_is_not_supported():
    provider = ThetaDataProvider(ThetaDataClient(base_url="http://theta"))
    with pytest.raises(SymbolNotSupported):
        await provider.fetch_chain("AAPL")
