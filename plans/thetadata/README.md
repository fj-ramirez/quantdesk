# ThetaData — history first, then a live feed (2026-09-25)

Bought 2026-09-24: **ThetaData Options Standard, $80/month. Options only — no Stocks, no
Indices subscription.** This is the "worth it if budget grows" source `PLAN.md` §1 deferred until
after Phase 4; the budget line in `PLAN.md` and `context/data-and-ops.md` is updated to match.

## What the subscription gives this desk

| Capability | Endpoint (Theta Terminal v3, `http://<terminal>:25503`) | Used by |
|---|---|---|
| Full EOD chain for one session, every expiry, with IV, Greeks, bid/ask/volume and `underlying_price` | `GET /v3/option/history/greeks/eod?symbol=&expiration=*&start_date=D&end_date=D` | T26 |
| Open interest; the value reported **on date D is the close of D−1** (OPRA, ~06:30 ET) | `GET /v3/option/history/open_interest?symbol=&expiration=*&date=D` | T26 |
| Real-time snapshots and streaming quotes | `/v3/option/snapshot/*`, the terminal's stream | T126, T21–T23 |

What it does **not** give: SPX/VIX index prices (Indices) or ETF quotes and bars (Stocks). So
T110 (same-session VIX) and T72 (live spot) stay blocked. Spot for a history snapshot comes
from the greeks row's `underlying_price`. If that is empty for SPX without an Indices
subscription, the fallback is the parity-implied forward (T33), and T125's probe has to
answer that **at the data level** before T26 relies on it.

## Rules this initiative settles

1. **The homeserver owns the terminal.** One ThetaData login equals one live terminal session,
   and a second session can knock the first one off. `theta-terminal` is defined in
   `compose.prod.yaml` and always runs there. Dev starts one only by naming
   `compose.theta-dev.yaml` explicitly, and only when the homeserver's is not needed.
2. **The Cboe live record stays authoritative.** A history load skips every session that
   already has an EOD snapshot. Backfilled sessions carry `source='thetadata'`, `is_eod=true`
   and `session_date = D`, so `WHERE is_eod GROUP BY session_date` never sees two rows for one
   session.
3. **OI semantics match the live capture.** A 16:20 Cboe capture on D carries D−1's closing OI.
   So a history snapshot for D pairs the D greeks/EOD rows with the OI reported *on* D. Pairing
   it with D+1's OI would leak the future into every backtest.
4. **The engine still recomputes gamma from IV** (context/gex-engine.md). ThetaData's own gamma
   is Black-Scholes with no dividends and is stored only as the vendor cross-check, as Cboe's
   is. Its IV is also inverted without dividends, which is a small, documented difference from
   Cboe's.
5. **A historical replay never writes to `gex.decisions`.** That table is the live, append-only
   track record. Replayed decisions (T127) go to a research table of their own.

## Tasks and order

```
T125 terminal service + probe ──▶ T26 history loader ──┬─▶ T25 GEX-level backtest
                                        │               └─▶ T127 decision replay ─▶ T112 (after T115–T117)
                                        └─▶ T126 live provider ─▶ T21–T24 (streaming, re-pointed from Tradier)
```

| ID | What | Status |
|---|---|---|
| T125 | `theta-terminal` service (prod always, dev opt-in), env, probe, doc updates | done bar the prod deploy |
| T26 | `providers/thetadata.py` history client + `gex.gex.history` CLI, resumable | open |
| T126 | `PROVIDER=thetadata` for live captures from the snapshot endpoints, + live 0DTE pull | code landed, not yet run live |
| T25 | GEX-level backtest over the loaded history — re-scoped, see `TASKS.md` | open |
| T127 | Replay the decision engine over history into a research table | open |

## Result

### T125: partial (2026-09-25)
- `theta/` (Dockerfile + entrypoint): Java 21 JRE and the bootstrap jar (gitignored and copied
  in before building). The terminal binds `0.0.0.0:25503` by itself, so there is no forwarder.
  Credentials come from `THETADATA_API_KEY`, or from `THETADATA_USERNAME` + `THETADATA_PASSWORD`
  (written to a 0600 creds file). With neither set, it exits 64 and names the variables
  (verified locally).
