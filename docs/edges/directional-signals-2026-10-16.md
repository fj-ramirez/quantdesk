# Directional signals, 2026-10-16: results, and what needs you

The question was: GEX levels may mark *where* price reacts, but which signals say *which way*?
Five signals with a flow mechanism and a published source were declared in
[hypothesis-backlog.md](hypothesis-backlog.md) pass 12 and committed (e1e7187) **before**
anything ran. Four were backtested once. The fifth needs history that starts loading tonight.

## Results

| Signal | Test | Result | Spec |
|---|---|---|---|
| Rest-of-day momentum into the close (gamma / LETF hedging) | S&P.fs, NAS100.fs, DJ30.fs 2019–26, #536–538 | **Fail.** −0.03 to −0.05% per trade, gross ≈ 0, 0–2 of 8 years positive | [rod-momentum](rod-momentum.md) |
| Turn of the month | ^GSPC / ^NDX 1990–2019, #539–540 | **Lead.** +0.25 / +0.45% per trade, t 2.5 / 2.7, 21–22 of 30 years positive; below the bar of 3.55, and only t ≈ 1.8 above ordinary drift | [turn-of-month](turn-of-month.md) |
| Month-end pension rebalancing (equity leg) | S&P.fs, NAS100.fs 2019–26, #541–542 | **Fail.** Sharpe 0.10 / 0.00 vs buy-and-hold 0.66 / 0.79 | [month-end-rebalancing](month-end-rebalancing.md) |
| Even weeks of the FOMC cycle | S&P.fs, NAS100.fs 2019–26, #543–544 | **Fail.** Gone after publication, like pre-FOMC drift | [fomc-cycle](fomc-cycle.md) |
| Gamma sign chooses bounce vs break | T167, on the T166 history | **Not run yet**, declared before the data exists | [gamma-conditioned-walls](gamma-conditioned-walls.md) |

Ledger: N = 544 after these runs.

**What this says:** none of the calendar or flow effects that work on decade-long index samples
survives on these CFDs at a tradable size. The one signal left with a mechanism that matches
the level events, **the dealer gamma sign**, is also the one the desk has unique data for. It
gets a clean test on ~3.5 years once T166 has loaded.

## Automated, nothing for you to do

- **Forward read of the gamma filter.** The logger has stored `net_gex` on every level event
  since T142. `gex_level_events(by_gamma=True)` now splits each key into `gamma+` / `gamma−`.
  The tool is live once this branch is deployed (step 2 below).
- **Stage 1 of the gamma filter** is part of T167 (`TASKS.md`), run on the same pass as the
  unconditional level rules.

## Needs you

### 1. Start the T166 backfill (today, homeserver)

Everything gamma-related waits on this. Run it from the stack directory at any time. It holds
its first request until 17:00 ET:

```
GEX_BACKFILL_NOT_BEFORE=2026-10-16T17:00:00-04:00 \
  docker compose -f compose.yaml -f compose.prod.yaml --profile backfill up -d --build gex-backfill
docker logs -f quantdesk-gex-backfill-1
```

Progress: `ls data/research/gex_intraday/*/ | grep -c levels`, out of about 935 sessions per
symbol, which takes about 10–14 days.

### 2. Merge and deploy `edges/directional-signals`

This makes the MCP tool's `by_gamma` reachable from claude.ai. The branch also carries the
specs, the ledger rows and the scripts.

### 3. Decide on the turn-of-month lead

By the search-log rules, a lead is not demo-traded. Options:

| Option | What it means | Cost |
|---|---|---|
| **Leave it (recommended)** | Logged as a lead, nothing trades | None. t is below the bar, and half of the effect is ordinary drift |
| Use it as a bias on level events | A new candidate, for example "on turn-of-month days take only the long-side level events", forward only | It cuts an already thin level-event sample to ~4 days a month, so it would take years to read |
| Demo it as its own rule | Long S&P.fs / NAS100.fs at 0.01 lot over each month boundary | About 12 trades a year: ≥ 30 demo trades take 2.5 years |

### 4. One check on your ThetaData account

Signed 0DTE option flow (aggressor-side call vs put premium) was the fourth-ranked signal. It
needs **trades with the quote at the time of each trade**. Check whether ThetaData *Options
Standard* includes **trade-quote** history for options. If it does, it can be
declared as backlog row 50. If not, it costs a plan upgrade, and I would not recommend that
before the gamma result is in.

### 5. If you trade level alerts by hand meanwhile

The Telegram alert already prints the net GEX and "long/short gamma". If you use it to pick
bounce over break, log **every** alert in the journal, including the ones you skip. Otherwise the
journal measures your choices, not the rule.

## Not declared, and why

| Signal | Why not |
|---|---|
| Charm / vanna drift into the close | Practitioner claims only, with no published test to fix parameters from |
| Market-on-close imbalance (15:50) | Exchange feed, not available to the desk |
| Futures order-flow imbalance | CFD ticks carry no aggressor side; CME data is paid |
| Variance risk premium | Quarterly horizon; it cannot set an intraday direction |
| Sentiment extremes | Already T173 |
| Bond-leg version of month-end rebalancing | The desk's instrument set has no bond CFD today |
