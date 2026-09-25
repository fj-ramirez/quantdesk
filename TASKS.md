# Open tasks

Only work that is **not finished** lives here: open, partial, blocked or conditional. The full
history T00–T107, including every finished block and its *Done* note, is frozen in
[docs/archive/TASKS-T00-T107.md](docs/archive/TASKS-T00-T107.md). The durable decisions those
tasks made — the things a later task must not quietly undo — are indexed in
[context/decisions.md](context/decisions.md). Read that before changing behaviour an earlier
task settled.

How to use this file:

- Each block is self-contained. Give the agent the block plus the standing preamble: *"Read
  PLAN.md first. Work only inside the paths listed. Do not change the public interfaces defined
  in earlier tasks. Run the tests before reporting done."* and the hard rules in
  [context/workflow.md](context/workflow.md).
- Block shape: `ID · Model · Depends on`. Opus for new pure-math modules, subsystems and review
  passes; Sonnet for views, ingestion against a spec and additive wiring.
- **Paths inside blocks filed before T75 (2026-09-19) predate the module host.** `app/scan/…`
  is now `app/modules/gex/scan/…`, `app/api/…` is `app/modules/gex/api/…`,
  `frontend/src/pages/…` is `frontend/src/modules/gex/pages/…`. Resolve them before dispatching.
- **When a task lands:** record the result under the plan file's *Result* heading, move the
  block to the archive's addendum under that day's date, and add one line to
  `context/decisions.md` if it settled a rule. A finished block does not stay here.
- New work gets the next free ID and is filed here, never fixed silently.

**Next free ID: T128.** (T108 was allocated retroactively — see the archive's addendum;
T109–T114 were filed on 2026-09-22 from the task-history audit, T115–T121 from the logic audit;
T122–T124 were filed and finished on 2026-09-23 — see the addendum; T125–T127 were filed on
2026-09-25 for the ThetaData initiative, [plans/thetadata/](plans/thetadata/README.md).)

---

## ThetaData (Options Standard, bought 2026-09-24)

> Initiative: [plans/thetadata/README.md](plans/thetadata/README.md). Options only — no Stocks,
> no Indices. **The homeserver owns the terminal session**; dev never starts one implicitly.

### T125 · Sonnet · —
**Theta Terminal service, probe, and the budget decision on record**

`theta-terminal` in `compose.prod.yaml` (always on at the homeserver), a dev opt-in file,
`THETADATA_*` settings, and `python -m app.modules.gex.providers.thetadata <SYMBOL> <DATE>`, which pulls one SPX and one SPY session and
reports row counts, the OI date semantics, and whether `underlying_price` is filled for SPX
without an Indices subscription. Update `PLAN.md` §1 and `context/data-and-ops.md` (the budget
line). Acceptance: the probe's output is recorded under the plan's *Result*.

### T26 · Sonnet · T125
**ThetaData history loader**

`app/modules/gex/providers/thetadata.py` (a client for the v3 REST API that normalizes to
`ChainSnapshot`) plus `python -m app.modules.gex.gex.history --symbols SPX,SPY --start --end`:
one snapshot per session built from greeks/EOD(D) + OI reported on D (= D−1 close), written
through the same Parquet + `compute_and_store` path as a live capture, `source='thetadata'`,
`is_eod`, `session_date=D`. Skip sessions that already have an EOD snapshot. Resumable; run
outside capture hours. Rules 2–4 of the plan are its acceptance tests.

### T126 · Sonnet · T26
**`PROVIDER=thetadata` for live captures**

`fetch_chain` from the snapshot endpoints (quote + OI + greeks), `delayed_minutes=0`. Cboe stays
the default until a week of side-by-side captures agrees; the switch is a config change.

**Partial (2026-09-25):** the code landed, together with the live 0DTE route the Explorer
polls. Prod was already on `PROVIDER=thetadata`, so the side-by-side week was skipped. Open:
first live run on the homeserver, then check the columns and SPX spot. See
`plans/thetadata/README.md`.

### T127 · Opus · T26, T115, T116, T117
**Replay the decision engine over the loaded history**

