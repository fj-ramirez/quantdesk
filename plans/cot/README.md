# cot: CFTC positioning that fits what the desk trades (2026-10-13) — T155–T158

The terminal has ingested the CFTC Legacy report since T79: net large-speculator positions for
the 10Y note, E-mini S&P, the USD index, WTI and gold, each with a three-year percentile. The
brief prints them in a table. Nothing has tested them, and three of the five are not things the
user trades.

This initiative does three things:

1. **Coverage that matches the book.** The CFDs the user trades (`S&P.fs`, `NAS100.fs`,
   `DJ30.fs`, `XAUUSD`, `EURUSD`) get their underlying futures' positioning. Financial futures
   also get the **Traders in Financial Futures** split (leveraged funds vs asset managers), and
   gold and crude get the **Disaggregated** managed-money line. The Legacy "non-commercial"
   category mixes hedge funds with asset managers on financial futures, which are the two
   groups the TFF report exists to separate.
2. **A stage-1 test** of positioning extremes as a weekly-horizon signal on those CFDs, with the
   search space declared before the first run (T156).
3. **A readable surface.** The new series appear in the brief, and positioning is reachable
   through the MCP connector (T157).

Decisions taken while the user was away are in [DECISIONS.md](DECISIONS.md). Each is marked
**taken** (the recommended option, applied) or **deferred** (needs the user).

## Verified facts (2026-10-13, live against publicreporting.cftc.gov)

