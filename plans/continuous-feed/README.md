# Continuous feed

Moving the app from one capture a day to a live picture of where price sits relative to the
gamma structure. Filed 2026-09-11 after the user asked "what would be the approach to have a
continuous data feed?".

This initiative does **not** allocate a fresh block of IDs. Most of the work already has a
number: Phase 4's `T18`–`T20` and the `T32` retention task were specified in `TASKS.md` in
2026-09-04 and never built, and Phase 5's `T21`–`T23` are still the real-time path. The plan
files here carry the full specs for those; only three genuinely new tasks are allocated,
**T70–T72**. Next free ID after this initiative: **T73**.

## The constraint that shapes everything

Open interest is published **once per day**, overnight, by OCC. No vendor sells intraday OI at
any price. So "continuous GEX" never means a continuously refreshed chain. It means two feeds
with different cadences:

| | What it carries | Cadence | Status |
|---|---|---|---|
| **Slow feed** | open interest, strike/expiry structure | once daily, overnight | built (Cboe EOD capture, T05) |
| **Fast feed** | spot and implied volatility | continuous | this initiative |

Everything below is a way of making the fast feed faster while the slow feed stays frozen for
the session. The engine already works this way — `contract_gex` multiplies a per-contract
gamma by an OI that does not change during the day — so nothing in `app/gex/` has to change
for any tier here. That is the point: this is a scheduling, storage and transport problem, not
a math problem.

## Tiers, cheapest first

1. **15-minute delayed polling** (`T18`–`T20`) — free, on the Cboe source already in use. The
   fast feed refreshes 27 times a session instead of once. Gets most of the value.
2. **Live spot against a frozen surface** (`T72`) — free. The underlying's price is real-time
   at no cost (the Yahoo path from T42 already works); hold OI *and* IV frozen from the last
   15-minute capture and move only spot. Because OI is fixed for the day anyway, walls and the
   flip point drift slowly — the fast-moving input is spot, and spot is free.
3. **True real-time options quotes** (`T21`–`T23`) — Tradier, $0–10/month, needs a funded
   brokerage account. Only this tier makes the *surface* live.

Tier 2 is the one that was missing from the original roadmap, and it is the best
value-per-hour in the list: it needs no new vendor, no account, and no new licence.

## The precondition that is not a data problem

APScheduler's job store is in-memory, so every job only fires while the process is alive.
`T29`'s startup catch-up rescues a missed **daily** EOD because Cboe still serves the settled
chain that evening. **Nothing can rescue a missed intraday series** — the endpoint serves only
"now". A laptop closed at 11:00 is a permanent hole in that session, forever, and
`context/workflow.md` already rates a capture that does not happen as a P0.

So continuous data requires a continuously running backend before it requires anything else.
That is `T70`, and it gates the value of everything else here even though it gates none of the
code.

## Dependency graph and dispatch order

```
T70  always-on host ───────────────────────────── (independent; do first, gates the value)

T32  gex_by_strike retention ──┐
                               ├──► T18  intraday polling ──┬──► T19  SSE ──► T72  live spot
T71  capture idempotency ──────┘                            └──► T20  intraday timeline

T21 ──► T22 ──► T23   real-time (Tradier)   blocked on a funded account, unchanged
```

`T32` and `T71` are independent of each other and both gate `T18`. Neither is optional:
`T32` because 27 captures a day multiplies the largest table by ~26×, `T71` because at 27
fires a day a duplicate write stops being a curiosity and becomes a daily event.

| Task | Model | Depends on | What | File |
|---|---|---|---|---|
| T70 | user decision, then Sonnet | — | Always-on host for the scheduler | [00-always-on-host.md](00-always-on-host.md) |
| T32 | Opus | T09 | `gex_by_strike` retention policy | [01-capture-integrity.md](01-capture-integrity.md) |
| T71 | Sonnet | T05 | Capture idempotency and content dedupe | [01-capture-integrity.md](01-capture-integrity.md) |
| T18 | Sonnet | T32, T71 | 15-minute intraday polling job | [02-intraday-polling.md](02-intraday-polling.md) |
| T19 | Sonnet | T18 | Server-Sent Events stream | [02-intraday-polling.md](02-intraday-polling.md) |
| T20 | Sonnet | T18 | `/intraday` timeline view | [02-intraday-polling.md](02-intraday-polling.md) |
| T72 | Sonnet | T19 | Live-spot overlay against a frozen surface | [03-live-spot-overlay.md](03-live-spot-overlay.md) |
| T21–T23 | Opus/Sonnet/Opus | T18 | Real-time via Tradier | [04-realtime-paid.md](04-realtime-paid.md) |

## Decisions taken here

- **Delayed data is not a material handicap for GEX.** Half the calculation (OI) is frozen
  until tomorrow morning regardless of how fast the quote feed is. This is why the ordering
  above puts a free live *spot* overlay ahead of a paid live *surface*.
- **Alpaca was evaluated 2026-09-11 and rejected as a primary source**; see
  [04-realtime-paid.md](04-realtime-paid.md) for the full finding. Short version: its market
  data API does not return open interest at all, real OPRA costs $99/month against a $50
  budget, and index-option market-data coverage is undocumented.
- **No new provider is introduced by tiers 1 and 2.** Both run on sources already in the repo
  (`providers/cboe.py`, `providers/yahoo.py`). Invariant 6 holds: a provider swap stays a
  config change.