Run `scan/decisions` and `scan/outcomes` over each historical session's levels and bars,
writing to a research-owned table — **never `gex.decisions`**. Its purpose is to give T112 an
out-of-sample record years deep instead of waiting for ~100 live resolutions. Gated on
T115–T117 for the same reason T112 is: the outcome logic is known to be wrong until they land.

### Real-time (re-pointed from Tradier to ThetaData)

> These were blocked on a funded Tradier account. ThetaData Standard streams real-time OPRA
> quotes, so T22 becomes a ThetaData streaming provider and the Tradier account is no longer
> needed. The spec below is the original; read "Tradier" as "ThetaData stream" and the
> `StreamingProvider` interface stands. Still no index feed: SPX spot comes from the option
> chain (parity forward, T33), not from a quote.

### T21 · Opus · T07, T08, T18
**Real-time recompute design**

Write `docs/realtime.md` before any code: how the day's OI is frozen at the first capture, how streaming quotes update spot and per-contract IV, how IV is derived when the stream provides only prices (Newton or Brent solve against T07 pricing, with fallbacks), recompute cadence (target 2–5 s, throttled), memory layout (NumPy arrays keyed by contract index), and what happens on reconnect or gaps. Define the `StreamingProvider` interface: `subscribe(contracts)`, `async iter_quotes()`. State the subset of contracts to stream (e.g. strikes within ±7% of spot for expiries within 45 days) to stay under vendor limits (ThetaData Standard: 10k streamed contracts).

### T22 · Sonnet · T21, T126
**ThetaData streaming provider** (was: Tradier provider)

Implement `StreamingProvider` from T21 over the Theta Terminal's stream. Reconnect with backoff; heartbeat; metrics counters. Tests with recorded fixtures; a live smoke script that streams 30 seconds and prints message counts.

### T23 · Opus · T21, T22
**Real-time GEX loop**

Implement the design from T21 in `backend/app/modules/gex/gex/realtime.py`, in a worker (invariant 7), never the API process. Publish results through the T19 SSE channel with an event type `realtime`. Frontend: when a realtime stream is active, charts update in place without flicker (ECharts `setOption` with `notMerge=false`). Config flag `REALTIME_ENABLED`.

Acceptance: during market hours, the dashboard updates every few seconds and CPU stays under one core.

### T24 · Opus (review) · T21–T23
**Phase 5 review**

Review the IV solver for stability on deep OTM and 0DTE contracts, reconnect behaviour, and that frozen OI is the previous day's (not stale from two days ago on a Monday). Fix or file.

---

## GEX — engine, capture and views

### T33 · Opus · T07, T08, T10
**Fit the carry term from put-call parity instead of guessing it**

`docs/validation.md` §6.2 established, from put-call parity on real market quotes, that our carry is mis-set: parity implies `r − q ≈ 3.6 %` against our configured `4.0 % − 1.3 % = 2.7 %`. This is worth roughly **8 % of SPX net GEX** and is the single largest source of the remaining gap against the one vendor that corroborates our magnitude (ours +48.29 B vs their +43.6 B — an 8 % correction lands almost exactly on their figure). It is a mis-set parameter, not a bug, which is why T10 correctly did not "fix" it by tuning.

- Derive the implied forward per expiry from put-call parity on liquid near-the-money pairs, and use it in place of a single global `RISK_FREE_RATE − DIVIDEND_YIELD`. The engine already prices SPX on the forward, so this is a change of input, not of model.
- Fall back to the configured constants when an expiry has no usable pair (wide spreads, no volume, deep-dated).
- Keep it a pure function in `backend/app/gex/`; no I/O.
- Report the before/after net GEX for a live SPX chain and re-run the T10 comparison rows.

Acceptance: parity residual on the fitted expiries drops materially; net GEX moves toward the vendor figure; a regression test pins the fitted forward for a stored fixture.

### T31 · Sonnet · T02, T05
**Alert on partial-chain degradation**

Providers skip-and-log unparseable contracts, which is the right policy, but the only signal is a WARNING. If Cboe renames a root or changes the OCC format, 30 % of SPX could vanish from every snapshot with GEX quietly wrong and captures still reporting `ok=True`. (T06 fixed the 100 %-skipped case, which now raises.)

- Carry `skipped` and `listed` out of the provider on the snapshot and into `CaptureResult`, the structured log line and the `snapshots` row.
- Log at ERROR when skipped exceeds a small threshold (e.g. 1 %), and flag any capture whose contract count deviates more than ~30 % from that symbol's trailing median.