- `theta-terminal` is in `compose.prod.yaml` only, on `internal`, with no published port;
  `THETADATA_URL` points the prod anchor at it. The dev opt-in is `compose.theta-dev.yaml`.
  All three compose configs parse, and prod still publishes no host ports.
- The budget line is updated in `PLAN.md` and `context/data-and-ops.md`.
- **Live probe, 2026-09-25** (dev terminal, stopped afterwards). The terminal reports
  `Options: STANDARD`, Stock/Index/Rate `FREE`, and **4 max concurrent requests**. The v3 column
  names match the docs, and so the parser. The OI rows are stamped 06:30 on the session date
  (the previous close, which is rule 3). **`underlying_price` is filled on 30,182/30,182 SPX
  rows** without an Indices subscription, so no T33 fallback is needed for spot.
  2026-09-24: SPX 30,182 contracts / 58 expiries, spot 7704.13; SPY 13,028 / 33, spot 767.18.
- **Cross-check against the desk's Cboe EOD for 2026-09-22** (engine `ALL` filter):

  | | Cboe | ThetaData |
  |---|---|---|
  | SPX net GEX / flip / walls | 9.851e10 / 7660 / 7800, 7500 | 9.747e10 (−1.1 %) / 7667 / 7800, 7500 |
  | SPY net GEX / flip / walls | 5.406e9 / 769.8 / 785, 745 | 4.933e9 (−8.8 %) / 770.1 / 785, 745 |

  The walls are identical and the flip points agree within 0.1 %. SPY's gap is consistent with
  ThetaData's no-dividend IV plus about 1.1k new listings with no OI yet (unknown, rule 4).
- Found while checking: the desk has **no EOD for 2026-09-23 or 09-24**. The last GEX capture
  was 2026-09-23 03:56Z, while the terminal and research workers are current, so the
  homeserver's `gex-capture` needs looking at. The history loader fills both sessions once it
  is deployed.

### T26: code landed, not yet run live (2026-09-25)
`providers/thetadata.py` (client, `build_eod_snapshot`, the one-session provider, the probe
`__main__`) and `gex/history.py`. 9 offline tests pin plan rules 2–4, resumability, and the
capture-window guard. They also pin that T71's content dedupe skips a stale report that is
byte-identical to the previous session. Full suite: 1260 passed.

### T126: code landed, not yet run live (2026-09-25)
Prod already ran `PROVIDER=thetadata`, which `get_provider` did not know, so every capture
since the switch failed and the Explorer's 0DTE view had nothing but 16:00 ET backfills. At
16:00, every PM-settled 0DTE contract is expired by definition.
- `ThetaDataProvider` (registered as `thetadata`) reads `/v3/option/snapshot/greeks/first_order`
  + `/v3/option/snapshot/open_interest`. It uses `first_order` because `greeks/all` needs the
  Pro tier; the engine recomputes gamma from IV anyway. `captured_at` is the newest quote
  timestamp, capped at now. OI is the 06:30 ET report, which is the same rule-3 OI a Cboe
  capture carries.
- `OptionChainProvider.fetch_expiry` narrows the request to one expiry. The base default
  filters the full chain, so Cboe still answers it; ThetaData asks the terminal for that
  expiry alone.
- `GET /api/gex/gex/{u}/live?filter=ZERO_DTE` pulls today's expiry on request, computes it and
  stores nothing (`snapshot.id` null). It returns 409 on a non-trading day and 503 with the
  reason when the provider has none. This is request handling, not background work
  (invariant 7).
- The Explorer, with the 0DTE filter and nothing pinned, polls it every 30 s. On failure it
  falls back to the stored snapshot and names the reason.
- Unverified until run on the homeserver: the live column names (taken from the docs, like
  T26's were), and what `underlying_price` carries for SPX intraday without an Indices
  subscription.

