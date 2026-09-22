# Desk integrity — what the desk asserts versus what it measured (2026-09-21)

Nine tasks, `T99`–`T107`, from [docs/state-review-2026-09-21.md](../../docs/state-review-2026-09-21.md)
and its source-confirmed companion
[docs/state-review-2026-09-21-verification.md](../../docs/state-review-2026-09-21-verification.md).

## The theme

Every item here is the same failure in a different surface: **the desk states something it did
not measure, in a voice that sounds measured.**

- A wall is named from its position relative to spot, not from its gamma sign (`F1`).
- A wall is named at all when it carries −1.87 dollars of gamma (`F1b`).
- The prose then asserts that name as a structural fact (`F2`).
- An empty aggregate is written as `0`, so "nothing was measurable" reads as "dealers are
  flat" (`F3`).
- A five-session outage passes silently because nothing watches the endpoint built to catch
  it (`F4`).
- A track record is hardcoded into a prompt file and quoted with full confidence months later
  (`F7d`).
- The MCP connector tells every caller that `outcome` is null when pending. It is never null
  (MCP).

None of these is a crash. All of them produce output that looks like the good kind.

**Read the verification document before dispatching anything.** The original review's causes
for `F1` and `F3` are both wrong in ways that change the fix, and its `F1` fix would
reintroduce a bug the code already handles deliberately.

## Files

```
00-wall-identity.md      T99   F1, F1b, F2 — a wall is named by its gamma, never its position
01-null-aggregates.md    T100  F3 — an empty aggregate is null, not zero
02-expiry-and-session.md T101  F5 — the expiry dimension, at capture time
                         T102  F8 — the session date the chain belongs to
03-iv-persistence.md     T103  F6 — ATM and 30-day IV, from inputs already in memory
04-capture-alerting.md   T104  F4 — something has to watch /api/gex/health/capture
05-mcp-ergonomics.md     T105  the connector's row shape and tool granularity
                         T106  desk status and track record as tools — retires F7d
06-doc-drift.md          T107  F7a/b/c — three documented facts the data contradicts
```

## Dependency graph

```
T99  wall identity ────┐
                       ├──> T100 null aggregates ──> T101 expiry ──> T102 session date
                       │    (shared paths: engine.py, regime.py — must not run in parallel)
                       │
T104 capture alerting ─┘ (independent, any time)

T105 MCP row shape ────> T106 desk status + track record ────> T107 doc drift
                                                               (T107 deletes the block T106 replaces)

T103 IV persistence  (independent; wants T101's capture-time rollup pattern, does not need it)
```

## Dispatch order — three tracks, run together

The Opus work and the Sonnet work touch disjoint paths (`modules/gex/` versus `app/mcp/` and
`workers/`), so they do not queue behind each other. Only the Opus track is internally
sequential, and only because of shared files plus the standing rule against two concurrent
Opus agents (`context/workflow.md`).

**Opus track — correctness. Strictly sequential, three cold dispatches.**

1. **`T99`** — the P0: actively emitting wrong names with confident justification, and a
   ready-made six-row regression fixture.
2. **`T100`** — after `T99` merges. Shares `engine.py` and `regime.py`.
3. **`T101` → `T102`** — chained in one worktree, after `T100`.

**Sonnet track A — the connector.** Starts immediately, in parallel with `T99`.

1. **`T105` → `T106`** — chained in one worktree, same file.
2. **`T107`** — after `T106`, because the correct fix for `F7d` is to delete the hardcoded
   block and point at the new tool.

*Why this runs first among the Sonnet work, per the user's call on 2026-09-21:* it is the
surface every research session pays for, including the sessions used to verify the Opus
track's output. Fixing the connector early makes checking `T99` cheaper.

**Sonnet track B — the alert.** `T104`, any time, independent of everything. Separate worktree.

**Last:** `T103`, the smallest capability gain of the set.

## Verified facts for every agent in this initiative

Measured on 2026-09-21 against live Postgres. Hand these over rather than letting an agent
rediscover them.

- `gex.decisions` holds **97 rows**; `outcome` is **never null** — the literals are `pending`
  (51), `stop` (27), `target` (13), `untriggered` (6). **Resolved means `result_r IS NOT NULL`,
  which is 40 rows, not 97.** Getting this wrong changes every track-record number.
- Six decisions are mislabeled by `F1`: ids 23, 32, 50, 77, 78, 80. Two have resolved (32, 23)
  and both are losses.
- `KeyLevels.net_gex` is typed `float`. `GexLevel.net_gex` is already `nullable=True`. The
  schema is right and the domain type is wrong — not the other way round.
- `WallInfo` already carries `net_gex`. Nothing needs to be plumbed to fix `F1`.
- `engine.py` and `greeks.py` are **pure** (invariant 1). Every task here that touches them
  adds no I/O and no logging.
- The `gex.decisions` table is append-only by design — "rows are inserted when first seen and
  never rewritten" is the commitment the track record rests on. **No task here rewrites a
  historic decision**, including the six wrong ones.

## Decisions the user has made

Recorded here so no agent re-opens them:

- **Alert channel: Telegram** (`T104`). Bot API, token from the environment. Reaching a phone
  is the requirement — the failure mode is twelve days of not looking at the desk.
- **Backfill history: yes**, in both places it was open. `gex.gex_levels` is recomputed after
  `T99` so the junk walls stop being served; `session_date` and the expiry rollup are
  backfilled over stored Parquet in `T101`/`T102`. Neither touches `gex.decisions`.
- **The connector goes first among the Sonnet work** (`T105`/`T106`), in parallel with `T99`.
- **The fifth decision key: `GAMMA_PIN`, scored.** The five mislabeled-but-sound rows are spot
  resting *on* the largest positive-gamma strike in a long-gamma book — a magnet, not a
  boundary. It gets its own key and its own row in the track record. Scoped to the below-spot
  case only; see `00-wall-identity.md` decision 2b for why the symmetric case is left alone.

**Cross-task consequence:** `T106` runs in parallel with `T99` and must derive its key list
from the data. A hardcoded four-key list drops `GAMMA_PIN` from the track record on the day it
starts emitting.

Nothing is still open.

## Out of scope for the whole initiative

- **Root-causing the `F4` outage.** The container logs for 09-12 → 09-19 need shell access on
  the homeserver and were unreadable from both review passes. `T104` builds the alert; it does
  not explain the outage. File the post-mortem separately if the logs survive.
- **Backfilling the 09-14 → 09-18 gap.** Options open interest cannot be reconstructed after
  the fact. `T104` should say so in the docs so nobody expects recovery. This is distinct from
  the backfills that *are* in scope — see below.
- **Rewriting historic decisions.** See above. `gex.decisions` is append-only; the six wrong
  rows stay. Only the `gex.gex_levels` they point at get recomputed.
- **A new decision key for "spot has broken above the call wall" as a traded product.** `T99`
  gives that regime an honest name and an honest thesis; whether it deserves its own scoring
  bucket, sizing and track record is a product decision, not a bug fix.