### T28 · Sonnet · T05
**Ops**

Backup script for `DATA_DIR` and a `pg_dump` cron; a `/api/health` that reports last successful capture per symbol and alerts (log at ERROR) if the EOD capture is more than one trading day old.

> **Partial.** `scripts/db-dump-push.sh` exists, capture health is T29's
> `/api/gex/health/capture`, and the alerting half is T104's `capture-watch`. What remains is
> a scheduled backup (Parquet `DATA_DIR` plus `pg_dump`) that runs without anyone remembering.

### T17 · Opus (review) · T11–T16
**Phase 3 review**

Review API/frontend contract consistency, error handling, and whether URL state fully drives the views (deep links work). Check that every chart uses the same sign convention and units. Fix small issues directly.

> Never run. Much of the surface has changed since (T62–T69, T75); scope it to the current
> app rather than the 2026-09-04 one.

### T20 · Sonnet · T18, T14
**Intraday timeline view**

Page `/intraday`: for a chosen date, a chart of flip point, call wall, put wall and spot across the session's snapshots, plus a slider to scrub the GexByStrike chart through the day's snapshots.

> T18 and T19 shipped; this is the remaining page of the Phase 4 tier. Full spec in
> [plans/continuous-feed/02-intraday-polling.md](plans/continuous-feed/02-intraday-polling.md).

### T72 · Sonnet · T19
**Live-spot overlay against a frozen surface**

The increment the original roadmap missed, and the best value-per-hour in the initiative: free,
no vendor, no account, no licence. OI is fixed for the session by construction and IV moves
slowly; spot is the fast input, and spot is not OPRA data. So freeze the whole surface at the
last 15-minute capture and move only spot, giving a live answer to "how far am I from the flip
point right now". Distance-to-level is arithmetic on `gex_levels` rows that already exist; net
GEX at live spot interpolates the 201-point `gamma_profile` that `compute_all` already computes
and currently discards - cached in process, never recomputed on a tick.

