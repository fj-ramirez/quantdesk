# 02 · Research module — EdgeLab — T77, T78

## Goal

Move `projects/research` (EdgeLab) in as `modules/research`: the search loop becomes a worker,
the SQLite registry becomes the `research` schema, the OHLCV parquet tree moves under
`DATA_DIR`, and the static `reports/leaderboard.html` becomes a real page.

EdgeLab's honesty rules are the product. The noise ceiling, the OOS-reuse caveat, the
pessimistic cost model and the futures roll-gap warning all survive the move and all appear in
the UI. A leaderboard that drops the noise ceiling is worse than no leaderboard, because it
looks authoritative.

## What the user sees

`/research` — a leaderboard table ranked by OOS Sharpe, with the noise-ceiling line drawn
across it and rows below it visibly discounted. Filters for market, strategy family, timeframe,
minimum trades and minimum exposure. A row expands to its parameters, its IS/OOS split and its
regime condition. A second tab lists the 23 paper candidates with their promotion date and
forward performance. A status strip: trials in the registry, cycles run, last cycle time.

## Data

```
research.trials             134,377 rows today, grows every cycle
  hash PK, run_date, market, strategy, symbol, timeframe, params(JSONB),
  is_sharpe, is_cagr, is_max_dd, is_fills,
  oos_sharpe, oos_cagr, oos_max_dd, oos_fills, oos_exposure, oos_bars, oos_years
research.paper_candidates   23 rows today
  hash PK, promoted_at, market, strategy, symbol, timeframe, params(JSONB),
  promoted_oos_sharpe, sharpe_2x, neighbor_med, wf_pos, wf_active, wf_med, corr_max

DATA_DIR/research/ohlcv/{crypto,forex,futures,stocks}/*.parquet    33 MB, moved as-is
```

Parquet stays parquet. This is the same split GEX already makes and invariant 5 already
states: Postgres holds computed results, bulk per-bar rows live on disk. Loading 33 MB of
OHLCV into Postgres would buy nothing the backtester wants — it reads whole series into pandas
and never queries a single bar.

## Design decisions

**`params` becomes `JSONB`, not `TEXT`.** It is a JSON string today because SQLite has no
better option. In Postgres, `JSONB` makes "every trial where lookback > 40" a query instead of
134,377 `json.loads` calls, and the trial hash — which is computed from
`json.dumps(params, sort_keys=True)` — is unaffected, because it is computed before storage
rather than derived from it.

**`run_date` and `promoted_at` become `TIMESTAMPTZ`.** They are `TEXT` today. Invariant 4 is
not negotiable for a new module; the migration parses them once.

**The hash stays the primary key, and the migration must not recompute it.** `trial_hash` is
a truncated sha256 over market, strategy, symbol, timeframe and the sorted-key JSON of the
params. The entire value of the registry is that a cycle never repeats work, and that
guarantee is exactly the guarantee that stored hashes still match what the running code would
compute. The migration copies hashes verbatim and the port must not touch `trial_hash`.

**The search runs in a worker container on its own APScheduler, not on `--loop 60`.** The
Windows Task Scheduler entry and the VPS systemd unit were the schedule; inside Docker neither
exists, and `--loop 60` is a `while True` with a sleep in it — no misfire policy, no overrun
protection, no way to ask what it is about to do. The worker uses the same
`build_scheduler()` shape as `app/jobs/scheduler.py`, which gives one scheduling mechanism in
the repo instead of two.

One job, and the trigger shape is configuration:

| `RESEARCH_SCHEDULE` | trigger | reproduces |
|---|---|---|
| `cron` (default) | `CronTrigger` from `RESEARCH_CRON`, default `0 2 * * *` in `settings.TZ` | the 02:00 nightly habit |
| `interval` | `IntervalTrigger` from `RESEARCH_INTERVAL_MINUTES`, default 60 | the VPS's `--loop 60` |
| `off` | nothing registered | a host that only ever runs cycles by hand |

Three policies on that job, each of which matters:

