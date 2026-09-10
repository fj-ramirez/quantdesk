# Continuation initiative

**Problem (user, 2026-09-09):** the assets they trade (SPX, SPY, QQQ, GLD, DIA via CFDs) are
fading breakouts and sitting in ranges. They want tools that show *which* markets currently
have continuation, and where money is rotating between sectors and industries.

**Framing.** Fade-versus-continue is, for optioned instruments, largely the dealer gamma
regime the engine already measures: long-gamma dealers absorb breakouts. Continuation tends to
live where dealer gamma is negative, thin or absent. So the initiative has two halves:

1. **Cross-asset scanning on daily bars** over a universe much wider than the five option
   underlyings (breakout ledger, trend/chop scorer, rotation, flows, cross-asset strip).
2. **Extending the existing GEX engine** to sector and industry ETFs and deriving a per-symbol
   regime verdict from wall spacing, flip distance and gamma composition (regime board).

Everything stays analysis-only. No order routing, no broker write APIs, data spend under
$50/month. All new data sources here are free and keyless by design.

## Dependency graph

```
T42 foundation: daily bars provider + table + universe + job + backfill      (Opus)
 ├── T43 breakout ledger, pure module + API   (Sonnet) ─┐
 ├── T45 trend/chop scorer, pure module + API (Opus)   ─┴─ T55 UI kit ── T44 /scan page (breakouts + trend; T46 folded in)
 ├── T50 rotation math + API (Opus, needs T45 for nothing; needs T42) ── T51 /rotation page (Sonnet)
 ├── T52 ETF shares-outstanding ingest (Sonnet) ── T53 /flows page (Sonnet)
 └── T54 cross-asset regime strip (Sonnet; adds a Cboe index-history bar provider)

T47 extend option capture to sector/industry ETFs (Sonnet, independent of T42)
 └── T48 regime metrics, pure module + API (Opus; also needs T42 and T45 for IV/RV) ── T49 /regime page (Sonnet)

T55 UI kit (Sonnet; frontend only) ── T44, T49, T51, T53, T54's strip ── T56 /overview page
```

UI specs for every page, the shared kit and the overview live in [07-ui.md](07-ui.md); a page
task's block in its tool's plan file is superseded by that document.

## Dispatch order

