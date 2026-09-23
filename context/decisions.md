# Decisions

The durable rules earlier tasks settled: each is a judgment call that a later task could
plausibly "fix" and would be wrong to. One line each — the rule, why, and where the argument
lives. The full history is [docs/archive/TASKS-T00-T107.md](../docs/archive/TASKS-T00-T107.md);
open work is [TASKS.md](../TASKS.md).

**This file holds rules, never state.** No counts, no test totals, no "currently", no track
record. Numbers written into an agent-facing file rot and are then quoted with confidence
(`F7` in [state-review-2026-09-21](../docs/state-review-2026-09-21.md)). If a line needs a
number to be true, it belongs in a plan file's *Result* heading or behind an MCP tool.

`A` = the archive; `P:` = a plan folder under [plans/](../plans/README.md).

## GEX engine and Greeks

- The engine and Greeks are pure — no HTTP, DB, filesystem or logging. — CLAUDE.md invariant 1; T07.
- The dealer sign is attached once to the contract frame and consumed from there; no value is
  ever re-signed. `greeks.gamma()` is unsigned. — invariant 2; T08.
- Implied vol is stored as a decimal. Cboe's per-contract `iv` is already decimal: never divide
  by 100. `iv == 0.0` means unknown → `None`. — T01, T02.
- Time to expiry is calendar days / 365 with a floor; SPX is priced Black-76 on the forward. — T07.
- SPX is AM-settled monthly; SPXW is PM-settled. — T01; [docs/schema.md](../docs/schema.md).
- Gamma is recomputed from our own inputs; vendor gamma is used only to cross-check. — T08.
- **Never tune the engine to match a vendor.** Fix only differences traced to a cause. — T10;
  [docs/validation.md](../docs/validation.md).
- Carry is a known mis-set input, fixed by fitting it from put-call parity, never by tuning. — T33 (open).
- **A wall is named by its gamma sign, never by its position relative to spot.** A put wall is a
  negative-gamma strike, unconditionally. — T99; P:desk-integrity.
- `WALL_MIN_ABS_FRACTION = 1e-4`, measured: `1e-2` would have nulled a real 0DTE wall. — T99.
- Spot resting on the largest positive-gamma strike is a pin (`GAMMA_PIN`), not a wall. — T99.
- Levels are stored at capture time for `ALL`, `ZERO_DTE` and `EX_ZERO_DTE`. — T09.

## Nulls and honesty

- **Unknown is `None`; zero is `0`. Never collapse them** — open interest (invariant 3), empty
  aggregates (T100), forward stats (T108), RSS deltas (T88), every wire type and every chart.
- An empty aggregate is `NULL`, not `0`: "dealers are flat" is a claim, not a default. — T100.
- A neutral ratio is a measurement, not a missing value; don't report it as absent. — T103.
- Screening output is labelled as screening output; a playbook is not a recommendation. — T40.
- Staleness is shown honestly: `effective_at` clamps to the close, and "as of" comes from the
  data, never from the wall clock. — T34.
- "No data yet" is an empty state, not an error; never show raw JSON. — T37.
- Never fake a series. Plot the real aggregates you have, and say so. — T53, T58.

## Capture and data integrity

- A capture failure never crashes the scheduler. A fully-skipped chain raises. — T05, T06.
- Catch-up plus a 20:00 safety net, keyed on the existence of an `is_eod` row — which must mean
  one whose `session_date` is today, not one whose timestamp falls today. — T29; T118 (open).
- **Nothing may risk the core EOD capture or its safety net.** Extended work gets its own jobs,
  ids and code paths. — T47; T109 (open).
- Dedupe on a content hash, never the vendor timestamp; a duplicate promotes `is_eod`. — T71.
- `session_date` (the session the chain belongs to) is distinct from `captured_at`, which is
  the **vendor's** timestamp on the chain, not when we fetched it. Group and compare by
  `session_date`. — T102.
- A missed intraday slot is unrecoverable on the free source, so intraday is opt-in
  (`INTRADAY_ENABLED` off by default) with a short misfire grace. — T18, T70.
- Publish to the stream only after the levels commit. — T19.
- `daily_bars` holds settled sessions only: no partial row, ever. The in-progress bar is
  aggregated at read time and typed so it cannot pass as settled. — T42, T74.
- Yahoo's trailing intraday row is a synthetic live quote; split it out, never store it. — T74.
- Issuer flow rows are keyed on the issuer's as-of date, never the run date. — T52.
- Scrapers never go through login or anti-bot pages; failures are per symbol. — T59.
- New capture symbols need no migration. — T38.

## Storage and schemas

- Postgres holds computed results and a snapshot index; per-contract rows live only in Parquet. — invariant 5; T04.
- `parquet_path` is relative to `DATA_DIR` and resolved by one helper. — invariant 5; T30.
- Expiry is stored as a rollup (horizon columns + `gex_by_expiry`), never per contract. — T101.
- Retention: EOD strike detail, levels, snapshots and Parquet are kept forever; intraday strike
  detail expires. — T32.
- One schema per module, declared on the module's `Base.metadata`, never per model. — invariant 8; T76.
- The read-only role's privileges live in a migration; its password is applied at boot. — T76.
- Migrations under `alembic/versions/` are never edited after they have run. — T83 (open).
- Decisions are **insert-when-unseen, never upserted**; paper scores and terminal vintages are
  append-only for the same reason: the history is the evidence. — T61, T108, invariant 10.

## API and frontend

