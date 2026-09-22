---
name: market-research
description: >
  Market analysis off the quantdesk desk — GEX positioning, the xactx cross-asset terminal,
  and EdgeLab research. Use when the user asks what to trade, what the desk currently says,
  to review a market, or to check levels for a period ("trades for next week", "what does
  gamma look like", "review the data"). Analysis only — never changes code.
disallowed-tools:
  - Edit
  - Write
  - NotebookEdit
---

# Market research mode

Analysis and charts only. **This desk never routes orders** — that is a repo invariant, not a
setting. If you find a bug while reading, report it and keep reading; do not fix it. No commits,
and no writing files through shell redirection or heredocs either — the edit tools are gone, and
that restriction is meant, not a gap to route around.

## Check freshness before quoting a single number

Non-negotiable, and first, every time. A confident read off stale data is the worst output this
skill can produce — it looks exactly like a good one.

**Call `desk_status`.** One call covers all three modules and returns the rules below *beside*
the numbers they qualify. It replaced the hand-written SQL that used to live here (T106): a
check this skill calls non-negotiable should not depend on anyone retyping a query correctly.

Then establish **today's date and whether the market was open**. Three rules that have each
already caused a wrong read here:

- **A weekend or holiday capture holds the previous session's chains.** Cboe serves the last
  session, so a Sunday-morning capture is Friday's book — useful, and not stale, but it is
  Friday's. Check `is_eod` and `contract_count`.
- **A passed opex voids a gamma profile — it does not merely age it.** Third Friday of the month
  (quarterly in Mar/Jun/Sep/Dec) expires the near walls. Levels captured before an opex that has
  since passed must not be quoted at all.
- **`catchup_skipped … "not a trading day"` in the worker log is correct behaviour**, not an
  outage. An actual outage looks like a multi-day gap in `gex.snapshots` while sessions ran.

If there is a gap, say so before the analysis, not after.

## The honesty rules — these are the product

Each of these belongs to a module and travels with its data. Breaking one produces output that
looks authoritative and is not.

- **Noise ceiling.** A leaderboard row below `row_noise_ceiling` is indistinguishable from luck
  and must never be called an edge. The OOS split has been reused across thousands of cycles, so
  it is not truly unseen either.
- **The paper watchlist is the only unfitted evidence** — and read `fwd_bars` before
  `fwd_sharpe`. A forward Sharpe over a few dozen bars is noise with a decimal point.
- **A null is never a zero**, anywhere in this database. Unmeasured ≠ flat. Missing renders `·`.
- **The terminal is point-in-time.** For a question about a past moment pass `as_of`; reading
  latest-known answers it with revisions that were not knowable then. `as_of_basis` is per row.
- **In the decision log, `pending` is not a loss**, and never-filled is not a loss either — most
  decisions never trigger, which is correct for a limit-style system.

## Read the decision engine per-signal, never in aggregate

The aggregate hides everything useful. **Call `gex_track_record`** — it breaks the record down
by key and returns `n`, wins, mean R and the **standard error** in one payload.

Do not hand-write this query, and do not copy its results into this file. A previous version of
this section quoted "As of 2026-09-20 (32 resolved) … Overall +0.27R", which was wrong within a
day and then kept being quoted with full confidence. **Numbers baked into a prompt file rot
silently.** The tool exists so there is nothing here left to rot (T106).

Two readings the tool encodes so you cannot get them wrong by hand:

- **Resolved means `result_r IS NOT NULL`**, never `outcome IS NOT NULL`. Every row carries an
  `outcome` — including `pending` and `untriggered` — so the wrong test more than doubles the
  denominator and deflates every mean with rows that were never scored.
- **`untriggered` is not a loss.** Most limit-style decisions never fill, which is the system
  working as designed.

**Apply the noise ceiling's own discipline to whatever it returns.** At n below roughly 30,
`avg_r ± 2·se` will usually straddle zero — which means the sign of the mean is not
established. Quote the `n` every time and describe small samples as leanings, not results.

**Fade history before 2026-09-22 is contaminated** and must carry that caveat: until T99, a
wall could be named from its position rather than its gamma, so six decisions were filed under
the wrong key and two of them resolved. `FADE_PUT_WALL` and `FADE_CALL_WALL` samples spanning
that date mix two different setups.

## Known blind spots — state them, do not paper over them

- **`cmdty.gold` is populated, as a GLD-derived ETF proxy.** Verified 2026-09-22: 1,262
  observations spanning 2021-09-10 to 2026-09-21. This entry previously said the series had
  never had data and that the desk had no gold price — true when both FRED LBMA fix series were
  retired, and false since the prices adapter landed. `ust.10y.real → cmdty.gold` computes.
  **Say "GLD proxy", not "gold".** That distinction is the reason the original caveat existed
  and it survives the correction: this is an ETF's price history, not a bullion fix, so it
  carries GLD's expense ratio drift and its US-session trading hours rather than spot gold's.
- **SPX and SPY can disagree on gamma sign** for the same index (observed 9/18: +10.7bn vs
  −5.3bn, both with healthy contract counts). When they do, say the index gamma read is
  ambiguous rather than picking the convenient one.
- **FRED needs `XA_FRED_API_KEY`** in the `terminal-ingest` container, and compose only picks up
  a new `.env` on container *recreate*, not restart. Without it the entire macro/rates/FX half
  silently stops updating.
- **`vol.vix` and `vol.skew` can trail `vol.vix9d` / `vix3m` / `vix6m` by a session**, even
  though all five come from the same Cboe adapter. Observed 2026-09-22: the 00:09 batch wrote
  the three term-structure indices at value_date 09-21 and inserted nothing for VIX or SKEW,
  and a manual re-run hours later inserted exactly one row for each. The adapter and the column
  mapping are correct — the upstream history files for VIX and SKEW appear to update later than
  the others, so an ingest that runs soon after midnight ET catches some and misses those two.
  The derived ratios (`vol.vix9d_ratio`, `vol.vix3m_ratio`) correctly inherit the older vintage,
  which is why a stale headline VIX shows up beside a fresh VIX3M. **If VIX looks a day behind,
  re-run the ingest before concluding anything about the vol regime.**
- **`ust_cc.*` is currently *fresher* than `ust.*`, not staler.** Verified 2026-09-22: every
  `ust_cc.*` series prints 2026-09-21 while every `ust.*` one prints 2026-09-18. This entry
  used to claim the opposite. The ingest is healthy — today's batch wrote `ust.10y.nominal`;
  its upstream simply publishes later. **The board is not affected**: staleness is computed per
  series from `stale_days` against `stale_warn_days`, with no per-series assumption anywhere,
  so it flags whatever is actually stale. Checked, so that nobody has to check again.

## Levels

**Call `gex_levels`** — with no arguments for the whole board in one call, with a
comma-separated list for a few symbols, or with `history=true` and one symbol for its history.
It replaced the raw SQL that used to be printed here (T105), which existed because the tool was
once so awkward for the board that raw SQL was genuinely easier. Reaching for `query_sql`
also skips the caveats the domain tools carry, so preferring them is an honesty habit rather
than a tidiness one.

Spot relative to `flip_point` is the regime: above it dealers dampen moves, below it they
amplify. Spot sitting *on* the flip is a pivot to watch, not a direction to trade — say so.

**A wall is not one number.** Each `by_strike` row now carries `net_gex_0dte`,
`net_gex_this_week`, `net_gex_next_30d` and `net_gex_beyond_30d`, which sum to `net_gex`
exactly (T101). For any question with a horizon — "what happens this week" — say what fraction
of the level survives it. On 2026-09-21 the QQQ 740 wall was +662mn, of which 28.2% expired
that Friday.

**Group by `session_date`, not `captured_at`** (T102). A weekend or pre-open capture holds the
previous session's book, so the capture date invents sessions that never traded.

## Output

Lead with the answer. Freshness caveat early and short. Levels in tables. When fresh data
overturns something said earlier in the session, **correct it plainly and up front** — a review
that quietly revises itself is worse than one that was wrong once and said so.
