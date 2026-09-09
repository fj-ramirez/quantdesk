# 03 · Regime board

## Goal

Explain *why* a given optioned market is fading or continuing, using the engine the app already
has, and do it across sector and industry ETFs rather than only the five index and gold
symbols. For each symbol: the dealer gamma sign, how far spot is from the flip point, how much
room there is to the next wall in each direction, how much of the gamma is 0DTE, and implied
versus realized volatility. From those, a labelled verdict with its reasons listed.

## What the user sees

A `/regime` route (nav tab "Regime"). One row per option-tracked symbol:

| Symbol | Verdict | Net GEX | |net|/abs | Flip dist % | Wall below (dist, ATR) | Wall above (dist, ATR) | Room beyond | 0DTE share | IV/RV | Trend pct |

The verdict cell expands to the reasons that produced it. Clicking the symbol opens the
dashboard for it with the current filter. At the top, the cross-asset strip from T54 once it
exists.

## Data

- Option chains: the existing Cboe provider, extended to sector and industry ETFs (T47).
- Stored levels: `gex_levels` and `gex_by_strike` for wall spacing and gamma composition.
- Bars from T42 for ATR normalisation and RV; `iv30` and the trend percentile from T45.

## Design decisions

- **The extended capture is a separate job, after the core five.** Adding twenty symbols to
  the 16:20 job would put the P0 capture behind twenty more HTTP calls. `capture_extended_job`
  runs at 16:45 ET, is registered separately, and reuses `capture_all_symbols` (sequential,
  per-symbol error isolation). Its symbols come from a new `EXTENDED_SYMBOLS` setting.
  `SYMBOLS` is untouched.
- **Verify every new symbol live before enabling it**, the way T38 did for GLD and DIA:
  contract count, expiry count, IV scale, whether 0DTE exists. Record each in the enum
  comment. A symbol that fails verification is left out of the default, not "fixed" by a
  guess.
- **Dividend yield.** `DIVIDEND_YIELD` is a global 1.3 % S&P-ish parameter. XLE and XLU carry
  roughly 3 %, XLK under 1 %. The effect on net GEX is small but the DIA precedent
  (`docs/validation.md` §9) says: measure it for one high-yield and one low-yield sector,
  record the sensitivity, do not silently ignore it. Per-symbol yields, if added, go in a
  config map with the same "parameter, never fetched" rule as `RISK_FREE_RATE`.
- **Verdict rules are deterministic and stated in code**, with the same gating as
  `report.py`'s positioning label: below the documented `|net|/abs` floor the row reads
  *noise-dominated* and no verdict is issued. First-cut rules, to be refined once the agent has
  seen a week of live rows:
  - *fade*: positive net GEX, flip below spot by more than 1 ATR, nearest wall in either
    direction within 1 ATR, 0DTE share above a documented floor.
  - *continuation*: negative net GEX, or positive net GEX with spot within 0.5 ATR of the
    flip and the nearest wall in the direction of the last 5-day move more than 2 ATR away.
  - *mixed*: everything else, with the reasons that pulled each way.
- **Room beyond** = distance from the wall to the next strike whose |GEX| exceeds 25 % of the
  wall's, in the same direction. It is the "how far can it run once the wall breaks" number.
- **No CFD conversion on this page.** The CFD ratio (T41) needs a user-typed spot per symbol;
  a 25-row table cannot ask for 25 spots. `CFD_INSTRUMENTS` may gain display names for the
  new symbols where the broker offers them, but conversion stays on the report page.

## Tasks

### T47 · Sonnet · T02, T38
**Extend option capture to sector and industry ETFs**