- Three Socrata datasets, all futures-only:
  - Legacy `6dca-aqww`: `noncomm_positions_long_all` / `_short_all`.
  - TFF `gpe5-46if`, history from **2006-06-13**: `lev_money_positions_long` / `_short`,
    `asset_mgr_positions_long` / `_short`, `dealer_positions_long_all` / `_short_all`.
  - Disaggregated `72hh-3qpy`, history from **2006-06-13**: `m_money_positions_long_all` /
    `_short_all`, `prod_merc_positions_long` / `_short`, `swap_positions_long_all` /
    `swap__positions_short_all` (**double underscore**, the API's spelling).
- All three report on the same Tuesday and publish on the same Friday 15:30 ET, so the
  adapter's `release_as_of` applies to all of them unchanged.
- Contract codes, the newest name each reports under, and Legacy history:

  | Code | Current name | Legacy from | CFD |
  |---|---|---|---|
  | `13874A` | E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE | 1997 | `S&P.fs` |
  | `209742` | NASDAQ MINI - CHICAGO MERCANTILE EXCHANGE | 1999 | `NAS100.fs` |
  | `124603` | DJIA x $5 - CHICAGO BOARD OF TRADE | 2002 | `DJ30.fs` |
  | `099741` | EURO FX - CHICAGO MERCANTILE EXCHANGE | 1999 (1986 as ECU) | `EURUSD` |
  | `088691` | GOLD - COMMODITY EXCHANGE INC. | 1986 | `XAUUSD` |

- Every financial contract was **renamed on 2022-02-08** without changing its code (for
  example "NASDAQ-100 STOCK INDEX (MINI)" became "NASDAQ MINI"). The adapter's
  reassignment check reads only the newest row, so this is fine.
- The **consolidated** codes (`13874+`, `20974+`, `12460+`) combine the mini, micro and big
  contracts, but exist in TFF only from 2010-06. The minis go back to 2006 in TFF and further
  in Legacy, so the minis are used. In the early years the big S&P and Nasdaq contracts
  (`138741` to 2021, `209741` to 2015) carried much of the positioning, which the three-year
  percentile only partly normalises. This is a caveat on any result before about 2012.
- **The release lag is not always Friday.** CFTC's own catch-up schedules show:
  - 2013 shutdown: first delayed report published 2013-10-25, back on schedule by 2013-11-08;
  - 2018–19 shutdown: first delayed report 2019-02-01, current again around 2019-03-08;
  - 2025 shutdown: reports dated 2025-09-30 → 2025-12-23 published 2025-11-19 → 2025-12-29.
    That is the accelerated schedule CFTC posted on 2025-12-09, which superseded
    [release 9138-25](https://www.cftc.gov/PressRoom/PressReleases/9138-25)'s table from the
    11-10 report on ([COT special announcements](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalSpecialAnnouncements/index.htm));
  - 2023 ION cyber incident (found during T158): reports dated 2023-01-31 → 2023-03-14
    published 2023-02-24 → 2023-03-21, one CFTC announcement per report;
  - holiday weeks: for example the 2025-11-25 report went out on Monday 12-01 (Thanksgiving
    week), and the 2025-12-23 report on Monday 12-29.

  Until T158 the adapter's `release_as_of` gave the Friday in all these cases, so the stored
  `as_of` was **early** for them: a point-in-time leak (invariant 10).

## Tasks

### T155 · Opus · —
**COT coverage: TFF and Disaggregated reports, and the contracts behind the traded CFDs.**
- Paths: `backend/app/modules/terminal/adapters/cftc.py`, `universe.py`, `derive.py`,
  `brief.py`, `backend/tests/test_terminal_cftc_adapter.py`.
- `source_code` stays the bare contract code for the existing Legacy series (no change to
  rows already stored). New series use `<report>:<code>:<group>`, for example
  `tff:13874A:lev_money` or `disagg:088691:m_money`.
- New series:
  - Legacy net spec: `pos.cot.nas`, `pos.cot.dji`, `pos.cot.eur`;
  - TFF leveraged funds and asset managers for es, nas, dji, eur;
  - Disaggregated managed money for gold and crude;
  - and the three-year percentile of each.
- Acceptance: contract tests against fixtures. Nightly ingest backfills the new series on
  its first run, because `cmd_ingest` fetches from `backfill_start` every night.

### T156 · Opus · T155 (reads CFTC directly, so it does not wait for the deploy)
**Stage-1 test of COT positioning extremes on the traded CFDs.** The search space and fixed
choices are in [study.md](study.md), declared before the first run. Harness:
`app/modules/research/cot_study.py`.

### T157 · Sonnet · T155
**Positioning on the desk's read surfaces.** The brief's positioning table covers every new
series. The market-research skill learns to read the TFF split. No new MCP tool is needed,
because `terminal_series` already serves any series id. Writing that down in the skill is the
whole job.

### T158 · Opus · —
**COT `as_of` is early in shutdown and holiday weeks.** See *Verified facts*. The user chose
the recommended fix on 2026-10-13 ([DECISIONS.md](DECISIONS.md) D4): correct the rule, and
delete the early rows in a one-off migration that documents the exception.

## Result

### T155: landed 2026-10-13 (`8e02d52`). Deploy pending.

- **Code:**
  - `adapters/cftc.py` reads the Legacy, TFF and Disaggregated reports. Source codes are
    `<report>:<code>:<group>`, and a bare code is still Legacy non-commercial.
  - `universe.COT_LEGACY`, `COT_SPLIT` and `COT_KEYS` are the one list. `derive` builds a
    percentile per key, and the brief prints a row per key.
- **New series:** 13 raw and 13 percentiles. Legacy `nas`, `dji` and `eur`; TFF
  `{es,nas,dji,eur}.{lev_money,asset_mgr}`; Disaggregated `{gold,crude}.m_money`.
- **Verified:**
  - Every one of the 18 CFTC series was live-fetched through the adapter on 2026-10-13. The
    Legacy series reach back to 2000 (the Dow from 2002-05), and TFF and Disaggregated to
    2006-06-13.
  - `pos.cot.es` and `pos.cot.gold` for 2026-10-06 match the desk's stored values exactly.
  - The adapter had no contract tests until now: there are 16 in
    `tests/test_terminal_cftc_adapter.py`.
- **Not done:** the deploy. The next nightly `terminal-ingest` run after the backend image
  is rebuilt backfills everything from `backfill_start`. No migration is needed (decision:
  "new capture symbols need no migration" applies here too).

### T156: study run 2026-10-13. No trial passes and there is no lead. Registry write open.

**The answer is no.** None of the 84 trials passes stage 1, and none qualifies as a lead.

| Gate | Trials passing (of 84) |
|---|---|
| ≥ 100 trades and ≥ 30 episodes | 64 |
| mean R > 0 after doubled costs | 39 |
| mean > 2 episode-clustered SE | **1** |
| positive in most calendar years | 30 |
| positive in both IS (< 2018) and OOS | 9 |
| OOS Sharpe above the noise ceiling (1.66 at N = 172,591; 1.00 for the family alone) | **0** |

What the numbers say:

1. **Costs don't decide it.** At holds of one to four weeks the spread is 0.002–0.006R
   per trade, and swap at most 0.02R. The gross signal is about zero. That is the opposite of
   T147, where cost was everything.
2. **Gold's positive "follow" rows are just gold's trend.** Following crowded-long specs
   means being long gold most of the time. Always-long gold over the same span earns
   +0.07 / +0.22 / +0.35R gross at 5 / 10 / 20 days, which is more than any gold COT row. The
   same holds for the indices.
3. **The one t > 2 is in-sample only.** EURUSD Legacy follow at 20 days: +0.29R, t 2.56,
   14 of 21 years positive. But it is +0.41R before 2018 and −0.04R after. That is how the
   2009–17 euro trend looks, not an edge.
4. **The textbook reading is wrong here.** Fading speculators at extremes (contrarian on
   Legacy non-commercial or managed money) loses on gold and EURUSD at every hold.
5. **The TFF split doesn't rescue it.** Leveraged-fund and asset-manager extremes are no
   better than the Legacy total on any instrument.

Files:
- Search space: [study.md](study.md).
- Harness: `app/modules/research/cot_study.py` (16 tests).
- Ledger: [docs/edges/search-log.md](../../docs/edges/search-log.md) #440–523.
- The per-trade output was written to the session scratchpad and is reproducible with
  `cot_study --out <dir> --n-registry <n>`.

**Open:**
- Record the 84 trials. Run `cot_study --out <dir> --n-registry <n> --record` on the
  homeserver. Until then the noise ceiling undercounts by 84 (D9).
- COT stays useful as **context**: the brief's positioning table, the market-research
  read. It is not a trigger. Anything further, such as COT as a regime filter on a rule that
  has its own edge, would be a new declared family, and the result above says to expect
  little from it.

**Post-run correction (T158, 2026-10-13).** Two bugs in the harness's release dates, both found
while fixing the adapter:
- `_no_release_on` treated a Thursday Veterans Day (2004, 2010, 2021) as Thanksgiving, which
  pushed those weeks' releases to Monday. The error was late, never early.
- The ION incident's reports (2023-01-31 → 03-14) were published 3–4 weeks late, but were not in
  `SHUTDOWN_REPORT_WINDOWS`, so they entered weeks before they were public. That error **was**
  early. The window is now dropped like the shutdowns.

Both fixed and re-run on the cached data. **The verdict is unchanged: 84 of 84 fail.** 45 trials
lost or gained a trade, and no trial's doubled-cost mean moved by more than 0.02R. The only
t > 2 is still EURUSD Legacy follow at 20 days (t 2.58, IS +0.41R, OOS −0.04R). The numbers in
the table above and in the search log are from the first run.

### T158: landed 2026-10-13. Deploy pending.

- **Rule:** `adapters/cftc.release_date(report_date)`.
  - A report a delay held back takes its date from `DELAYED_RELEASES`: the 2013, 2018–19 and
    2025 shutdowns and the 2023 ION incident.
    - 2025 and 2023 are exact, from CFTC's tables and announcements.
    - 2019 follows the Tuesday-and-Friday cadence CFTC announced in release 7864-19.
    - 2013 dates each report to the Friday ending the week CFTC announced, because release
      6745-13 named weeks, not days. Those dates can be late, never early.
    - A delayed report not in the table gets the date its delay ended.
  - Otherwise it is the Friday, pushed to the next day CFTC could publish on. That skips
    weekends, federal holidays, the day after Thanksgiving, and four executive-order or
    mourning closures (2004-06-11, 2008-12-26, 2025-12-26, 2026-01-02).
  - A test checks every week from 2003 to 2026: the new date is never earlier than the old
    Friday. That is the property the migration relies on.
- **Migration** `c0d1e2f3a4b5_t158_cot_early_as_of.py`.
  - It deletes every `pos.cot.*` row, raw or `.pctile`, whose `as_of` is earlier than 15:30 ET
    on its corrected release date.
  - The percentile rows are caught by the same test. A percentile's `as_of` is the running
    maximum over its window, and corrected release dates never go backwards.
  - Counted read-only against the homeserver on 2026-10-13: **850 of 11,619 rows, in 89 report
    weeks.**
  - After deploy, the nightly ingest inserts the corrected rows and the derivation rebuilds the
    percentiles. Until that run, those weeks are simply missing.
- **Acceptance:**
  - Tests pin the 2025 and 2023 catch-up dates, Thanksgiving and the other holidays, a Thursday
    Veterans Day, and the as_of `parse` stores for a shutdown report.
  - Not yet checkable, because it needs the deploy and one ingest: `terminal_series
    pos.cot.gold as_of=2025-11-01` should return the 2025-09-23 report, not 10-28's.
- **Not covered:** a delay CFTC announced that this list misses. The sources checked were CFTC's
  COT special-announcements page and the press releases above. Hurricane Sandy (2012) turned up
  no COT delay.
