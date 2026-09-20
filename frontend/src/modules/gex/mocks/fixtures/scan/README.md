# Scan fixtures (T55)

> **Paths below carry a `/gex` segment since T75**, when every GEX route moved under
> `/api/gex`. The curl recipes have been updated and work as written. The *provenance* notes
> further down quote module paths (`app.api.scan`, `app.api.decisions`) as they were at the
> time of recording -- those are records of what was run, not instructions, and are left as
> they were.

Recorded 2026-09-09 against the live backend (`docker compose up`, `http://localhost:8001`)
with the exact curl commands from `plans/continuation/07-ui.md`'s T55 task:

```
curl -s http://localhost:8001/api/gex/scan/breakouts            > breakouts.json
curl -s http://localhost:8001/api/gex/scan/breakouts/SPY        > breakouts_SPY.json
curl -s http://localhost:8001/api/gex/scan/trend                > trend.json
curl -s http://localhost:8001/api/gex/scan/trend/SPY            > trend_SPY.json
curl -s "http://localhost:8001/api/gex/bars/SPY?start=2026-03-01" > bars_SPY.json
curl -s http://localhost:8001/api/gex/universe                  > universe.json
curl -s http://localhost:8001/api/gex/health/capture            > health_capture.json
```

All seven are the live response, byte-for-byte (only re-indented — no field was added, removed
or renamed). Response shapes matched `07-ui.md`'s "Verified facts" list exactly; see the T55
report for the one wording nuance worth flagging (`bars.stale_count` exists alongside
`bars.symbols[]`, which the plan's prose didn't spell out but is consistent with it).

## Hand-edited variants

The live universe happened to already contain two of the four required `null`/empty cases, so
only two fixtures needed a hand edit:

- **`breakouts.json`** (unedited) already carries a `rate: null` row — `XLP`, 4 events, below
  the 5-event floor the plan describes — and `trend.json` (unedited) already carries `iv30:
  null` rows — 19 of the 47 universe symbols, e.g. `DBA`. Both are the naturally-occurring
  common case the plan calls out, not something to fabricate.
- **`breakouts_open_empty.json`** — a copy of `breakouts.json` with `open_breakouts` forced to
  `[]`. The live snapshot has 18 open breakouts, so this variant exists purely to exercise the
  "no range breaks are inside their window" `EmptyState` the plan specifies for that panel.
- **`breakouts_excluded.json`** — a copy of `breakouts.json` with one synthetic row added to
  `excluded`: `{"symbol": "DBA", "reason": "4.2% of expected trading days missing bars in the
  last 126 bars (max 2%)"}`. The live universe has zero exclusions today (0.90-1.03s response,
  47/47 symbols admitted), so this is fabricated to exercise the "N symbols excluded for gaps"
  disclosure — the reason string's wording matches the format `app/api/scan.py`'s
  `_MAX_MISSING_BAR_FRACTION` check actually produces, just with numbers picked to demonstrate
  the case rather than measured.
- **`health_capture_stale.json`** — a copy of `health_capture.json` with the first
  `bars.symbols[]` entry's `stale` flipped to `true` and `bars.stale_count` set to `1` (the live
  capture is fully fresh, `stale_count: 0` everywhere). Exists to exercise `BarsFreshness`'s
  "stale count in the accent colour when non-zero" rule, which the live data can't currently
  demonstrate.

No other fixture was edited. `bars_SPY.json`, `breakouts_SPY.json`, `trend_SPY.json` and
`universe.json` are all the unmodified live response.

## Rotation fixtures (T51)

Recorded 2026-09-09 against the same live backend, one file per `(group, benchmark, weeks)`
combination the page and its tests exercise:

```
curl -s "http://localhost:8001/api/gex/scan/rotation?group=sectors&benchmark=SPY&weeks=6"      > rotation_sectors.json
curl -s "http://localhost:8001/api/gex/scan/rotation?group=industries&benchmark=RSP&weeks=6"   > rotation_industries_rsp.json
curl -s "http://localhost:8001/api/gex/scan/rotation?group=assets&benchmark=SPY&weeks=6"        > rotation_assets.json
```

All three are the live response, byte-for-byte (only re-indented). `rotation_sectors.json` is
the page's default (`group=sectors&benchmark=SPY&weeks=6`, matching `useScanParams`'
defaults); `rotation_industries_rsp.json` is the exact deep-link combination
07-ui.md's acceptance line names (`/rotation?group=industries&benchmark=RSP&weeks=6`);
`rotation_assets.json` is the live case where SPY, benchmarked against itself, sits at exactly
`(100, 100)` on every trail week — used for the "renders on the crosshair" acceptance check.
`breadth` is identical across all three (the backend always computes it over the fixed 11
sector ETFs regardless of the requested `group`), which is itself worth recording: the
Breadth block's numbers do not change when the toolbar's `group` changes, only its own fixed
sector-level inputs do.