- `max_instances=1`. A cycle that overruns its next fire must never start a second alongside
  itself. Two concurrent searches would sample the same combinations and race each other's
  writes for no gain.
- `coalesce=True`. Four missed fires are one run when the worker comes back, not four.
- **No catch-up job.** GEX has one because a missed 16:20 capture is gone forever. A missed
  research cycle costs nothing — the registry already remembers everything tried, so the next
  cycle simply continues. Adding catch-up here would import a failure mode and buy nothing.

A cycle killed mid-flight (SIGTERM on redeploy) loses only its in-flight trials: `search.py`
writes per trial, so the registry is consistent at every instant and the next cycle picks up
from what is stored. That is a property worth keeping and worth a test.

**The Windows scheduled task stays, repointed.** It is not disabled — local runs remain a
first-class way to work. What changes is where it writes: it runs the ported module against
`DATABASE_URL`, exactly as the container does, so there is still one registry. See the
two-writers hazard below for the rule that makes this safe.

**Point the worker at Postgres by swapping the `Registry` class internals, not by rewriting
callers.** `registry.py` is a small class holding a path and a schema string. `search.py`,
`nightly.py` and `report.py` keep calling the same methods. Same rule as GEX's invariant 6
about providers: a storage swap is not an edit to callers.

**`Registry` has no SQLite fallback.** If `DATABASE_URL` is unset or unreachable, it raises.
This is the single rule that keeps the local Windows run and the container from forking the
search history: there is no path by which a failed connection quietly lands trials in a local
`registry.db` again.

**The static HTML report stays, for now.** `--report-only` still writes
`reports/leaderboard.html`. It costs nothing to keep, it works when the stack is down, and it
is the fallback while T78's page is being built. Drop it in a later pass once the page is
trusted.

## Tasks

### T77 · Opus · T76

Port EdgeLab into `app/modules/research/` against the `research` schema, with a one-shot
migration of the existing registry.

- Move `src/edgelab/*` to `app/modules/research/`, keeping module-internal structure
  (`backtest.py`, `search.py`, `strategies.py`, `validation.py`, `robustness.py`, `xs.py`,
  `paper.py`, `report.py`, `data.py`, `registry.py`, `config.py`).
- SQLAlchemy models for the two tables under `SCHEMA_RESEARCH`; Alembic migration creating
  them.
- `scripts/migrate_registry.py` — SQLite to Postgres, one-shot, idempotent, verifying row
  counts and a sample of hashes before and after. Leaves `registry.db` untouched.
- `config/research.yaml` moves to `backend/config/research.yaml`. Its knobs stay YAML rather
  than becoming environment variables: it is a search budget, not deployment config.
- `app/workers/research_search.py` — a `build_research_scheduler()` mirroring
  `app/jobs/scheduler.py`, plus a `research-search` compose service on the `internal` network
  only. It never serves a request. `Nice`-equivalent CPU restraint: give the service a
  `cpus` limit in `compose.prod.yaml` so a saturating search cannot starve the API, the way
  `Nice=10` did in the systemd unit.
- Keep `nightly.py` runnable by hand (`--trials N`, `--report-only`, `--loop N`). The worker
  and the CLI call the same cycle function; neither reimplements the other.
- **Repoint** the "EdgeLab Nightly" Windows scheduled task at the ported module against
  `DATABASE_URL`, and update `run_nightly.ps1`'s header with the re-registration commands and
  the note that Postgres must be reachable. Do not delete or disable it.
- Retire `deploy/edgelab.service`: the VPS now runs the whole quantdesk stack under its
  `compose@` unit, so a second standalone runner there would be a genuine fork. Keep the file
  with a header saying what replaced it.
- Requirements merge into `backend/pyproject.toml`: `ccxt`, `yfinance`, `PyYAML`. `pandas`,
  `numpy` and `pyarrow` are already there.
- Port the existing tests; add tests for the migration's hash fidelity, for both trigger
  shapes building without starting, and for `max_instances=1` refusing a concurrent cycle.

