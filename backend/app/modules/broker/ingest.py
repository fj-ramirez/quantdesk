"""Bars, ticks, specs and the clock check (T144, plans/charter-mt5/README.md).

Called by the `broker-ingest` worker on a one-minute cadence. Everything here is idempotent: a
re-run of any window rewrites the same rows, and a missed poll costs nothing because the next
one starts from what is stored.

The clock gate comes first. Bars and ticks are converted from server time with the New York
close convention (`servertime.py`), and only while a fresh measurement has confirmed the
convention: once in the last :data:`CLOCK_TRUST_DAYS`, with no fresh disagreement since. A
disagreement stops all bar and tick writes until a measurement agrees again. Specs carry no
server timestamps and are not gated.

History depth is discovered, not configured. The first run fetches the last week, and each
later run walks back one chunk at a time from the oldest stored bar. It stops for the day after
:data:`EMPTY_CHUNKS_TO_STOP` empty chunks in a row: the broker has nothing older, or MT5's
"Max bars in chart" limit is in the way (see the plan's first-contact failures). A chunk counts
as empty only after :data:`EMPTY_RETRIES` more tries :data:`EMPTY_RETRY_S` apart, because MT5
answers a range it has not downloaded yet with nothing and fetches it in the background. Two
immediate empties stopped the first live walk at 2026-06-30, though January 2026 was there.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from app.modules.broker import servertime
from app.modules.broker.client import BridgeClient, BridgeError
from app.modules.broker.tables import Bar, ClockCheck, SymbolSpec, TickFile

__all__ = [
    "CHUNK",
    "CLOCK_TRUST_DAYS",
    "ClockGate",
    "Ingestor",
    "symbol_slug",
]

logger = logging.getLogger("app.modules.broker.ingest")

TIMEFRAME = "M1"
BAR = dt.timedelta(minutes=1)
#: One `rates_range` request. A week of M1 is at most about 7,000 bars.
CHUNK = dt.timedelta(days=7)
EMPTY_CHUNKS_TO_STOP = 2
EMPTY_RETRIES = 3
#: About three months of M1 per symbol per minute-run (see `bars_backward`).
BACKWARD_CHUNKS_PER_RUN = 13
#: A UTC day with fewer bars than this in an M1 answer is not minute data. Where the broker
#: has no M1 history, MT5 answers an M1 request with one bar per *day* (S&P.fs before
#: 2019-07-17, found 2026-10-08), and those were being stored as minutes. Real days run 60+
#: even on a Sunday open or a holiday half-session.
MIN_M1_BARS_PER_DAY = 30
EMPTY_RETRY_S = 10.0
#: How long a confirmed convention is trusted without a fresh measurement: a long weekend plus
#: a holiday, with margin.
CLOCK_TRUST_DAYS = 8
#: Write a clock check at least this often while measurements are possible.
CLOCK_RECORD_EVERY = dt.timedelta(hours=1)


def symbol_slug(symbol: str) -> str:
    """A filesystem-safe name: `S&P.fs` -> `s-p.fs`."""
    return re.sub(r"[^a-z0-9._]+", "-", symbol.lower()).strip("-")


def _insert(session: Session):
    return pg_insert if session.bind.dialect.name == "postgresql" else sqlite_insert


def server_epochs_to_utc(server_s: pd.Series) -> pd.Series:
    """Vectorised `servertime.server_epoch_to_utc`. An ambiguous or skipped New York wall time
    (01:00–03:00 on a DST Sunday, when nothing trades) becomes `NaT` and is dropped by callers."""
    wall = pd.to_datetime(server_s, unit="s") - servertime.SERVER_MINUS_NY
    return wall.dt.tz_localize(servertime.NY, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


@dataclass
class ClockGate:
    """Whether server timestamps may be converted with the convention right now."""

    last_tick_ms: dict[str, int] = field(default_factory=dict)
    last_recorded: dt.datetime | None = None
    last_verdict: bool | None = None

    def allowed(self, session: Session, now: dt.datetime) -> bool:
        latest = session.execute(
            select(ClockCheck).where(ClockCheck.agrees.is_not(None))
            .order_by(ClockCheck.checked_at.desc()).limit(1)
        ).scalar_one_or_none()
        return (
            latest is not None
            and latest.agrees is True
            and now - latest.checked_at <= dt.timedelta(days=CLOCK_TRUST_DAYS)
        )

    async def check(self, client: BridgeClient, symbols: list[str], session: Session,
                    now: dt.datetime) -> None:
        """Measure from every symbol whose tick changed since the previous poll."""
        verdict: bool | None = None
        measured: int | None = None
        source: str | None = None
        model = servertime.model_offset(now)
        for symbol in symbols:
            try:
                reply = await client.tick(symbol)
            except BridgeError as e:
                logger.warning("broker: tick %s failed: %s", symbol, e)
                continue
            tick_ms = int(reply.result["time_msc"])
            previous = self.last_tick_ms.get(symbol)
            self.last_tick_ms[symbol] = tick_ms
            if previous is None or tick_ms == previous:
                continue  # first sight, or stale: not a fresh reading
            m = servertime.measure_offset(tick_ms, reply.utc_ms)
            if m is None:
                continue
            measured, source = m, symbol
            verdict = m == model
            if not verdict:
                break  # one fresh disagreement is enough to stop
        if verdict is None:
            return
        changed = verdict != self.last_verdict
        due = self.last_recorded is None or now - self.last_recorded >= CLOCK_RECORD_EVERY
        if changed or due:
            session.add(ClockCheck(checked_at=now, measured_s=measured, model_s=model,
                                   agrees=verdict, symbol=source))
            session.commit()
            self.last_recorded = now
            log = logger.info if verdict else logger.error
            log("broker: clock %s: measured %s s on %s, convention says %s s",
                "agrees" if verdict else "DISAGREES", measured, source, model)
        self.last_verdict = verdict


@dataclass
class Ingestor:
    client: BridgeClient
    factory: sessionmaker[Session]
    symbols: list[str]
    data_dir: Path
    tick_hours: int = 168
    empty_retry_s: float = EMPTY_RETRY_S
    gate: ClockGate = field(default_factory=ClockGate)
    _deepened_on: dict[str, dt.date] = field(default_factory=dict)

    # ------------------------------------------------------------------ the cycle

    async def run_minute(self, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.UTC)
        with self.factory() as session:
            await self.gate.check(self.client, self.symbols, session, now)
            if not self.gate.allowed(session, now):
                logger.info("broker: clock not confirmed yet; no bars or ticks written")
                return
        for symbol in self.symbols:
            try:
                await self.bars_forward(symbol, now)
                if (self._deepened_on.get(symbol) != now.date()
                        and await self.bars_backward(symbol, now)):
                    self._deepened_on[symbol] = now.date()
            except BridgeError as e:
                logger.warning("broker: bars %s failed: %s", symbol, e)

    async def run_hour(self, now: dt.datetime | None = None) -> None:
        now = now or dt.datetime.now(dt.UTC)
        for symbol in self.symbols:
            try:
                await self.spec(symbol, now)
            except BridgeError as e:
                logger.warning("broker: spec %s failed: %s", symbol, e)
        with self.factory() as session:
            if not self.gate.allowed(session, now):
                return
        for symbol in self.symbols:
            try:
                await self.ticks(symbol, now)
            except BridgeError as e:
                logger.warning("broker: ticks %s failed: %s", symbol, e)
            except OSError:
                # A tick file that cannot be written must not take the bars down with it: on
                # 2026-10-08 an unwritable /data/broker crash-looped the whole worker, and the
                # history walk with it. Logged loudly; the hour is retried next run.
                logger.exception("broker: ticks %s could not be written", symbol)

    # ------------------------------------------------------------------ bars

    def _bounds(self, symbol: str) -> tuple[dt.datetime | None, dt.datetime | None]:
        with self.factory() as session:
            return session.execute(
                select(func.min(Bar.ts), func.max(Bar.ts))
                .where(Bar.symbol == symbol, Bar.timeframe == TIMEFRAME)
            ).one()

    async def _fetch(self, symbol: str, start: dt.datetime, end: dt.datetime,
                     now: dt.datetime) -> pd.DataFrame:
        df = await self.client.rates(symbol, TIMEFRAME, servertime.utc_to_server_epoch(start),
                                     servertime.utc_to_server_epoch(end))
        if df.empty:
            return df
        df["ts"] = server_epochs_to_utc(df["server_time"])
        bad = int(df["ts"].isna().sum())
        if bad:
            logger.warning("broker: %s: %d bar(s) at an ambiguous DST wall time dropped", symbol, bad)
        df = df.dropna(subset=["ts"])
        df = df[df["ts"] + BAR <= now]  # never store the forming bar (decision 4)
        # Unit-independent on purpose: pandas may hold `ts` at second or nanosecond resolution.
        epoch = (df["ts"] - pd.Timestamp(0, tz="UTC")).dt.total_seconds().round().astype("int64")
        df["offset_s"] = df["server_time"] - epoch
        return df

    def _upsert(self, symbol: str, df: pd.DataFrame, now: dt.datetime) -> tuple[int, int]:
        """Returns `(new, changed)`. A closed bar that comes back different is updated and
        logged."""
        if df.empty:
            return 0, 0
        rows = [
            {"symbol": symbol, "timeframe": TIMEFRAME, "ts": ts.to_pydatetime(),
             "open": float(o), "high": float(h), "low": float(lo), "close": float(c),
             "tick_volume": int(tv), "spread": int(sp), "real_volume": int(rv),
             "offset_s": int(off), "ingested_at": now}
            for ts, o, h, lo, c, tv, sp, rv, off in df[
                ["ts", "open", "high", "low", "close", "tick_volume", "spread", "real_volume",
                 "offset_s"]
            ].itertuples(index=False)
        ]
        with self.factory() as session:
            stamps = [r["ts"] for r in rows]
            existing = {
                ts: (o, h, lo, c)
                for ts, o, h, lo, c in session.execute(
                    select(Bar.ts, Bar.open, Bar.high, Bar.low, Bar.close).where(
                        Bar.symbol == symbol, Bar.timeframe == TIMEFRAME,
                        Bar.ts >= min(stamps), Bar.ts <= max(stamps),
                    )
                )
            }
            changed = [
                r["ts"] for r in rows
                if r["ts"] in existing
                and existing[r["ts"]] != (r["open"], r["high"], r["low"], r["close"])
            ]
            if changed:
                logger.warning("broker: %s: %d closed bar(s) changed, first %s",
                               symbol, len(changed), changed[0].isoformat())
            ins = _insert(session)
            for i in range(0, len(rows), 2000):
                stmt = ins(Bar).values(rows[i : i + 2000])
                stmt = stmt.on_conflict_do_update(
                    index_elements=["symbol", "timeframe", "ts"],
                    set_={c: getattr(stmt.excluded, c) for c in (
                        "open", "high", "low", "close", "tick_volume", "spread", "real_volume",
                        "offset_s", "ingested_at")},
                )
                session.execute(stmt)
            session.commit()
        return len([r for r in rows if r["ts"] not in existing]), len(changed)

    async def bars_forward(self, symbol: str, now: dt.datetime) -> int:
        """From the newest stored bar (re-reading it) to now. A first run takes the last week."""
        _, last = self._bounds(symbol)
        start = last if last is not None else now - CHUNK
        total = 0
        while start < now:
            end = min(start + CHUNK, now)
            new, _ = self._upsert(symbol, await self._fetch(symbol, start, end, now), now)
            total += new
            start = end
        if total:
            logger.info("broker: %s: %d new bar(s)", symbol, total)
        return total

    async def bars_backward(self, symbol: str, now: dt.datetime,
                            max_chunks: int = BACKWARD_CHUNKS_PER_RUN) -> bool:
        """Walk back from the oldest stored bar, at most `max_chunks` chunks per call.

        True once history is exhausted (two empty chunks in a row), so the caller stops asking
        for today. The budget keeps one symbol's years of history from holding the minute job:
        an unbounded walk froze the other four symbols' live bars for over 1.5 hours on
        2026-10-08. A call that ends on an empty chunk is safe, because the next one restarts
        from the oldest stored bar and re-tries it.
        """
        first, _ = self._bounds(symbol)
        if first is None:
            return False
        end, empty, total = first, 0, 0
        # Never below the stop rule: an end needs that many empty chunks inside one call.
        for _ in range(max(max_chunks, EMPTY_CHUNKS_TO_STOP)):
            start = end - CHUNK
            df = await self._fetch(symbol, start, end, now)
            for _ in range(EMPTY_RETRIES):
                if not df.empty:
                    break
                await asyncio.sleep(self.empty_retry_s)
                df = await self._fetch(symbol, start, end, now)
            if not df.empty:
                df, coarse = _drop_coarse_days(df, keep_day=end.date())
                if coarse:
                    self._upsert(symbol, df, now)
                    oldest, _ = self._bounds(symbol)
                    logger.info("broker: %s: M1 history starts %s; the broker serves coarser "
                                "bars before it, not stored", symbol,
                                oldest.isoformat() if oldest else None)
                    return True
            new, _ = self._upsert(symbol, df, now)
            total += new
            empty = 0 if new else empty + 1
            end = start
            if empty >= EMPTY_CHUNKS_TO_STOP:
                oldest, _ = self._bounds(symbol)
                logger.info("broker: %s: history starts %s",
                            symbol, oldest.isoformat() if oldest else None)
                return True
        if total:
            logger.info("broker: %s: back to %s (%d older bars)", symbol, end.date(), total)
        return False

    # ------------------------------------------------------------------ ticks

    async def ticks(self, symbol: str, now: dt.datetime) -> int:
        """Each closed UTC hour in the last `tick_hours` not yet in `tick_files`."""
        last_closed = now.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
        hours = [last_closed - dt.timedelta(hours=h) for h in range(self.tick_hours)]
        with self.factory() as session:
            done = set(session.execute(
                select(TickFile.hour).where(TickFile.symbol == symbol, TickFile.hour >= hours[-1])
            ).scalars())
        written = 0
        for hour in sorted(set(hours) - done):
            s_srv = servertime.utc_to_server_epoch(hour)
            e_srv = servertime.utc_to_server_epoch(hour + dt.timedelta(hours=1))
            offset = s_srv - int(hour.timestamp())
            df = await self.client.ticks(symbol, s_srv, e_srv)
            rel = None
            if not df.empty:
                df["ts"] = server_epochs_to_utc(df["time_msc"] // 1000) + pd.to_timedelta(
                    df["time_msc"] % 1000, unit="ms")
                df = df.dropna(subset=["ts"]).rename(columns={"time_msc": "server_ms"})
                rel = Path("broker", "ticks", symbol_slug(symbol), hour.strftime("%Y-%m-%d"),
                           f"{hour:%H}.parquet")
                out = self.data_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                df[["ts", "bid", "ask", "last", "flags", "server_ms"]].to_parquet(out, index=False)
            with self.factory() as session:
                session.merge(TickFile(symbol=symbol, hour=hour, path=rel.as_posix() if rel else None,
                                       rows=len(df), offset_s=offset, written_at=now))
                session.commit()
            written += len(df)
        if written:
            logger.info("broker: %s: %d tick(s) written", symbol, written)
        return written

    # ------------------------------------------------------------------ specs

    async def spec(self, symbol: str, now: dt.datetime) -> bool:
        """Store the spec if it differs from the latest stored one. True when a row was added."""
        spec = (await self.client.spec(symbol)).result
        with self.factory() as session:
            latest = session.execute(
                select(SymbolSpec.spec).where(SymbolSpec.symbol == symbol)
                .order_by(SymbolSpec.observed_at.desc()).limit(1)
            ).scalar_one_or_none()
            if latest is not None and _digest(latest) == _digest(spec):
                return False
            session.add(SymbolSpec(symbol=symbol, observed_at=now, spec=spec))
            session.commit()
        logger.info("broker: %s: spec %s", symbol, "changed" if latest is not None else "recorded")
        return True


def _drop_coarse_days(df: pd.DataFrame, *, keep_day: dt.date) -> tuple[pd.DataFrame, bool]:
    """Drop UTC days too sparse to be M1 (see :data:`MIN_M1_BARS_PER_DAY`). `keep_day` is the
    day the chunk ends in, cut short by the oldest stored bar, so it is never judged. Returns
    the kept rows and whether anything was dropped."""
    days = df["ts"].dt.date
    counts = days.map(days.value_counts())
    coarse = (counts < MIN_M1_BARS_PER_DAY) & (days != keep_day)
    if not coarse.any():
        return df, False
    # The first day of real minutes can also carry that day's coarse bar, and enough minutes
    # to pass the count. It is dropped whole: at most one partial day at the start of history,
    # against a day-sized bar stored as a minute.
    kept = days[~coarse]
    if not kept.empty and kept.min() != keep_day:
        coarse |= days == kept.min()
    return df[~coarse], True


def _digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()
