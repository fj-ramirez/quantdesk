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
answer on the `decide` row, and writes and sends the open or close it calls for.

A 4h strategy (`nas_4h_atr3`, T180) schedules a `decide` leg at the end of every 4h bin and
decides it when the first M1 bar after the bin is in `broker.bars`, since that bar's open is the
backtest's fill. Its stop comes from the signal candle (`initial_stop`) and is trailed with a
`modify` leg: written before it is sent like every other leg, sent through the bridge's
raise-only `modify_sl`. A position the broker's stop has closed is journalled as a close at the
exit price `deals` reports. Every entry signal goes to Telegram, in a dry run too. A close pairs
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
from app.modules.broker.strategies import NY, ClosedTrade, Held, rth_daily
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
                if intent.action == "decide" and hasattr(strat, "h4_bars"):
                    await self._decide_h4(s, strat, intent, now)
                elif intent.action == "decide":
                    await self._decide(s, strat, intent, now)
                elif intent.action == "modify":
                    # Only a crash between writing and sending leaves one pending. The next 4h
                    # decision recomputes the trail, so this one is never sent late.
                    self._finish(s, intent, "expired", "not sent; the next decision recomputes it")
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


    # ---------------------------------------------------------------- 4h decisions (T180)

    def h4(self, s: Session, strat, until: dt.datetime) -> pd.DataFrame:
        """The strategy's 4h bins from `broker.bars` M1 before `until`."""
        rows = s.execute(select(Bar.ts, Bar.open, Bar.high, Bar.low, Bar.close).where(
            Bar.symbol == strat.symbol, Bar.timeframe == "M1",
            Bar.ts >= until - strat.history, Bar.ts < until,
        ).order_by(Bar.ts)).all()
        m1 = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"])
        m1.index = pd.DatetimeIndex(pd.to_datetime(m1.pop("ts"), utc=True))
        return strat.h4_bars(m1)

    def _stop_now(self, s: Session, strat, opened: OrderIntent) -> float | None:
        """The position's stop as journalled: the latest filled `modify` after the open."""
        last = s.scalar(select(OrderIntent.sl).where(
            OrderIntent.strategy == strat.name, OrderIntent.action == "modify",
            OrderIntent.status == "filled", OrderIntent.due_at > opened.due_at,
        ).order_by(OrderIntent.due_at.desc()).limit(1))
        return last if last is not None else opened.sl

    async def _held(self, s: Session, strat, now: dt.datetime):
        """(the open row, `Held`, the position ticket) for a strategy the journal has long, after
        journalling a broker stop-out if the position is gone; (None, None, None) when flat.
        Raises BridgeError when the bridge cannot say."""
        if not self.holding(s, strat):
            return None, None, None
        opened = self._last_open(s, strat.name)
        positions = await self.client.positions(symbol=strat.symbol, magic=strat.magic)
        if not positions:
            await self._stopped(s, strat, opened, now)
            return None, None, None
        stop = self._stop_now(s, strat, opened)
        return opened, Held(opened.sent_at or opened.due_at, opened.fill_price, stop), \
            positions[-1]["ticket"]

    async def _stopped(self, s: Session, strat, opened: OrderIntent, now: dt.datetime) -> None:
        """Journal the close the broker's stop made, at the price its deals report."""
        price, note = None, "closed by the broker's stop"
        try:
            outs = [d for d in await self.client.deals(opened.ticket) if d["entry"] == 1]
            price = outs[-1]["price"] if outs else None
        except BridgeError as e:
            note += f"; deals unavailable: {e}"
        if price is None:
            price = self._stop_now(s, strat, opened)
            note += "; exit price taken as the journalled stop"
        steps = self._roll_steps(s, strat.symbol, opened.sent_at or opened.due_at, now)
        pnl = ((price - opened.fill_price) / opened.fill_price - sum(steps)) * 100
        s.add(OrderIntent(
            key=opened.key.removesuffix(":open") + ":stopped", strategy=strat.name,
            symbol=strat.symbol, action="close", side=strat.side, volume=opened.volume,
            due_at=now, expires_at=now, status="filled", attempts=0, created_at=now,
            ticket=opened.ticket, fill_price=price, pnl_pct=pnl, note=note[:512]))
        s.commit()
        self.notify(f"{strat.name}: stopped out @ {price} ({pnl:+.3f}%)")
        self._check_kill(s, strat, now)

    async def _decide_h4(self, s: Session, strat, intent: OrderIntent, now: dt.datetime) -> None:
        end = intent.due_at

        def first_bar(lo: dt.datetime, hi: dt.datetime | None = None):
            q = select(Bar.ts).where(Bar.symbol == strat.symbol, Bar.timeframe == "M1",
                                     Bar.ts >= lo)
            if hi is not None:
                q = q.where(Bar.ts < hi)
            return s.scalar(q.order_by(Bar.ts).limit(1))

        nxt = first_bar(end)
        if nxt is None:
            if now > intent.expires_at:
                self._finish(s, intent, "skipped", "no bar after the bin")
            else:
                intent.note = "waiting for the first bar after the bin"
                s.commit()
            return
        if first_bar(end - dt.timedelta(hours=4), end) is None:
            self._finish(s, intent, "skipped", "no bars in the bin (market shut)")
            return
        nxt = nxt if nxt.tzinfo else nxt.replace(tzinfo=dt.UTC)
        if now - nxt > strat.act_within:
            self._finish(s, intent, "expired", f"decided too late: the next bar opened {nxt:%H:%M} UTC")
            self.notify(f"{strat.name}: the {end.astimezone(NY):%a %H:%M} NY bin was not decided "
                        "in time (executor down, or bars late)")
            return
        h4 = self.h4(s, strat, end)
        if h4.empty or h4.index[-1] != strat.bin_of(end - dt.timedelta(minutes=1)):
            self._finish(s, intent, "skipped", "the bin has no bars in broker.bars")
            return
        try:
            _opened, held, ticket = await self._held(s, strat, now)
        except BridgeError as e:
            intent.note = f"positions: {e}"[:512]
            s.commit()
            return
        action, why, px = strat.signal(h4, held)
        if action is None:
            self._finish(s, intent, "decided", f"no trade: {why}")
            return
        base = intent.key.removesuffix(":decide")
        if action == "trail":
            await self._modify(s, strat, intent, base, ticket, px, why, now)
            return
        window = strat.entry_window if action == "open" else strat.exit_window
        leg = OrderIntent(
            key=f"{base}:{action}", strategy=strat.name, symbol=strat.symbol, action=action,
            side=strat.side, volume=self.volume, due_at=now, expires_at=now + window,
            status="pending", attempts=0, created_at=now,
            # Until it is sent, an open's `sl` holds the signal candle's low: the stop's anchor.
            sl=px if action == "open" else None)
        s.add(leg)
        self._finish(s, intent, "decided", f"{action}: {why}")
        if action == "open":
            self._entry_alert(strat, h4, px)
            await self._open(s, strat, leg, now)
        else:
            await self._close(s, strat, leg, now)

    def _entry_alert(self, strat, h4: pd.DataFrame, anchor: float) -> None:
        """Telegram on every entry signal. In a dry run nothing is held, so the condition stays
        true bin after bin; there only a fresh signal (the previous bin was off) is sent."""
        last = h4.iloc[-1]
        if not self.enabled and len(h4) > 1 and strat.entry_on(h4.iloc[-2]):
            return
        risk = last.close - anchor
        mode = "" if self.enabled else " (dry run: no order)"
        self.notify(
            f"{strat.name}: ENTRY signal{mode} on the {h4.index[-1]:%a %H:%M} NY "
            f"4h bin: buy {strat.symbol} at the next open, ~{last.close:.2f}; stop {anchor:.2f} "
            f"less the spread (risk ~{risk:.1f} pts, ~${risk * strat.usd_per_point:.2f} at 0.01 "
            f"lot). Close {last.close:.2f} > EMA20 {last.ema20:.2f} > EMA50 {last.ema50:.2f}, "
            f"MACD {last.macd:+.2f}; trail = 4h high since entry - {strat.atr_mult:g} x ATR "
            f"{last.atr14:.2f}")

    async def _modify(self, s: Session, strat, decide: OrderIntent, base: str, ticket: int,
                      sl: float, why: str, now: dt.datetime) -> None:
        leg = OrderIntent(
            key=f"{base}:modify", strategy=strat.name, symbol=strat.symbol, action="modify",
            side=strat.side, volume=self.volume, due_at=now, expires_at=now + strat.act_within,
            status="pending", attempts=0, created_at=now, sl=sl, ticket=ticket)
        s.add(leg)
        self._finish(s, decide, "decided", f"trail: {why}")
        if not self.enabled:
            self._finish(s, leg, "skipped", "EXECUTOR_ENABLED is false (dry run)")
            return
        leg.attempts, leg.sent_at = 1, now
        try:
            await self.client.modify_sl(ticket, sl)
        except BridgeError as e:
            self._finish(s, leg, "rejected", str(e))
            self.notify(f"{strat.name}: trail to {sl} REJECTED: {e}; the stop stays where it was")
            return
        self._finish(s, leg, "filled", None)

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
            if hasattr(strat, "initial_stop"):
                intent.sl = strat.initial_stop(intent.sl, quote["bid"], quote["ask"])
                if intent.sl >= quote["bid"]:  # the backtest's gap rule: no risk, no trade
                    self._finish(s, intent, "skipped",
                                 f"gap: stop {intent.sl} is not below the bid {quote['bid']}")
                    return
            else:
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