### T78 · Sonnet · T77

`/api/research/*` and the leaderboard page.

- `GET /api/research/leaderboard` — ranked, filtered, paginated; returns the computed noise
  ceiling alongside the rows, never as a client-side guess.
- `GET /api/research/trials/{hash}`, `GET /api/research/paper`, `GET /api/research/status`.
- `frontend/src/modules/research/` — leaderboard table, filter bar, paper-candidates tab,
  status strip. Reuse the existing GEX table and empty/error/loading components; do not invent
  a second visual language.
- The noise ceiling renders as a line with a one-sentence plain-English caption, and rows below
  it are visibly discounted. The OOS-reuse caveat appears on the page, not only in the README.
- MSW fixtures for all four endpoints.

## Verified facts

Measured 2026-09-19.

- 134,377 rows in `trials`, 23 in `paper_candidates`.
- `trials` has exactly one index: `idx_trials_oos ON trials (oos_sharpe DESC)`.
- `data/` is 33 MB, `results/` 37 MB, `reports/` 132 KB.
- Dependencies: `ccxt>=4.0`, `pandas>=2.2`, `numpy>=2.0`, `pyarrow>=17.0`, `PyYAML>=6.0`,
  `yfinance>=0.2.50`.
- `research/` was git-initialized on 2026-09-19 (`bcd727d initial commit`, clean tree), so the
  port has a baseline to diff against.
- An "EdgeLab Nightly" Windows scheduled task runs one cycle at 02:00 daily on this host;
  `run_nightly.ps1`'s header carries the re-registration commands.
- `deploy/edgelab.service` is a systemd unit running `scripts/nightly.py --loop 60` with
  `Restart=always`, `RestartSec=60` and `Nice=10`, from `/opt/edgelab`. Both runners currently
  write to `results/registry.db`.
- GEX's `build_scheduler()` returns an `AsyncIOScheduler` with `CronTrigger` jobs and is
  deliberately separated from the job functions so tests can inspect registered triggers
  without starting a clock. The research worker follows that shape.

## Acceptance

- Row counts match exactly after migration, and 100 randomly sampled hashes recompute to the
  same value under the ported `trial_hash`.
- A cycle run against Postgres skips every already-tried combination — the registry's whole
  purpose. Verify by running `--trials 50` twice and confirming the second adds no rows for
  combinations the first tried.
- `--report-only` still produces `reports/leaderboard.html`.
- The page's ranking, noise ceiling and filters agree with the static HTML report for the same
  data. A disagreement is a bug in one of them, to be resolved rather than averaged.
- The search worker survives a stack restart and resumes without repeating work.
- Both trigger shapes build and register one job, verified without starting a scheduler; a
  second cycle fired while one is running is refused rather than queued.
- A cycle killed with SIGTERM mid-flight leaves the registry consistent, and the next cycle
  continues rather than redoing the killed one's completed trials.
- **The repointed Windows task and the container write to the same registry.** Run a local
  cycle while the worker is running and confirm both land in `research.trials` and neither
  duplicates the other's rows.
- With Postgres unreachable, a local run **fails** rather than creating a `registry.db`.

## Likely first-contact failures

- **Two writers against two stores.** This is the one that silently destroys the registry's
  value. Two writers against *one* Postgres is fine and intended — the hash primary key turns
  a collision into `ON CONFLICT DO NOTHING`, so the worst case is a little duplicated CPU. Two
  writers against *different* stores is the fork: nothing errors, the histories diverge, and
  the never-repeat-work guarantee quietly dies. The no-fallback rule on `Registry` is what
  prevents it, and the repointed Windows task is what makes local runs safe rather than
  forbidden.
- **The local run cannot reach Postgres.** The Windows task now needs the lab database
  reachable — over the LAN or Tailscale, or from a local `docker compose up postgres`. It will
  fail loudly when it is not, which is correct but will be surprising the first time the
  laptop is off the network. Say so in `run_nightly.ps1`'s header.
