"""The executor: the one part of the desk that places orders (T151, plans/charter-mt5 step 4).

It trades the account the `mt5` container is logged into, whatever its mode, but only:

* strategies listed in `app.modules.broker.strategies.STRATEGIES` and enabled in
  `EXECUTOR_STRATEGIES`. Each one is a candidate's frozen spec;
* with `EXECUTOR_ENABLED=true`. Otherwise it logs what it would do and sends nothing;
* with a stop-loss on every order (the bridge refuses one without);
* at `EXECUTOR_VOLUME`, refused outright above the hard cap `EXECUTOR_MAX_VOLUME`;
* with one position per strategy. An open that finds one already there is skipped;
* while the strategy is not paused. A tripped kill rule pauses it, and only a person resumes it.

A strategy that decides from prices (`nas_mr`) schedules one `decide` leg per session. When it
is due, the executor reads the strategy's M1 bars from `broker.bars`, asks `signal`, journals the
answer on the `decide` row, and writes and sends the open or close it calls for. A close pairs
with the strategy's latest filled open, which may be days old. On a `.fs` CFD the round trip's
`pnl_pct` removes every roll step `broker.rolls` had confirmed by the close, as the backtests do.

Every leg is written to `broker.order_intents` **before** it is sent. The fill, the quote around
it and any error are recorded on the same row. A restart therefore picks up the pending rows,
and the table is the trade journal. A close that fails stays pending and is retried every tick,
because leaving the position open is the worse outcome.

A tick that completes overwrites `broker.executor_heartbeat` (T174). `capture-watch` alerts when
that row goes stale while a leg is due: the executor cannot report its own absence.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.broker.client import BridgeClient, BridgeError
from app.modules.broker.strategies import NY, ClosedTrade, rth_daily
from app.modules.broker.tables import Bar, ExecutorHeartbeat, OrderIntent, RollRow, StrategyState

log = logging.getLogger(__name__)


class Executor:
    def __init__(
        self,
        client: BridgeClient,
        factory: sessionmaker[Session],
        strategies: list,
        *,
        enabled: bool,
        volume: float,
        max_volume: float,
        notify: Callable[[str], object] = lambda msg: None,
    ) -> None:
        self.client, self.factory, self.strategies = client, factory, strategies
        self.enabled, self.volume, self.max_volume = enabled, volume, max_volume
        self.notify = notify
        self.started_at: dt.datetime | None = None
        if volume > max_volume:
            raise ValueError(f"EXECUTOR_VOLUME {volume} exceeds the hard cap {max_volume}")

    # ---------------------------------------------------------------- scheduling

    def schedule(self, now: dt.datetime) -> int:
        """Write the legs for yesterday's and today's New York session. Idempotent."""
        today = now.astimezone(NY).date()
        written = 0
        with self.factory() as s:
            for strat in self.strategies:
                for day in (today - dt.timedelta(days=1), today):
                    for leg in strat.legs(day):
                        if s.scalar(select(OrderIntent.id).where(OrderIntent.key == leg.key)):
                            continue
                        s.add(OrderIntent(
                            key=leg.key, strategy=strat.name, symbol=strat.symbol,
                            action=leg.action, side=strat.side, volume=self.volume,
                            due_at=leg.due_at, expires_at=leg.expires_at, status="pending",
                            attempts=0, created_at=now))
                        try:
                            s.commit()
                            written += 1
                        except IntegrityError:  # another process wrote it first
                            s.rollback()
        return written

    # ---------------------------------------------------------------- the tick

    async def tick(self, now: dt.datetime) -> None:
        self.schedule(now)
        with self.factory() as s:
            due = s.scalars(
                select(OrderIntent)
                .where(OrderIntent.status == "pending", OrderIntent.due_at <= now)
                .order_by(OrderIntent.due_at)
            ).all()
            for intent in due:
                strat = self._strategy(intent.strategy)
                if strat is None:
                    self._finish(s, intent, "skipped", f"strategy {intent.strategy} not enabled")
                    continue
                if intent.action == "decide":
                    await self._decide(s, strat, intent, now)
                elif intent.action == "open":
                    await self._open(s, strat, intent, now)
                else:
                    await self._close(s, strat, intent, now)
        self.heartbeat(now)

    def heartbeat(self, now: dt.datetime) -> None:
        """Overwrite this process's liveness row. Last in the tick, so a tick that raises does
        not beat, and a database or bridge failure that keeps recurring reads as an outage."""
        self.started_at = self.started_at or now
        with self.factory() as s:
            s.merge(ExecutorHeartbeat(
                service="executor", beat_at=now, started_at=self.started_at,
                enabled=self.enabled, strategies=",".join(x.name for x in self.strategies)))
            s.commit()

    def _strategy(self, name: str):
        return next((x for x in self.strategies if x.name == name), None)

    def _finish(self, s: Session, intent: OrderIntent, status: str, note: str | None) -> None:
        intent.status, intent.note = status, (note or "")[:512] or None
        s.commit()
        log.info("executor: %s %s (%s)", intent.key, status, note or "")

    def paused(self, s: Session, strategy: str) -> str | None:
        st = s.get(StrategyState, strategy)
        if st is None or not st.paused:
            return None
        return st.reason or "paused"

    # ---------------------------------------------------------------- decisions

    def daily(self, s: Session, strat, now: dt.datetime) -> pd.DataFrame:
        """The strategy's sessions from `broker.bars` M1, up to `now`."""
        rows = s.execute(select(Bar.ts, Bar.open, Bar.high, Bar.low).where(
            Bar.symbol == strat.symbol, Bar.timeframe == "M1",
            Bar.ts >= now - strat.history, Bar.ts <= now,
        ).order_by(Bar.ts)).all()
        m1 = pd.DataFrame(rows, columns=["ts", "open", "high", "low"])
        m1.index = pd.DatetimeIndex(pd.to_datetime(m1.pop("ts"), utc=True))
        return rth_daily(m1)

    def holding(self, s: Session, strat) -> bool:
        """Whether the journal has the strategy long: a filled open with no close written
        after it. A position a disaster stop has taken still counts until the rule's own exit,
        so the rule never re-enters early; that close then finds nothing and says so."""
        opened = self._last_open(s, strat.name)
        if opened is None:
            return False
        return s.scalar(select(OrderIntent.id).where(
            OrderIntent.strategy == strat.name, OrderIntent.action == "close",
            OrderIntent.due_at > opened.due_at)) is None

    async def _decide(self, s: Session, strat, intent: OrderIntent, now: dt.datetime) -> None:
        day = intent.due_at.astimezone(NY).date()

        def bar_in(lo: dt.timedelta, hi: dt.timedelta) -> bool:
            return s.scalar(select(Bar.ts).where(
                Bar.symbol == strat.symbol, Bar.timeframe == "M1",
                Bar.ts >= intent.due_at + lo, Bar.ts < intent.due_at + hi).limit(1)) is not None

        if now > intent.expires_at:
            if not bar_in(-dt.timedelta(minutes=30), strat.decide_window):
                # The market was shut around 16:00 (a holiday or an early close): the backtest
                # has no session on such a day either.
                self._finish(s, intent, "skipped", "no 16:00 session (holiday or early close)")
                return
            held = self.holding(s, strat)
            self._finish(s, intent, "expired", "decision window passed before it could be made")
            self.notify(f"{strat.name}: no decision on {day}: the window passed (executor down, "
                        "or the 16:00 bar late)" + ("; the position stays open" if held else ""))
            return
        # Cheap first: until the 16:00 bar is in, there is nothing to decide on.
        daily = (self.daily(s, strat, now) if bar_in(dt.timedelta(0), strat.decide_window)
                 else None)
        if daily is None or daily.empty or daily.index[-1] != day:
            intent.note = "waiting for the 16:00 bar"
            s.commit()
            return
        action, why = strat.signal(daily, self.holding(s, strat))
        if action is None:
            self._finish(s, intent, "decided", f"no trade: {why}")
            return
        window = strat.entry_window if action == "open" else strat.exit_window
        leg = OrderIntent(
            key=intent.key.removesuffix(":decide") + f":{action}", strategy=strat.name,
            symbol=strat.symbol, action=action, side=strat.side, volume=self.volume,
            due_at=now, expires_at=now + window, status="pending", attempts=0, created_at=now)
        s.add(leg)
        self._finish(s, intent, "decided", f"{action}: {why}")
        if action == "open":
            await self._open(s, strat, leg, now)
        else:
            await self._close(s, strat, leg, now)

    # ---------------------------------------------------------------- legs

    async def _open(self, s: Session, strat, intent: OrderIntent, now: dt.datetime) -> None:
        if now > intent.expires_at:
            self._finish(s, intent, "expired", "entry window passed before it could be sent")
            return
        reason = self.paused(s, strat.name)
        if reason:
            self._finish(s, intent, "skipped", f"paused: {reason}")
            return
        if intent.volume > self.max_volume:
            self._finish(s, intent, "rejected", f"volume {intent.volume} > cap {self.max_volume}")
            return
        if not self.enabled:
            self._finish(s, intent, "skipped", "EXECUTOR_ENABLED is false (dry run)")
            return
        intent.attempts += 1
        intent.sent_at = now
        try:
            if await self.client.positions(symbol=strat.symbol, magic=strat.magic):
                self._finish(s, intent, "skipped", "a position for this strategy is already open")
                return
            account = await self.client.account()
            quote = (await self.client.tick(strat.symbol)).result
            ref = quote["ask"] if strat.side == "buy" else quote["bid"]
            intent.sl = strat.stop_for(ref)
            r = (await self.client.order_market(
                strat.symbol, strat.side, intent.volume, intent.sl, magic=strat.magic,
                comment=intent.key[-28:])).result
        except BridgeError as e:
            self._finish(s, intent, "rejected", str(e))
            self.notify(f"{strat.name}: open REJECTED: {e}")
            return
        intent.ticket, intent.fill_price = r["order"], r["price"]
        intent.quote_bid, intent.quote_ask = r["quote_bid"], r["quote_ask"]
        intent.account_login, intent.account_mode = account.login, account.trade_mode
        self._finish(s, intent, "filled", None)
        self.notify(f"{strat.name}: bought {intent.volume} {strat.symbol} @ {r['price']} "
                    f"(spread {_spread(intent)}, stop {intent.sl}, {account.trade_mode})")

    async def _close(self, s: Session, strat, intent: OrderIntent, now: dt.datetime) -> None:
        opened = self._opening(s, intent)
        try:
            positions = await self.client.positions(symbol=strat.symbol, magic=strat.magic)
        except BridgeError as e:
            self._retry(s, strat, intent, now, f"positions: {e}")
            return
        if not positions:
            why = ("no entry was filled" if opened is None or opened.status != "filled"
                   else "position gone before the exit (disaster stop or a manual close)")
            self._finish(s, intent, "skipped", why)
            if opened is not None and opened.status == "filled":
                self.notify(f"{strat.name}: {why}; check the account")
            return
        if not self.enabled:
            self._retry(s, strat, intent, now, "EXECUTOR_ENABLED is false; position left open")
            return
        intent.attempts += 1
        intent.sent_at = now
        try:
            fills = [(await self.client.close_position(p["ticket"], comment=intent.key[-28:])).result
                     for p in positions]
        except BridgeError as e:
            self._retry(s, strat, intent, now, str(e))
            return
        r = fills[-1]
        intent.ticket, intent.fill_price = positions[-1]["ticket"], r["price"]
        intent.quote_bid, intent.quote_ask = r["quote_bid"], r["quote_ask"]
        note = None
        if opened is not None and opened.fill_price:
            sign = 1 if strat.side == "buy" else -1
            steps = self._roll_steps(s, strat.symbol, opened.sent_at or opened.due_at, now)
            intent.pnl_pct = sign * ((r["price"] - opened.fill_price) / opened.fill_price
                                     - sum(steps)) * 100
            if steps:
                note = f"roll steps removed: {', '.join(f'{x * 100:+.2f}%' for x in steps)}"
        self._finish(s, intent, "filled", note)
        self.notify(f"{strat.name}: closed @ {r['price']}, "
                    f"{'n/a' if intent.pnl_pct is None else f'{intent.pnl_pct:+.3f}%'}")
        self._check_kill(s, strat, now)

    def _last_open(self, s: Session, strategy: str, before: dt.datetime | None = None):
        q = select(OrderIntent).where(OrderIntent.strategy == strategy,
                                      OrderIntent.action == "open", OrderIntent.status == "filled")
        if before is not None:
            q = q.where(OrderIntent.due_at < before)
        return s.scalar(q.order_by(OrderIntent.due_at.desc()).limit(1))

    def _opening(self, s: Session, close: OrderIntent) -> OrderIntent | None:
        """The open a close belongs to: the same session's open for a fixed-time strategy,
        else the strategy's latest filled open before the close."""
        same = s.scalar(select(OrderIntent).where(
            OrderIntent.key == close.key.removesuffix(":close") + ":open"))
        return same if same is not None else self._last_open(s, close.strategy, close.due_at)

    def _roll_steps(self, s: Session, symbol: str, since: dt.datetime,
                    now: dt.datetime) -> list[float]:
        """Roll steps in `symbol` between the open and `now`, as far as was confirmed by now."""
        if not symbol.endswith(".fs"):
            return []
        return list(s.scalars(select(RollRow.step).where(
            RollRow.cfd_symbol == symbol, RollRow.rolled_at > since, RollRow.rolled_at <= now,
            RollRow.confirmed_at <= now).order_by(RollRow.rolled_at)))

    def _retry(self, s: Session, strat, intent: OrderIntent, now: dt.datetime, why: str) -> None:
        intent.note = why[:512]
        s.commit()
        log.warning("executor: %s close not done: %s", intent.key, why)
        if now > intent.expires_at and intent.attempts % 10 == 0:
            self.notify(f"{strat.name}: EXIT OVERDUE since {intent.due_at:%H:%M} UTC: {why}")

    # ---------------------------------------------------------------- kill rules

    def closed_trades(self, s: Session, strategy: str) -> list[ClosedTrade]:
        rows = s.scalars(select(OrderIntent).where(
            OrderIntent.strategy == strategy, OrderIntent.action == "close",
            OrderIntent.status == "filled", OrderIntent.pnl_pct.is_not(None),
        ).order_by(OrderIntent.due_at)).all()
        out = []
        for c in rows:
            o = self._opening(s, c)
            out.append(ClosedTrade(c.pnl_pct, _spread(o) if o is not None else None))
        return out

    def _check_kill(self, s: Session, strat, now: dt.datetime) -> None:
        reason = strat.kill(self.closed_trades(s, strat.name))
        if reason is None:
            return
        st = s.get(StrategyState, strat.name) or StrategyState(strategy=strat.name)
        st.paused, st.reason, st.updated_at = True, f"kill rule: {reason}", now
        s.merge(st)
        s.commit()
        self.notify(f"{strat.name}: PAUSED by kill rule: {reason}. Back to demo; "
                    "resume only on a fresh sample.")

    # ---------------------------------------------------------------- boot

    async def reconcile(self, now: dt.datetime) -> list[str]:
        """Report positions carrying a strategy's magic number that no pending close covers.
        Reports only: the pending close rows (or a person) act on them."""
        problems = []
        with self.factory() as s:
            for strat in self.strategies:
                try:
                    open_ = await self.client.positions(symbol=strat.symbol, magic=strat.magic)
                except BridgeError as e:
                    problems.append(f"{strat.name}: cannot list positions: {e}")
                    continue
                pending_close = s.scalar(select(OrderIntent.id).where(
                    OrderIntent.strategy == strat.name, OrderIntent.action == "close",
                    OrderIntent.status == "pending"))
                # A decide-leg strategy holds across sessions: its next decision covers it.
                covered = pending_close is not None or (
                    hasattr(strat, "signal") and self.holding(s, strat))
                if open_ and not covered:
                    problems.append(f"{strat.name}: {len(open_)} open position(s) with no "
                                    "pending close")
        for p in problems:
            self.notify(f"executor boot: {p}")
        return problems


def _spread(intent: OrderIntent) -> float | None:
    if intent.quote_ask is None or intent.quote_bid is None:
        return None
    return round(intent.quote_ask - intent.quote_bid, 5)