Add to `Underlying` and `_ROOT_TO_UNDERLYING` in `backend/app/models/chain.py`, after live
verification of each against
`https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json`: the 11 Select Sector
SPDRs (XLK, XLF, XLE, XLV, XLI, XLY, XLP, XLU, XLB, XLRE, XLC) and IWM, SMH, XBI, KRE, XOP,
TLT, HYG, EEM, FXI, SLV, USO, GDX. Record contract and expiry counts per symbol in the enum
comments as T38 did. Add `EXTENDED_SYMBOLS: str` to `Settings` with the verified subset as
default and a `extended_symbols` property; add `capture_extended_job` to
`backend/app/jobs/scheduler.py` at 16:45 ET mon–fri, calling `capture_all_symbols(
settings.extended_symbols, is_eod=True)` with the same top-level try/except as
`capture_eod_job`. Extend `catch_up_missed_eod` coverage to the extended set only if that is a
one-line change; otherwise leave a TODO in `TASKS.md` and say so. Add `GET /api/symbols`
returning `{core: [...], extended: [...]}` and make the frontend `UNDERLYINGS` list (currently
a hard-coded `as const` array in `frontend/src/api/types.ts`) accept the extended symbols in
the URL-state validator without breaking the TopBar's five-symbol switcher; the switcher
gains a second group. Report page: `CFD_INSTRUMENTS` lookups for a symbol without an entry
must degrade to "no CFD mapping" rather than raise (check `api/report.py`'s current
behaviour first and pin it with a test).

Paths: `backend/app/models/chain.py`, `backend/app/config.py`, `backend/app/jobs/scheduler.py`,
`backend/app/jobs/catchup.py` (conditional, see above), `backend/app/api/health.py` (extended
symbols appear in capture freshness, labelled), new `backend/app/api/symbols.py`,
`backend/app/gex/report.py` (`CFD_INSTRUMENTS` entries only), `backend/app/api/report.py`,
`frontend/src/api/types.ts`, `frontend/src/state/urlState.ts`,
`frontend/src/components/layout/TopBar.tsx`, tests, `docs/validation.md` (new subsection
recording the per-symbol verification and the dividend-yield sensitivity measurement).

Constraints: `SYMBOLS` and `capture_eod_job` are not modified. Engine and Greeks untouched.
Do not run the extended capture inside the 16:20 job under any circumstances. Storage
estimate for 23 extra Parquet files per day goes in the validation subsection.

Acceptance: `POST /api/snapshots/capture?underlying=XLK` persists a snapshot with a non-zero
contract count and stored levels for all default filters; the same for at least 15 of the
listed symbols, with the failures named; `capture_extended_job` with one symbol failing still
captures the rest (test with a mock provider); `/api/health/capture` shows the extended
symbols; dashboard deep link `?symbol=XLK` renders; all four test and lint commands pass.

### T48 · Opus · T42, T45, T47
**Regime metrics: pure module and API**

Paths: `backend/app/scan/regime.py` (pure; consumes a `GexResult`, the by-strike frame, the
spot, ATR14, `iv30`, `rv20`, the 5-day return, and returns a frozen `RegimeRow` with every
input echoed and a `reasons: tuple[str, ...]`), `backend/app/api/scan.py` (add `GET
/api/scan/regime?filter=`, reading stored levels and by-strike rows the way `api/gex.py`
does, never recomputing from Parquet when levels are persisted), tests,
`docs/validation-scan.md`.

Reuse `report.py`'s positioning gate by importing it; do not duplicate the ratio floor. The
0DTE share needs the by-strike GEX split by expiry bucket: check whether `gex_by_strike` holds
it per filter (`ZERO_DTE` vs `ALL`) and derive the share as the ratio of the two filters'
absolute GEX at the same strike set. If the stored data cannot support it, return `None` and
document that instead of estimating.

Acceptance: fixture chains pin each verdict branch and the noise-dominated branch; "room
beyond" is hand-checked on a fixture with a known strike ladder; a DIA fixture never yields a
verdict; the API returns a row per core and extended symbol with `None` where inputs are
missing; `uv run pytest` and `ruff check .` pass.

### T49 · Sonnet · T48, T55
**Regime page**

> **UI spec superseded (2026-09-09):** the page spec and task block for this view now live in
> [07-ui.md](07-ui.md); the paragraph below is kept for history. Dispatch from 07-ui.md.

Paths: `frontend/src/pages/Regime.tsx`, `frontend/src/components/regime/RegimeTable.tsx`,
`api/queries.ts`, `api/types.ts`, `App.tsx`, `AppShell.tsx`, MSW handlers and fixtures, tests.

Acceptance: verdict cell expands to reasons; noise-dominated rows are visibly distinct and
carry no verdict colour; clicking a symbol navigates to `/?symbol=XLK` preserving the filter;
`npm test` and `npm run lint` pass; supervisor sees live rows.