- **`--loop N` and the worker's scheduler both running.** `nightly.py --loop` still exists for
  hand use; started inside the worker container alongside the scheduler it would double
  everything. The worker entrypoint registers the job and nothing else.
- **`misfire_grace_time` left at its default.** APScheduler's default drops a job fired more
  than one second late, which on a busy host silently means a nightly cycle that never runs.
  Set it explicitly and generously, and log skips.
- **`INSERT OR IGNORE` is SQLite.** `Registry.record` uses it today; Postgres needs
  `ON CONFLICT (hash) DO NOTHING`. Getting this right is also what makes two writers against
  one database harmless rather than an integrity error.
- **`seen()` becomes a network round trip.** `search.py` calls `registry.seen(hash)` before
  every trial. Against a local SQLite file that is free; against Postgres over the LAN — and
  much more so from the Windows host over Tailscale — it can dominate a cycle that is
  otherwise CPU-bound. Batch the check, or load the hash set once per cycle and consult it in
  memory, and measure trials-per-minute before and after the port rather than assuming.
- **`ccxt` and `yfinance` in the API image.** Worker-only dependencies that will bloat the
  runtime stage and pull a network-heavy tree into the request path. Either add a separate
  worker stage to the Dockerfile or accept the size deliberately and say so.
- **`REAL` versus `DOUBLE PRECISION`.** SQLite's `REAL` is already a 64-bit float, so values
  transfer exactly — but a migration that lands them in `NUMERIC` would round Sharpes and
  change rankings.
- **Timestamp parsing.** `run_date` as `TEXT` holds whatever format the writer used. The
  migration must parse explicitly and fail loudly on anything it cannot, never coerce to
  `NULL`.
- **The noise ceiling depends on the total trial count.** It scales with the number of trials
  in the registry, so if the migration loses or duplicates rows the ceiling moves and every
  green row is wrong.
- Yahoo's 730-day cap on hourly data, and `=F` futures roll gaps. Both are documented in
  EdgeLab's README; neither is fixed here, and the futures caveat must reach the UI.

## Out of scope

- New strategy families, new markets, changes to the search or the cost model. This is a port;
  the science does not move.
- Execution or paper-trading automation. `paper.py` moves as-is.
- Migrating OHLCV parquet into Postgres.

## Result — T77

**Done 2026-09-19.** 1,040 backend tests green (1,003 before, 37 added), both linters clean, and
the registry migrated for real against the lab Postgres.

Verified, not assumed:

- **134,377 trials and 23 paper candidates migrated**, counts exact in both directions, and 100
  randomly sampled *migrated* rows re-hashed to their stored hash with zero mismatches. Sharpes
  compared with exact equality, not tolerance. Re-running the import added nothing.
- **The ported report is byte-identical to the original's.** Both were run over the same data --
  the new one on Postgres, the original on `registry.db` -- and both reported `total_trials:
  134377, noise_ceiling: 5.6, candidates_above_ceiling: 0`. A diff of the two HTML files differs
  on exactly two lines: the generation timestamp, and the data-cache age (the copied parquet
  files have fresh mtimes). Same ranking, same rows, same family tables. That is the strongest
  single piece of evidence that the science did not move.
- **The dedupe guarantee holds against the migrated hashes.** A 50-trial cycle logged `loaded
  134377 known trial hashes` and `duplicates: 65` -- it consulted the migrated registry and
  skipped combinations already tried. Running the same *seeded* search twice raised the
  duplicate count from 63 to 130, i.e. the second run recognised the first run's new trials and
  searched past them rather than redoing them.
- **A full cycle runs inside the container**: `docker compose exec research-search` completed a
  20-trial cycle, ran the promotion gates (several walk-forward rejections logged), and wrote
  `/data/research/reports/leaderboard.html`. The worker boots, finds `research.trials`
  immediately and schedules `cron[hour='2', minute='0']`.
- `--report-only` still produces the static HTML.
- `docker compose config` valid for dev and prod; the prod overlay gives `research-search` a
  2-core ceiling.

### Judgment calls