- **`rotation_sectors_null.json`** — a copy of `rotation_sectors.json` with `XLRE`'s first two
  trail points' `rs_ratio_approx`/`rs_momentum_approx` both set to `null`. The live universe
  has no symbol still inside its z-score warm-up (every sector ETF has years of history), so
  this is fabricated to exercise plan 04's documented state ("the z-score window with fewer
  than `w` weeks after a new symbol is added to the universe... return `None` rows rather than
  a partial-window number") — `RrgChart` must omit these two points from the trail/scatter
  rather than plotting them at `(0, 0)`.

No other rotation fixture was edited.

## Regime fixtures (T49)

Recorded 2026-09-09 against the same live backend:

```
curl -s "http://localhost:8001/api/gex/scan/regime?filter=ALL"       > regime.json
curl -s "http://localhost:8001/api/gex/scan/regime?filter=ZERO_DTE"  > regime_zero_dte.json
```

Both are the live response, byte-for-byte (only re-indented) — neither was hand-edited.
`regime.json` is the page's default (`filter=ALL`) and, by itself, exercises every case the
T49 acceptance line names: 28 rows, verdict distribution `continuation: 13, mixed: 6, fade: 2,
null: 7`; of the seven nulls, five are stale (`GDX`, `KRE`, `XBI`, `XLC`, `XLRE`, each with
`stale: true` and a `chain_age_minutes` past the 30-minute threshold — XLRE's is 159.6) and two
are noise-dominated (`XLK`, `TLT`, `positioning.noise_dominated: true`, `stale: false`).
`zero_dte_share` is `null` on all 28 rows. `wall_below` is `null` for XLRE (no wall on that side
in the current strike ladder), which is the live case exercising that field's own nullability.

`regime_zero_dte.json` is the honest degenerate case `plans/continuation/03-regime-board.md`'s
"The 0DTE share is not derivable from an EOD snapshot" describes: every row's `positioning`
comes back `net_gex: 0, abs_gex: 0, ratio: null, noise_dominated: true, label: "NO DATA"` and
every wall/flip field is `null`, because the same-day expiry has already left the payload by
capture time (Cboe's `0 by-strike rows` fact from that section, not fabricated for this
fixture). Worth recording because it isn't obvious from the plan text alone: the same five
symbols that are stale in `regime.json` are *also* `noise_dominated: true` here (all 28 rows
are noise-dominated under `ZERO_DTE`) — this fixture is therefore the one live case that
actually exercises `regimeRows.ts`'s "stale wins" precedence rule (a row can genuinely be both
at once; the board must group it as `stale`, not `noise-dominated`, since the stale chain is
the reason nothing about it, including the noise reading, can be trusted for today).

## Cross-asset regime strip fixtures (T54)

`cross_asset.json` was **not** curled against a running dev server (this task built the route
inside an isolated git worktree, and the guardrail against restarting the shared dev
server/Docker stack means there was no running server with this task's code to curl). Instead
it was captured by invoking the real FastAPI router in-process
(`TestClient(app).get('/api/scan/cross-asset')`, `app` built from `app.api.scan.router` with no
session-factory override) against the **real dev Postgres**, immediately after backfilling all
six Cboe index symbols into it (`uv run python -m app.bars_backfill --years 3/5 --symbols
^VIX,^VIX3M,...`, see the T54 report for the exact commands and their output). This is the live
computed response, byte-for-byte (only re-indented) — not fabricated, not hand-typed. Recorded
2026-09-10: `term_structure: "contango"` (VIX 16.46 < VIX3M 18.87 and VIX9D 15.59 < VIX 16.46),
`vrp: 8.36` (VIX 16.46 minus SPY RV20 8.10 vol points), and — worth calling out specifically,
since it is exactly the hazard `plans/continuation/06-cross-asset-regime.md` names by name —
`sector_correlation_n: 18`, not `20`: two of the trailing 20 daily bars were dropped from the
aligned sample because at least one of the 11 sector ETFs was missing a bar on those dates,
live evidence the alignment/effective-sample-size logic is doing real work, not merely passing
its own hand-built unit tests.

- **`cross_asset_empty.json`** — hand-built (not curled): every field `null`/`0`/its own
  `*_reason`, the state before any of the six Cboe index symbols has ever been backfilled (or,
  for a fresh deployment, before the 17:30 ET bars job has run once). Confirmed to match the
  real route's own behavior in this state by `backend/tests/test_scan_cross_asset_api.py
  ::test_get_cross_asset_empty_when_nothing_seeded`, not merely assumed. Exercises
  `RegimeStrip`'s "n/a tiles with the reason, never a fabricated number" contract.

## Flows fixtures (T53)

Recorded 2026-09-10 against the live backend:

```
curl -s "http://localhost:8001/api/gex/scan/flows?window=5"   > flows_5.json
curl -s "http://localhost:8001/api/gex/scan/flows?window=20"  > flows_20.json
curl -s "http://localhost:8001/api/gex/scan/flows?window=60"  > flows_60.json
curl -s "http://localhost:8001/api/gex/health/capture"         > health_capture.json
```

All four are the live response, byte-for-byte (only re-indented) — nothing hand-edited.
`health_capture.json` is **re-recorded from the T55 fixture** (T52's `flows` block didn't exist
when T55 first curled it on 2026-09-09; this is additive to the shape T55 recorded, nothing
existing was removed or renamed) — `health_capture_stale.json` was regenerated from this new
base with the same single edit T55's own README section above describes (first
`bars.symbols[]` entry's `stale` flipped `true`, `bars.stale_count: 1`), so both fixtures stay
on the same base data and both now type-check against `CaptureHealth`'s additive `flows` field.

**The live state today is the page's normal case, not an edge case.** All three `flows_*.json`
files carry the same content regardless of window (`window` accumulates from the day T52's job
first ran, and no window changes that): 22 of 23 supported symbols read `flow: null,
flow_pct: null, history_since: null, message: "no data yet"` (zero stored rows yet); the
23rd, `XLK`, reads `flow: null, history_since: "2026-09-08", message: "history since
2026-09-08"` (exactly one stored row — not enough for even a one-day flow). `no_flow_data`
names the same four unsupported symbols (`SMH`, `GDX`, `QQQ`, `USO`) in every file, and
`sources` names both supported families' newest stored row date (`spdr: 2026-09-08`,
`ishares: 2026-09-09`). This is precisely the state `Flows.tsx`'s empty/short-history rendering
exists for — see that file's own docstring.