## Verified facts (2026-09-09, from T47's first live 16:45 run)

**Cboe's delayed feed serves hours-stale payloads for thin ETFs, and those land flagged as
EOD.** This is the single most important thing T48 must design around, because a regime
verdict is only as honest as the chain under it.

Observed on the first real 16:45 extended capture. Of the 23 extended symbols, five ended the
day with an `is_eod` snapshot whose `captured_at` is **before the 16:00 close**:

| Symbol | EOD `captured_at` (ET) | Contracts | Staleness at the close |
|---|---|---|---|
| XBI | 11:39 | 2,088 | 4h 21m |
| XLC | 13:33 | 964 | 2h 27m |
| XLRE | 13:35 | 222 | 2h 25m |
| GDX | 15:33 | 3,042 | 27m |
| KRE | 15:35 | 1,552 | 25m |

The mechanism is `app/jobs/capture.py::_persist_sync`. `captured_at` is Cboe's own payload
timestamp, recorded verbatim, and the duplicate check keys on an exact
`(underlying, captured_at)` match. For SPX/SPY that timestamp advances on every request
(verified 2026-09-04), so the check only ever fires on a genuine retry. For a thin sector ETF
it does **not** advance — Cboe regenerates the payload only on activity — so a 16:45 fetch can
return a payload identical to one served hours earlier. The existing row is then promoted to
`is_eod=True` rather than a fresh row being written.

That promotion is deliberate and correct on its own terms (its docstring explains that
skipping without promoting would leave the day with no `is_eod` row at all). The problem it
cannot solve is upstream: **at 16:45 Cboe simply had nothing newer to give for those symbols**,
so a fresh fetch would have stored the same stale instant anyway. The promotion reveals the
staleness; it does not cause it.

Consequences T48 must handle rather than inherit silently:

- A regime verdict for XBI computed from that snapshot describes **11:39 ET**, not the close.
  Its 0DTE contracts had not yet expired at that instant, so the 0DTE-share component and any
  wall spacing measured against the closing spot are both describing a different market.
- Staleness is **per symbol and varies day to day** with each ETF's activity. It is not a
  fixed property of a symbol, so it cannot be handled with a static exclusion list.
- `app/jobs/calendar.py::effective_data_time` (T34) already exists to express an honest "as
  of" instant, and `/api/health/capture` already reports `last_eod_capture_at` per symbol.
  The pieces to surface this are in place; T48 has to use them.

**Requirement on T48:** every regime row carries the age of the chain it was computed from,
and a row whose chain predates the close is marked as such rather than being presented
alongside genuinely-at-the-close rows as if they were equivalent. Whether that means a
staleness column, a chip, or suppressing the verdict entirely past some threshold is T48's
call to make and justify — but silently ranking a 11:39 chain against a 16:44 one is not an
option. The same rule applies to `/regime`'s page task (T49, spec in `07-ui.md`).

## Verified facts (2026-09-09)

- `Underlying` is a closed `StrEnum`; `underlying_for_root` raises on unknown roots by design.
  Adding a symbol is one enum line plus one `_ROOT_TO_UNDERLYING` entry (its docstring says so).
- `capture_all_symbols` is sequential on purpose, sharing one provider connection, with
  per-symbol error isolation via `CaptureResult`.
- GLD and DIA were verified live on 2026-09-05 with 7,546 and 5,028 contracts. Sector ETFs are
  **not yet verified**; expect some (XLRE, XLC, XLB) to have thin chains.
- `CFD_INSTRUMENTS` in `app/gex/report.py` is a plain dict of five entries.
- `frontend/src/api/types.ts` `UNDERLYINGS` is an `as const` five-element array used by the
  URL validator and the TopBar.

## Likely first-contact failures

- Cboe delayed endpoint returning a payload with a different shape for a low-volume ETF (empty
  `options` array, missing `current_price`). Log and skip, do not crash the loop.
- IV scale on ETFs differs from SPX (T-series notes say SPX IV is already decimal); check one
  ETF payload by hand before assuming.
- Twenty-three extra captures at 16:45 taking long enough to overlap with the 17:30 bars job.
  Measure and record the wall time; if above 20 minutes, split into two jobs.

## Out of scope

Intraday regime tracking, single-stock chains, CFD conversion on this page.
