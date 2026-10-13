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
  - 2025 shutdown: reports dated 2025-09-30 → 2026-01-13 published 2025-11-19 → 2026-01-20
    ([release 9138-25](https://www.cftc.gov/PressRoom/PressReleases/9138-25));
  - holiday weeks: for example the 2025-11-25 report went out on Monday 12-01 (Thanksgiving
    week), and the 2025-12-23 report on Monday 12-29.

  The adapter's `release_as_of` gives the Friday in all these cases, so the stored `as_of` is
  **early** for them. That is a point-in-time leak (invariant 10) and is filed as T158.

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
**COT `as_of` is early in shutdown and holiday weeks.** See *Verified facts*. The fix must add
rows and never overwrite them (invariant 10), and rows already stored with an early `as_of`
stay visible at that early instant. Deciding how to handle them is the user's call: see
[DECISIONS.md](DECISIONS.md) D4.

## Result

*(filled as each task lands)*
