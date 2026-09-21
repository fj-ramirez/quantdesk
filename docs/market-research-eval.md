# chat with market-research agent

> **Read the verification before acting on this.** Transcript kept as the origin of the
> `decision-inputs` initiative (T90–T96). It was checked against the database and the code on
> 2026-09-21 and parts of it did not hold: the correlation percentiles below are read
> backwards, `etf_shares_outstanding` is not collapsing, `intraday_bars` is writing again, and
> three of the recommendations describe things that already exist. Where this file and
> [plans/decision-inputs/README.md](../plans/decision-inputs/README.md) disagree, that one is
> right. The mangled final paragraph was repaired from context in the same pass; nothing else
> here has been edited.

❯ other than the options chain, which sources of information might be useful for you to polish your chooses? just asking for next dev iteration

Good question, and I can answer it concretely rather than generically — let me first check which of these you already have and I just didn't use.

  Called quantdesk 4 times

Checked what's already there before answering — and the most useful finding is that three of the best sources are already in your database and I didn't use them. Ranked by whether they'd have changed a call I made an hour ago.

Tier 0 — already in the DB, unused. Zero integration cost.

1. terminal_edges — the transmission graph. This is the big one.
It's live (as_of 2026-09-20 19:24) and I built my entire regime thesis by hand instead of reading it. It already had it quantified:

┌──────────────────────────┬─────────────────────────────────┬───────────────────────────────────────────────────────────────────────────────┐
│           Edge           │             Reading             │                                 What it says                                  │
├──────────────────────────┼─────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ ust.10y.nominal → eq.spx │ corr pctile 0.66                │ rates→equity transmission at its weakest in 250 days                          │
├──────────────────────────┼─────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ ust.10y.real → eq.ndx    │ corr pctile 4.63                │ exactly why XLK holds a 20-day high while duration proxies sit at 60-day lows │
├──────────────────────────┼─────────────────────────────────┼───────────────────────────────────────────────────────────────────────────────┤
│ fx.usd.broad → cmdty.wti │ sign_conflict: yes, pctile 87.6 │ an automated version of my "I don't trust the oil prints"                     │
└──────────────────────────┴─────────────────────────────────┴───────────────────────────────────────────────────────────────────────────────┘

That third row is the machine-readable form of a hunch I hedged on. And the first two are a warning I didn't give you: the rates→equity channel my duration trades lean on is measured at a historic low right now. Make this a mandatory read in the trade workflow, not an optional tool.

2. gex.daily_bars.volume — populated, and I never touched it. IWM ran 1.2–1.5× its 60-day average volume for seven consecutive sessions, 1.49× on Friday's breakdown. That's distribution, not drift — it would have firmed up Trade C. A relative-volume filter is the cheapest upgrade on this list.

3. gex.etf_shares_outstanding — creation/redemption flow. For an ETF-based desk this is the best positioning proxy available: actual primary-market flow, not inferred like COT. It's current to 9/19 but only 82 rows across 27 symbols, and coverage is collapsing — 27 symbols on 9/10, 17 on 9/17, 9 on 9/18, 1 on 9/19. Fix the coverage and it's a real signal.

Also worth a bug note: gex.intraday_bars is dead — 540 rows, 6 symbols, last write 2026-09-11 19:55Z. It stopped at the same moment as chain capture while daily_bars kept running, which localizes that outage cleanly to the gex-capture worker.

Tier 1 — missing, and would have changed today's answer

4. An event calendar. I gave you trades "for this week" with no idea what's scheduled — FOMC, CPI, claims, sector earnings, and especially Treasury auctions. A 30y refunding is a first-order event for a thesis that is entirely long-end real yields. Related: policy.ff.meeting_1 exists as a node in your transmission graph with zero data, so there's no market-implied Fed path either. Fed funds futures / OIS would fill both. This is the single biggest hole.

5. A measured correlation matrix. My most important risk note — "these eight are one trade" — was an assertion, not a measurement. A rolling 60-day correlation or PCA over the ETF universe turns it into a number, and lets the decision engine cap exposure per factor instead of emitting 17 correlated shorts that look like a diversified book. Highest-value new computation here, and it needs no new data source at all.

6. Breadth. I inferred "narrow market masking a breakdown" from 27 ETFs. A/D line, % above 50/200dma, new highs−lows. Cheapest version: just add RSP (equal-weight) to daily_bars — RSP vs SPY gets you most of the signal for one ticker.

7. Crude term structure. Backwardation vs contango is what separates a physical supply squeeze (doesn't mean-revert) from speculative froth (does). That was my exact unresolved uncertainty on XOP/USO, and
COT percentiles couldn't settle it.

Tier 2 — useful, more work

- Realized vs implied vol per name. RV is computable from bars today, IV is already in the chain. The RV/IV ratio filters wall-fades and sizes stops honestly.
- Short interest / days-to-cover — matters most for KRE, XBI, XOP.
- Liquidity: ADV and typical spread. I quoted XLRE and KRE levels without knowing either.
- Sector-level transmission edges. The graph covers eq.spx, eq.ndx, eq.rut but nothing sector-level — which is where all four of my trades live. credit.hy.oas → eq.rut is already declared in the graph and empty; that's the precise edge Trade C needed.

Tier 3 — data quality

A cross-series plausibility layer. Brent 130.8 against WTI 107 should have tripped an automatic flag rather than me catching it by eye, and the FX block being a week stale should surface on the board the way ust_cc.* already does.

---

If you only do three: wire terminal_edges into the trade path and add the relative-volume filter (both would have changed today's write-up), build the correlation/factor cap (fixes the worst structural flaw in the decision engine), then the event calendar and Fed path. The first two are days of work against data you already own.