# Scan fixtures (T55)

Recorded 2026-09-09 against the live backend (`docker compose up`, `http://localhost:8001`)
with the exact curl commands from `plans/continuation/07-ui.md`'s T55 task:

```
curl -s http://localhost:8001/api/scan/breakouts            > breakouts.json
curl -s http://localhost:8001/api/scan/breakouts/SPY        > breakouts_SPY.json
curl -s http://localhost:8001/api/scan/trend                > trend.json
curl -s http://localhost:8001/api/scan/trend/SPY            > trend_SPY.json
curl -s "http://localhost:8001/api/bars/SPY?start=2026-03-01" > bars_SPY.json
curl -s http://localhost:8001/api/universe                  > universe.json
curl -s http://localhost:8001/api/health/capture            > health_capture.json
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
curl -s "http://localhost:8001/api/scan/rotation?group=sectors&benchmark=SPY&weeks=6"      > rotation_sectors.json
curl -s "http://localhost:8001/api/scan/rotation?group=industries&benchmark=RSP&weeks=6"   > rotation_industries_rsp.json
curl -s "http://localhost:8001/api/scan/rotation?group=assets&benchmark=SPY&weeks=6"        > rotation_assets.json
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
curl -s "http://localhost:8001/api/scan/regime?filter=ALL"       > regime.json
curl -s "http://localhost:8001/api/scan/regime?filter=ZERO_DTE"  > regime_zero_dte.json
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