**The lint config, not the ported code, absorbed the style mismatch.** The eight science modules
raise 16 ruff findings under this repo's config -- `date.today()`, `param_space` class
attributes, `zip` instead of `pairwise`, the `datetime.UTC` alias. None is a defect and one
(`DTZ011`) would change which day a trial is filed under, which is research-visible. They are
covered by a scoped `per-file-ignores` block naming each rule and its reason, so the port stays
diffable against `bcd727d` and "did the backtester change?" is answerable by `diff`. New code in
the module -- `registry.py`, `config.py`, `nightly.py`, `jobs/` -- is linted normally, and
`nightly.py`'s own `date.today()` was fixed properly rather than ignored.

**`RESULTS_DIR` was deleted rather than repointed.** It existed to hold `registry.db`. Keeping a
name that resolves to a plausible place to put a SQLite file would be an open invitation to the
exact fork the no-fallback rule exists to prevent. For the same reason `Registry.__init__` has
no `path=` argument, and a test asserts its signature.

**`alembic/env.py` takes a list of metadatas.** Each module owns its own `Base` -- nothing in
`research` imports `gex`, and a test calling `create_all` for one must not create the other's
tables -- so Alembic gets `[Base.metadata, ResearchBase.metadata]`. Combining them into one
throwaway `MetaData` via `to_metadata` was tried first and is wrong: it preserves each table's
schema but re-resolves string foreign keys against the *new* metadata's default schema, so gex's
`ForeignKey("snapshots.id")` went looking for `public.snapshots` and autogenerate died with
`NoReferencedTableError`.

### Gotchas worth recording

- **Autogenerate emits a fully-qualified type name without importing it.** The generated
  revision referenced `app.modules.gex.models.db.UTCDateTime` on `promoted_at` with no
  corresponding import, so the file raised `NameError` the moment it ran. Caught by reading the
  generated migration line by line, which the brief demanded for a different reason.
- **The frozen hash test caught its own placeholder.** The expected `trial_hash` value was first
  written from memory and was wrong; running the original implementation in the standalone
  repo's virtualenv produced `4974b321697a13288a21fc65`. A test that recomputed the expectation
  from the code under test would have passed either way and proved nothing.
- **`seen()` needed the in-memory set to be more than an optimisation.** The brief flagged the
  round-trip cost; what makes it structural is that `record` has to keep the set current, or a
  trial recorded earlier in the same cycle would not be seen later in it.

### Not done here

T78 (the `/api/research/*` endpoints and the leaderboard page) is the next task in this file and
is unstarted. Until it lands, the static `leaderboard.html` under `DATA_DIR/research/reports/` is
the only UI -- which is exactly the fallback the brief kept it for.

The 33 MB OHLCV tree was **copied**, not moved: `projects/research/data/` is untouched, so the
standalone repo still runs. It should be deleted once T78 has been used in anger for a while and
nobody has needed to fall back.

## Result — T78

**Done 2026-09-19.** 1,060 backend tests (20 added) and 377 frontend tests (20 added) green,
both linters clean (frontend 0 errors), `npm run build` clean, and every endpoint exercised
against the real 134,507-trial registry.

### What shipped

Four endpoints under `/api/research`: `leaderboard` (ranked, filtered, paginated, **with the
ceiling in the same payload**), `trials/{hash}`, `paper`, `status`. Two pages: the leaderboard
with per-row verdicts, a filter bar whose options come from the data, a pager reporting the true
match count, and a trial drawer carrying the IS/OOS split; and the paper watchlist with every
promotion gate as a column.

### The honesty rules, and how they are enforced rather than intended

The brief's central claim is that "a leaderboard that drops the noise ceiling is worse than no
leaderboard". Each of these is a test, not a convention:

- **The ceiling cannot be separated from the rows.** It is a required field of the same
  response, and the TypeScript types make it and `above_ceiling` non-nullable, so a component
  cannot render rows while forgetting the ceiling.