- **`flows_synthetic.json`** — hand-built from `flows_20.json`, tagged with a top-level
  `_provenance` field naming exactly what was changed (the repo's `backend/tests/fixtures/
  marketdata/*_synthetic.json` convention for a fabricated fixture, carried over here since the
  live data cannot yet produce a single non-null flow to test `FlowBars` actually drawing a bar
  against). Six symbols' `flow`/`flow_pct`/`history_since`/`message` were invented (SPY, GLD,
  IWM, HYG, XLF, XLE — three positive, three negative, plausible magnitudes for funds of roughly
  that size, not measured); `XLK`'s real short-history row, every other symbol's real "no data
  yet" row, `no_flow_data` and `sources` are all copied unedited from the live response. Used
  only in `FlowBars`'s own test and one `Flows.test.tsx` case exercising the non-empty chart —
  never wired as a default MSW handler response, so the app's default dev/test state stays the
  honest, all-null live one.

## Decisions fixture (T60)

`decisions.json` is `GET /api/decisions` (default `filter=ALL`, `min_score=0`), recorded
**in-process** on 2026-09-10 against the real dev Postgres (`DATABASE_URL` from `backend/.env`,
`DATA_DIR=../data` so the IV lookup could open the Docker-written Parquet files) -- the running
Docker backend predated the router and, per the project's "never restart a shared process"
rule, was not restarted to curl it:

```
cd backend && DATA_DIR=../data uv run python -c "
from fastapi import FastAPI; from fastapi.testclient import TestClient
from app.api.decisions import router
app = FastAPI(); app.include_router(router, prefix='/api')
print(TestClient(app).get('/api/decisions').text)" > ../frontend/src/mocks/fixtures/scan/decisions.json
```

Unedited (only re-indented). What the live universe happened to contain that day, which the
page tests rely on: 25 ranked opportunities across 28 optioned symbols (22 `active`, 3 `watch`,
no `rejected` -- so the rejected branch is exercised only by the backend's own unit tests),
grades A/B/C all present, four symbols with no trade for a stale chain (XLRE, XLC, XBI, KRE --
two of them sharing the identical reason sentence, which is why `Decisions.test.tsx` uses
`getAllByText` there), one for noise-dominated net GEX (EEM), and an empty `no_chain`. The
handler ignores `filter` (one recording serves all three) and honours `min_score` so the
toolbar's threshold visibly trims the table against the mock.

## Decisions history fixture (T61)

`decisions_history.json` is `GET /api/decisions/history`, recorded in-process on 2026-09-10
right after the first live `record_decisions_job` run (the same in-process recipe as the
decisions fixture above, `DATA_DIR=../data`). Unedited. It is the honest day-one state: 25
stored rows, every one `pending` with `outcome_note` "no bars after the decision date yet",
every rate `null` (withheld below five resolved trades) and every R `null`. Nothing resolved
exists on the live database yet, so `Decisions.test.tsx` builds a clearly-labelled synthetic
variant *in the test* (three rows flipped to target/stop/pending-with-mark) to exercise the
R and rate columns, rather than committing a fabricated recording as if it were live.