The decision the task turns on: **two clocks, shown as two clocks** ("spot 14:32:05 ·
structure 14:15"). A smoothly moving marker invites the reading that the walls are live too,
and they are not - which matters most on exactly the fast tape where the view is most wanted.

Acceptance: feeding a changed spot moves the marker and the distance readouts without a new
capture, the structure stamp does not move, and killing the quote source drops back to the
captured spot with a visible reason.

### T57 · Sonnet · T50, T51
**Label the in-progress week on the rotation page**

Found while verifying T50 live on 2026-09-09 (a Wednesday). `/api/scan/rotation`'s newest
trail point is dated **`2026-09-11`** — the coming Friday, a date that has not happened — and
its value is computed from Wednesday's close. `weekly_closes` resamples `W-FRI` and labels each
bin with its week-*ending* Friday, which is correct for a completed week and becomes a
future-dated, partial-week point for the current one.

Nothing in the response or the page says so. Grepping `rotation.py`, `api/scan.py` and
`Rotation.tsx` for "partial", "in-progress" or "to date" finds only unrelated matches about
rolling-window warm-up.

An RRG tail whose head moves during the week is normal and *should* keep updating — the bug is
not the value, it is presenting it under a future date with no marker. Nor should it be dropped:
the current week is the most decision-relevant point on the chart.

**Do:** carry a per-point (or per-response) flag saying the newest week is still open, and in
the UI render that week's label as the week's own range or "week to date" rather than a bare
future Friday. Reuse T34's freshness vocabulary rather than inventing a second one; the same
question ("as of when, honestly?") already has an answer in this codebase.

Paths: `backend/app/scan/rotation.py` (the flag, still pure — derive it from the data's own last
daily date versus the bin's Friday, never from `datetime.now()` inside a pure module),
`backend/app/api/scan.py`, `frontend/src/pages/Rotation.tsx`, `frontend/src/api/types.ts`, tests
both sides, `docs/validation-scan.md`.

Acceptance: a fixture whose last daily bar is a Wednesday marks that week open and renders it as
a range/"to date"; a fixture ending on a Friday close marks nothing open; the numbers themselves
are unchanged from today's (assert against the existing fixtures); `uv run pytest`,
`ruff check .`, `npm test`, `npm run lint` and `tsc -b` all pass.

**Verified while filing this (2026-09-09):** T50's math itself is correct. `rs_ratio_approx` and
`rs_momentum_approx` were recomputed by hand from `/api/bars` for XLK, XLE, XLU and XLRE over
the last three weeks and matched the API to **1.3e-13** on both coordinates, including the
`ddof=0` convention `_rolling_zscore` documents. This task is about the label, not the number.

### T58 · Sonnet · T52, T53
**Daily flow series endpoint, so the flows sparkline is the one 07-ui.md specifies**

Found while reviewing T53 (2026-09-10). `07-ui.md`'s `/flows` spec asks for "a sparkline of
**cumulative flow over 60 days** per fund". The only endpoint that exists,
`GET /api/scan/flows?window=`, returns **one aggregate per window** — there is no daily series
behind it, and building one is backend work, which T53 was scoped out of.

T53 shipped `FlowSparkline` plotting the three real window aggregates (5d/20d/60d) instead,
documented in its own docstring and flagged in its report rather than passed off as the
specified widget. That is the right call for a frontend-only task and the wrong end state: three
points spaced by window length is a different object from a 60-point cumulative series, and it
cannot show *when* money arrived, which is the whole point of the sparkline.

**Do:** add a daily series to the flows API — `GET /api/scan/flows/{symbol}/series?days=60`, or a
`series` field on the existing response, whichever fits `app/api/scan.py`'s existing shape better
— returning per-day `flow` and cumulative flow from `etf_shares_outstanding`. `app/scan/flows.py`
already computes per-day flows internally on the way to its aggregates (`flow_t = (SO_t −
SO_{t−1}) × NAV_t`); this is largely exposing what it already derives, not new math. Then point
`FlowSparkline` at it.

**The honest constraint that shapes this:** there is no backfill. The table accumulates from the
day T52's job first ran (2026-09-09), so a 60-day series will not exist until roughly December
2026, and until then the endpoint must return what it has with `history_since` rather than
padding. Do not build this expecting full sparklines on day one — build it so the sparkline fills
in correctly as history accrues, and so the short-history case is what it renders today.

Paths: `backend/app/scan/flows.py`, `backend/app/api/scan.py`, `frontend/src/components/flows/
FlowSparkline.tsx`, `frontend/src/api/types.ts`/`queries.ts`/`client.ts`, MSW fixtures, tests both
sides, `docs/validation-scan.md`.

Acceptance: the series endpoint returns per-day and cumulative flow for a symbol with history and
an honest short-history response for one without; `FlowSparkline` renders a real cumulative series
where one exists and the short-history state otherwise, never a padded or interpolated line; a
hand-built fixture's cumulative values match a hand computation; `uv run pytest`, `ruff check .`,
`npm test`, `npm run lint` and `tsc -b` all pass.

### T109 · Sonnet · T47
**Extended-symbol EOD safety net**

Filed 2026-09-22 from an unnumbered TODO in the archive (T47, 2026-09-09).
`catch_up_missed_eod` (`app/modules/gex/jobs/catchup.py`) covers only `settings.symbols`. A
missed 16:45 extended capture has no catch-up at all — it is gone until the next trading day's
run, unlike the core five, which get both the 20:00 safety net and the startup catch-up.

T47's guardrail still holds: **nothing may risk delaying or altering the core EOD capture's
safety net.** So the fix is a *second*, extended-only safety-net job — its own id, its own
trigger, no shared code path with `capture_eod_safety_net_job`, the same shape as
`capture_extended_job`. Re-check first whether T104's `capture-watch` or T102's `session_date`
changes the picture.

Acceptance: with the clock faked past the extended safety-net time on a trading day and no
extended snapshots for that session, the job captures them; the core job's schedule, id and
code path are unchanged (assert it).

### T110 · Sonnet · T73
**Same-session VIX family**

Filed 2026-09-22 from T73's *Still open* note. The 08:15 pre-open bars run means the VIX family
is at worst one session behind, not two, but the Cboe CSV may not carry day D during day D. If
the regime strip should show *today's* VIX, those six need a different source (Yahoo serves
`^VIX` and `^VIX3M`, not obviously the whole family). The source decision is the task; a
documented "not worth it" closes it.

### T111 · Sonnet · T74
**Wire the in-progress daily bar and the 5-minute series into the UI**

Filed 2026-09-22 from T74's *Next* note. `GET /api/gex/bars/{symbol}/intraday` and the typed
`session_bar` exist; the frontend still reads only the settled daily series. Surface
`session_bar` in the symbol views — typed and labelled so it can never pass as a settled bar —
and chart the 5-minute series. `daily_bars` stays untouched (T74's decision).

### T112 · Opus · T61, T115, T116, T117
**Calibrate the decision engine against its own track record — gated**

Filed 2026-09-22. T61 called this "a T62 candidate", but T62 went to the UI refresh, so it never
had an ID. **Do not start until roughly 100 decisions are resolved** (`result_r IS NOT NULL`;
read the count from `gex_track_record`, never from a doc). Then re-fit
`FADE_STOP_BUFFER_ATR`, `VOLATILITY_STOP_ATR`, `MAX_HOLD_BARS` and the score weights against
`result_r`, `mfe_r` and `mae_r`, per key, with out-of-sample discipline. Until then, no
threshold moves. The resolved count only means something once T115–T117 land: until then
the outcome columns it would fit against are known to be wrong.

### T113 · Sonnet · T92
**`variance_ratio` raises on a flat close series**

Filed 2026-09-22 from T92's result
([plans/decision-inputs/02-relative-volume.md](plans/decision-inputs/02-relative-volume.md)).
`variance_ratio` raises `ZeroDivisionError` on a perfectly flat close series, which breaks
`score_symbol`'s "never raises" contract. The answer for zero variance is *undefined*, so it
should be `None`/NaN, never `0` or `1`. Add the flat-series test first.

---

## Platform and modules

### T81 · Sonnet · T78, T80

Launcher and module shell: a card per module showing its current state and health, plus the
module switcher. Spec: [plans/quantdesk/04-launcher-shell.md](plans/quantdesk/04-launcher-shell.md).

> **Partial.** Commit `32dabe7` shipped the design half (launcher page, module chrome). There
> are no `GET /api/<module>/summary` endpoints and no in-frame module switcher, so no card
> reports whether its module is healthy — which the spec calls the whole point of the card.

### T83 · Sonnet · T76

`alembic heads` and `alembic history` die with `ModuleNotFoundError: No module named
'app.models'`. Two frozen revisions import the pre-T75 path; T75's alias for them lives in
`alembic/env.py`, which those two commands never run (`upgrade`, `downgrade`, `current` and
`revision` do, so containers and CI are unaffected — this is a developer-facing wart only).

Found during T76, logged rather than fixed silently. The fix must not edit
`alembic/versions/**`: a migration records what was applied to a real database, and rewriting
one to match code that did not exist when it ran is how the next rename earns the identical
edit. Options worth weighing: a deliberate, documented `app/models/__init__.py` compatibility
shim; or teaching the two commands to load `env.py` first. Whichever lands needs a test, since
the unit suite never invokes those subcommands.

> **ID collision.** Commit `808dee9` is subjected "T83: score the paper watchlist" — unrelated
> work, now recorded retroactively as **T108**. This block is still open.

### T84 · Sonnet · T79

Port the xactx test suite. `test_board.py`, `test_brief.py`, `test_point_in_time.py`,
`test_loader.py`, `test_graph.py`, `test_derive.py`, `test_policy.py` and the rest are built on a
`Store(tmp_path / "test.duckdb")` fixture and did not come across in T79. They need a fixture
that gives each test a throwaway Postgres schema (create, `alembic`-less `create_all` from
`tables.py`, drop), plus the `_needs_pg` skip guard `tests/test_terminal_store.py` already uses
so the offline suite stays offline.

This is the honest gap in T79 and should not sit for long: those tests encode the loader's
same-vintage-two-values refusal, the derive unit checks and the graph's sign-conflict logic, and
none of that is currently covered in this repo. What T79 does cover is `translate_sql` (the new
code) and the point-in-time invariant against the real migrated data.

### T85 · Sonnet · T80

`GET /api/terminal/brief` writes to the database. The ported `brief.section_affects` calls
`graph.register()` and `graph.estimate_all()`, so generating the brief registers edge definitions
and recomputes the whole transmission graph. That was reasonable when `brief` was a CLI command
run after ingesting; for an HTTP GET it means the endpoint cannot be cached, cannot be served
from a replica, cannot be read by the `quantdesk_ro` role, and re-estimates the full panel on
every page load.

Fix in `brief.py`: `section_affects` should read stored `edge_stats` at the requested `as_of`,
exactly as `app/modules/terminal/api/edges.py` already does. Then change the endpoint's
`connect(read_only=False)` back to `read_only=True` -- the comment there marks the spot.

### T114 · Sonnet · —
**Code-side drift found by the 2026-09-22 doc audit**

Three places where code, not docs, carries a stale claim. Each is small; they share a task so
they land together.

- `scripts/_common.sh:24` sets `APP_SERVICES` without `capture-watch`, so `db-restore.sh` does
  not stop or restart it while restoring a database it queries. Add it, and check every other
  consumer of `APP_SERVICES`.
- `app/modules/gex/jobs/catchup.py:189` still says it is `asyncio.create_task`d from
  `app/main.py`'s lifespan. There has been no lifespan since T75; it runs in the `gex-capture`
  worker.
- `app/mcp/server.py:8` says "The ten tools below". There are eleven.
- `scan/decisions.py`: `TARGET_FALLBACK_ATR`'s comment says a 2-ATR target "exactly clears
  `MIN_REWARD_RISK`", but that floor is 1.0, and the next comment calls 2:1 "the conventional
  floor" the engine does *not* use. Make the two comments agree with the constant (audit, low).

Acceptance: the three edits, plus a test for the first — ideally one that derives the list of
database-reading services from `compose.yaml`, so the next worker cannot be forgotten the same
way.

---

## Terminal and decision inputs

### T94 · Sonnet · T91

Sector-level transmission edges. The graph's four equity nodes are all index-level; every trade
the desk makes is in a sector or industry ETF, all of which are already in `daily_bars`. Spec:
[plans/decision-inputs/04-sector-edges.md](plans/decision-inputs/04-sector-edges.md).

### T95 · Sonnet · —

Crude term structure: `cmdty.wti` is a single series, so contango versus backwardation is
unanswerable. The source survey is the task, and a negative result closes it. Spec:
[plans/decision-inputs/05-crude-term-structure.md](plans/decision-inputs/05-crude-term-structure.md).

### T96 · Opus · T90

Event calendar and the implied policy path. `terminal.releases` has 0 rows; `policy.py` is
finished, tested, and has never produced a row because its input is a hand-supplied CME file
that their terms forbid fetching. Calendar first (free sources, solved problem), then an OIS
source survey. Spec:
[plans/decision-inputs/06-calendar-and-policy-path.md](plans/decision-inputs/06-calendar-and-policy-path.md).

---

## Research

### T25 · Opus · T09, T18
**Backtest harness**

`backend/app/research/`: load all EOD levels and next-day OHLC; compute for each day whether the next session's range stayed within call wall / put wall, distance to flip vs. realized range, and 0DTE-only vs. all-expiry level quality. Output a Markdown report and CSV. Notebook optional. Design the module so a ThetaData history loader can be added later without changing the analysis code.

> **Now fed by T26** (2026-09-25): years of EOD levels instead of weeks of self-capture, so this
> is worth running. Largely overtaken by EdgeLab (T77). Before dispatching, decide whether a GEX-level backtest
> still belongs here or as an EdgeLab strategy family; a documented "superseded" closes it.

---

## Audit findings (2026-09-22)

Filed from [docs/audit-2026-09-22.md](docs/audit-2026-09-22.md). **Dispatch order:** T115 first (it
is critical and blocks T112), then T116 and T117; T118 before T109; the rest are independent.

### T115 · Opus · —
**Fade outcomes must not exit at a price printed before the fill — audit A1, critical**

`scan/outcomes.evaluate` starts the hold loop on the trigger bar (`outcomes.py:236`), checks the
target against that bar's whole range (`:244`) and exits at `min/max(target, open)` (`:267`). For
a resting limit that did not gap, the open printed *before* the fill. All five scored fade wins
(ids 19, 24, 28, 41, 47) are this case; XOP 19 "exited" at a 194.7 open and then filled at 200.
`mfe_r`/`mae_r` include the pre-fill excursion the same way (`:238-241`).

**Judgment calls, name them and argue them:** (1) the same-bar rule for a non-gapped limit fill
— the audit re-scored with "only the stop can resolve on the trigger bar, resume next bar"; a
stricter or looser rule needs its reason. (2) What happens to **already-resolved** rows. The
decision rows are append-only (T61), but `outcome`/`result_r` are an evaluation of them, and
leaving a known-wrong evaluation in place is how the record keeps lying. The plan must say
whether resolved outcomes are re-evaluated, and how that is recorded.

Acceptance: a test with the target inside the trigger bar's range for a non-gapped fill (long
and short), a gapped-fill test proving the open is still a legal exit there, and an MFE test.
The audit's re-score of the five rows is reproduced, or the difference argued. `gex_track_record`
before/after is recorded in the result. Also blocks T112 — calibrating against these columns
would fit the bug.

### T116 · Opus · T115
**Score a position once, not once per re-emission — audit A2**

A fade's resting limit is re-emitted every session while its wall stands, and each emission is
scored. 19/41 (XOP) and 24/47 (USO) are one order each; eleven same-symbol, same-key pairs of
scored rows overlap in time. `gex_track_record`'s `se` treats them as independent. Decide the
identity of "one trade" (symbol + key + entry while an earlier emission is live is the obvious
start), mark re-emissions append-only rather than deleting them, and make the record and its
`se` count independent positions. Say whether T93's cross-symbol factor marks should also reach
the record, and if not, why.

### T117 · Sonnet · —
**Keep mislabelled history out of the track record, append-only — audit A3**

T99 kept `gex.decisions` append-only, correctly, so its six wrong-sign rows stay in history. 23
and 32 are scored under keys whose thesis they contradict; 50, 77, 78 and 80 will be when they
resolve. Add an append-only annotation — e.g. `gex.decision_annotations(decision_id, kind,
reason, created_at)` — and have `gex_track_record`, the decisions API's summary and T112
exclude `kind = 'excluded_from_record'`, reporting how many were excluded and why. The six
`(id, key)` literals are in T99's test; reuse them.

### T118 · Sonnet · —
**Key "is this session covered?" on `session_date` — audit B1**

`catchup.has_eod_snapshot_today` (`jobs/catchup.py:79-98`) checks `captured_at`'s NY date. But
`captured_at` is the vendor's timestamp, and a thin name can be stamped before the open (XBI on
09-22: 16:45 capture, chain stamped 07:18, correctly `session_date = 09-21`). That row satisfies
the guard while covering yesterday, so the 20:00 safety net would not retry. Key it on
`session_date = today`. T109 must use the same predicate; do this first. Test: a fixture whose
16:20 chain carries a pre-open timestamp is retried by the safety net.