**Corrected 2026-09-09 after T42 shipped.** The original table below the correction had
T43, T45 and T52 running in parallel on the grounds that "all three read only T42's public
interface". They do — but reading the same interface is not the constraint; *writing* the same
files is. All three write `backend/app/api/scan.py`, T43 and T45 both write
`backend/app/scan/indicators.py` (T45's spec says "extend"), and T47 and T52 both write
`backend/app/jobs/scheduler.py` and add an Alembic migration off the same `down_revision`.
Check the `Paths:` line of every task in a proposed wave against every other before dispatching
concurrently; the dependency graph does not capture write collisions.

| Wave | Tasks (all Sonnet unless noted) | Note |
|---|---|---|
| 1 | T42 | Shipped 2026-09-09. Yahoo replaced Stooq; see that plan's verification section. |
| 2 | T43, T47 | Disjoint: T43 owns `app/scan/` + `app/api/scan.py`; T47 owns `models/chain.py`, `jobs/capture.py`, `jobs/scheduler.py`, `config.py`, `frontend/src/api/types.ts`. Both make a one-line additive mount edit in `app/main.py`. |
| 3 | T45 | Extends `app/scan/indicators.py` and `app/api/scan.py`, so it needs T43 merged first. Not parallelizable with anything that writes either file. |
| 4 | T52 | Writes `app/api/scan.py`, `jobs/scheduler.py`, `models/db.py` and a migration; run alone, after T45. |
| 4′ | T55 | **Ready now** (T43, T45, T47 are merged). Frontend only, so it runs alongside T52 with no shared files. |
| 5 | T48, T44 | T48 needs T42, T45 and T47. T44 needs T55 and writes only frontend files, so it runs alongside T48. |
| 6 | T50, T54 | T54 adds a bars provider — a `BAR_PROVIDER_GROUPS` config change plus a new module, per T42's registry design — and its `RegimeStrip` follows 07-ui.md. |
| 7 | T49, T53, T51 | Page tasks. Each writes `App.tsx` and its own `pages/`/`components/` folder; `App.tsx` is a one-line route swap per task, so run them sequentially or let the supervisor apply the route lines. |
| 8 | T56 | After T44, T49, T51 and T54. |

Model note: the user asked on 2026-09-09 to use Sonnet wherever possible. T42 was specced for
Opus and built by Sonnet without trouble, supervised by Opus. The `Model` column in TASKS.md
is a suggestion, not a requirement.

<details>
<summary>Original wave table, superseded</summary>

| Wave | Opus | Sonnet (parallel) | Note |
|---|---|---|---|
| 1 | T42 | T47 | T47 touches `models/chain.py`, `config.py`, `jobs/scheduler.py`; T42 touches `config.py` and `scheduler.py` too. **Supervisor writes the two new `Settings` fields and the two scheduler registration stubs first** to pre-empt the collision. |
| 2 | T45 | T43, T52 | All three read only T42's public interface. |
| 3 | T48 | T44, T46, T54 | T46 needs T45 merged. |
| 4 | T50 | T49, T53 | |
| 5 | | T51 | |

</details>

## Open decisions for the user

Nothing below blocks the remaining build; each is a choice only the user can make. Recorded
here so they survive the session.

### 1. Universe width

The default `SCAN_UNIVERSE` T42 shipped is 47 tickers: ETFs only (index, 11
sectors, ~10 industries, commodities, rates, FX, international), plus `SPX` and `^VIX`.
Continuation is more likely in single names and in the broker's full CFD list.

Widening is a config string change and costs nothing structurally. On the request budget:
the full 47-symbol, 5-year backfill ran on 2026-09-09 against Yahoo in a few minutes with a
0.5 s sleep between symbols and **zero failures or throttling**, so Yahoo's budget is not the
binding constraint Stooq's was assumed to be. The real limits are the daily 17:30 job's
runtime and how many symbols the scan pages can render usefully.

**Supervisor recommendation (2026-09-09): widen to ~120 by adding single names, in one step,
and leave the pages' default views ranked and truncated rather than paginated.** Reasoning:
the initiative exists because *ETFs* are fading breakouts — an ETF is a basket, and a basket
averages away exactly the continuation the user is hunting. Every scan page already sorts and
the tables already cut to a top-N, so the marginal cost of a wider universe is job runtime
(linear, ~0.5 s/symbol → about a minute for 120) and nothing else. What the user has to supply
is *which* names: the broker's CFD list is the natural source and only they have it.

### 2. Three ETF families have no free flow source

`docs/etf-flows-sources.md` (survey done 2026-09-09) covers SPDR and iShares — 23 of the 27
symbols. **VanEck (SMH, GDX), Invesco (QQQ) and USCF (USO)** all render shares outstanding
client-side from an internal API, so T53's page will list those four under "no flow data".
Chasing their private endpoints is possible but is scraping undocumented JSON that can change
without notice. Say the word if those four matter enough to spend a task on.

### 3. Does `/overview` become the landing page?

07-ui.md's T56 spec says the overview page "becomes the nav's default landing when the user
says so; until then it is the last nav tab". T56 will ship it as the last tab. Flipping it to
`/` later is a one-line route change.

### 4. Still no git remote, still one copy of `data/`

P0 item 3 in `docs/state-review-2026-09-05.md`, unchanged. Everything built in this initiative
exists on one disk. Adding a remote needs the user's account and is one command afterwards.

### 5. `AGENTS.md` is a byte-identical copy of `CLAUDE.md`

Untracked, created outside this session (presumably for another agent tool). Two copies of the
project's instruction file will drift. Options: track it and accept manual syncing, replace it
with a one-line pointer to `CLAUDE.md`, or delete it. Left alone pending the user's call —
it is their file.

## Where results go

`docs/validation.md` covers the GEX engine. Scan analytics get their own
`docs/validation-scan.md`, started by T43 and appended by T45, T48 and T50, holding the
hand-checked fixtures and every place the implementation deviates from a textbook definition.