- The API process starts no background work; every clock-bound job is its own container. — invariant 7; T75.
- Only `/health` sits outside a module prefix; it is the container healthcheck's contract. — T75.
- The app starts without a database; blocking work stays off the event loop. — T35.
- URL search params are the global state, not a store. Terminal `as_of` is URL state in the
  module frame, and a pinned past moment is always flagged. — T12, T80.
- Gamma profile always shows All + Ex-0DTE; its axis never forces zero. — T14, T36.
- The CFD ratio comes from two user-entered spots and is never stored; GEX, premiums and IV are
  never converted. — T41.
- UI refreshes keep every piece of information and change only its presentation;
  compact first, heavy sections collapsed. — T62, T69.
- The build is stamped per service, never stack-wide; unknown says `unknown`. — T98.

## Decisions and scoring (GEX)

- Scan and decision logic lives in pure modules. — T43, T60.
- ATR multiples are uncalibrated named constants; `no_trade_reasons` is non-empty exactly when
  nothing is emitted. — T60.
- The stop is checked before the target on the same bar, and nothing is credited from before
  the fill: a limit's trigger bar cannot exit at its own open. — T61; T115 (open).
- **No threshold moves before ~100 resolved decisions**, and then only by the calibration task. — T61; T112.
- Resolved means `result_r IS NOT NULL`; `untriggered` is not a loss. — T106.
- The factor cap marks and explains, never removes; correlation is side-adjusted. — T93.
- Relative volume excludes the current bar from its own baseline, and is not in the composite. — T92.
- Every regime verdict carries its reasons. — T48.

## Research (EdgeLab)

- The noise ceiling, doubled-cost gate, walk-forward gate and roll-gap caveat travel with every
  result. The ceiling is in the same payload as the rows, and its denominator is the whole
  registry, so filtering cannot lower it. — invariant 9; T77, T78.
- One trial registry, in Postgres; no SQLite fallback. — invariant 9; T77.
- The ported science stays diffable against the original (scoped lint ignores, not rewrites). — T77.
- Paper candidates are scored forward from `promoted_at`, one row per cycle. — T108.

## Terminal (xactx)

- A revision adds a row, never overwrites one; `as_of` is in the primary key and `as_of_basis`
  is per row. — invariant 10; T79.
- A pre-publication `as_of` returns nothing rather than approximating. — T79.
- The engine swap is a facade in `store/db.py`; SQL strings stay character for character. The
  terminal connection's own `search_path` is the one deliberate exception to invariant 8. — T79.
- One dead source never stops the ones after it, and the run still exits non-zero; the per-step
  guard catches `SystemExit`. — T90, T97.
- Graph nodes with no series are fed from data already captured, through a named cross-module
  adapter, never a second ingest. — T91.
- A GET never writes (T85 is the open violation). — T85.

## MCP connector

- `quantdesk_ro` is the safety boundary; the SQL guard is a courtesy. — T82.
- Caveats are payload: the tool that returns a number returns the rule that qualifies it. — T82, T106.
- Latest-per-symbol by default; the notes stay. — T105.
- Keys are derived from the data (`ROLLUP`), so a new signal appears without a code edit. — T106.
- **A hardcoded result is deleted, never updated** — updating it re-arms the trap. — T107.

## Ops and scope

- Analysis only — **no order routing, ever**. Single user, no auth, data spend under $50/month.
- The desk is never exposed publicly: loopback plus a private tunnel. — T70.
- Every service has a memory limit; `MALLOC_ARENA_MAX=2`. — T86.
- Alert on a universe-wide capture gap, never on ordinary per-symbol staleness. Delivery is
  opt-in and off by default, so the notifier cannot fail the way the thing it watches does. — T104.
- Reuse the in-hand snapshot only on a fresh write, never the duplicate path, so levels stay
  reproducible from Parquet. — T87.

## Process

- Findings are filed as numbered tasks, never fixed silently. A data-only review is a list of
  symptoms, not of fixes: confirm the cause in code before planning. — T06; P:desk-integrity.
- Verify the artifact, not the agent's report. Never run two Opus agents at once; never kill
  processes by image name. — [workflow.md](workflow.md).

## Initiative ledger

| IDs | Initiative | State | Plans |
|---|---|---|---|
| T00–T17 | Phases 0–3: scaffold, EOD ingestion, engine, API and dashboard | complete except T17 | A |
| T18–T20 | Phase 4: delayed intraday | T20 open | P:continuous-feed |
| T21–T24 | Phase 5: real-time (Tradier) | blocked | P:continuous-feed |
| T25–T26 | Phase 6: research | open / conditional | A |
| T27–T41 | Cross-cutting, review follow-ups, report view | open: T28, T31, T33 | A |
| T42–T56 | Continuation | complete | P:continuation |
| T57–T61 | Rotation label, flows series, flow sources, decision engine | open: T57, T58 | A |
| T62–T69 | UI/UX refresh | complete | P:ui-ux-refresh |
| T70–T74 | Continuous feed, bars | open: T72 | P:continuous-feed |
| T75–T85 | quantdesk module host | open: T81, T83, T84, T85 | P:quantdesk |
| T86–T89 | Capture memory | complete | P:capture-memory |
| T90–T97 | Decision inputs | open: T94, T95, T96 | P:decision-inputs |
| T98 | Per-service build stamps | complete | A |
| T99–T107 | Desk integrity | complete | P:desk-integrity |
| T108–T121 | Retroactive allocation, task-history and logic audit follow-ups | see TASKS.md | P:audit-and-compaction |