### T119 · Sonnet · —
**`desk_status` misstates `captured_at` — audit F1**

`mcp/server.py:368` tells every caller "`captured_at` is the wall clock". It is Cboe's
`timestamp` (`providers/cboe.py:293`). Fix the note, and check every other surface that
describes the column — `context/`, the market-research skill, `quantdesk://schema`. Consider,
and argue for or against, a `fetched_at` column: today nothing records when the desk actually
asked for a chain, which is what an operator needs when a symbol goes quiet.

### T120 · Sonnet · T85
**The brief's counts must respect `as_of` — audit E1**

`brief.py:322` (policy-path trade dates), `:530` (observation total) and `:558` (provenance)
read `terminal.observations` with no `as_of` filter, so a past-dated brief counts later vintages
and the first can flip the "Implied policy path" section between available and not. Filter all
three on `as_of <= ?`. Test: a brief generated at an `as_of` before a later vintage produces the
same text before and after that vintage is loaded. Pairs naturally with T85, which touches the
same function.

### T121 · Sonnet · —
**`Registry` refuses a non-Postgres database — audit D1**

Invariant 9 says the registry raises rather than forking into SQLite; `Registry.__init__`
(`research/registry.py:96-112`) accepts any dialect. When no `session_factory` is injected
(production), raise unless the engine's dialect is `postgresql`. Tests inject their own and are
unaffected; add one proving a `sqlite://` URL raises.