- **Filtering never lowers it.** The denominator is the whole registry. Tested both server-side
  and on the page: narrowing to one market leaves `total_trials` and the ceiling untouched. A
  ceiling that moved with a filter would let anyone filter their way to a green row.
- **Losing trials stay in the denominator** even though they are filtered out of the rows —
  which is *why* they are never deleted.
- **Each row is judged against its own OOS span.** A test asserts a one-year row gets a higher
  ceiling than a sixteen-year row at the same Sharpe.
- **Below-ceiling rows are dimmed and labelled in words**, not hidden and not colour-only.
- **The paper list is ordered by promotion date.** The test's fixture is built so a
  performance sort would reorder it and fail.
- **Both caveats render on the page**, asserted by test.

The real data makes the point better than any fixture could: the top of the leaderboard is
`oos_sharpe 4.98` against its own ceiling of `5.52` — `above_ceiling: false`. 22,237 rows pass
the filters and **none of the top ones clears its ceiling**, which matches the static report's
`candidates_above_ceiling: 0` exactly.

### Judgment calls

**Two things were hoisted out of `modules/gex` rather than duplicated.** `lib/http.ts` now holds
the base-URL resolution, `ApiError` and `apiFetch`; `src/mocks/` composes both modules' MSW
handlers into the one server and worker. Neither was scope creep: `resolveBaseUrl`'s correctness
rests on a chain of reasoning about same-origin deployment that a second copy would silently
fork, and the Vitest server runs with `onUnhandledRequest: 'error'`, so research requests had to
be registered or every research test would fail on a confusing network error. Nine gex test
files had their `mocks/server` import repointed; `modules/gex/api/client.ts` re-exports
`ApiError` and `API_BASE_URL` so its own callers were untouched.

**Research got its own `ResearchFrame` instead of reusing `shell/AppFrame`.** `AppFrame` is not
the generic shell its name suggests: it hard-mounts GEX's `ContextBar` (symbol/filter/snapshot
controls meaningless here) and a `SideRail` bound to GEX's `NAV_GROUPS`. Making it generic is
**T81**'s explicit scope — "launcher and module shell, plus the module switcher" — and
half-building that here would leave T81 undoing work rather than doing it. `ResearchFrame` uses
the same shared primitives and is documented as something T81 should absorb.

**The API returns numbers and flags; the page owns the prose.** The caveats are frontend content
with tests asserting they render, rather than strings in the JSON. Putting presentation text in
an API response would have guaranteed they appear at the cost of making the endpoint a view.

**Mock fixtures mirror the pessimistic reality.** The MSW leaderboard's best row is below its
ceiling, because that is what the registry actually looks like; building against optimistic mock
data would have tuned the UI for a state that has never occurred. One long-span row exists so
the `above_ceiling` branch stays reachable in development.

### Caught by doing

- **`npm run build` catches what `tsc --noEmit` does not.** The build runs `tsc -b`, which found
  four missing `metricKey` props in `StatusStrip` that a plain `--noEmit` pass had reported
  clean. Worth knowing: the typecheck alone is not the gate CI applies.
- **The repo has no `@testing-library/user-event`.** Tests were first written against it; the
  house idiom is `fireEvent`, and matching it was better than adding a dependency for four
  interactions.
- **`App.test.tsx` encoded T75's state** — it asserted EdgeLab was *not* a navigable link. T78
  makes it one, so the assertion was inverted rather than deleted, and now pins that `xactx`
  stays non-navigable until T80.
- One new frontend lint **warning** (0 errors): `FilterBar.tsx` exports `DEFAULT_FILTERS`
  alongside a component, which trips `react-refresh/only-export-components`. Four existing
  components in the repo do the same; left consistent rather than adding a file for one
  constant.

### Not done here

The brief's "a row expands to its parameters, its IS/OOS split and its regime condition" —
the regime condition is in `params.regime` and is shown as a parameter rather than being given
its own treatment. Worth revisiting once the page has been used.

The static `leaderboard.html` still exists and is still written by `--report-only`. The brief
says to drop it "in a later pass once the page is trusted"; it has not earned that yet.
