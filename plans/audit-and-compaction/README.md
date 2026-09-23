# Logic audit + task-history compaction

## Context
The user asked for two things: (1) an audit of whether the app's logic holds together, and
(2) archiving finished tasks behind a compact decisions document, so agents don't have to read
the 121 KB `TASKS.md`.

Two read-only surveys and live MCP queries already show that both are needed.
- **The bookkeeping has drifted.** 13 finished blocks have no Done marker. Several markers are out of date: T87, T88 and T104 say "not deployed" but have been deployed, T89 was closed by the gate, and T70 is superseded.
- **There is a task-ID collision.** Commit `808dee9`, labelled "T83: score the paper watchlist", is not the T83 in `TASKS.md`. That T83 is still open, and the paper-scores work has no entry.
- **Five pieces of open work have no task ID:**
  - the extended-symbol EOD safety net
  - a same-session VIX source
  - T74's frontend wiring
  - decision-engine calibration
  - the `variance_ratio` ZeroDivisionError
- **The docs agents rely on contradict the code in 13 confirmed places.** Examples: `CLAUDE.md` says "T00–T37" and "two workers" (there are four, including `capture-watch`); `workflow.md` says "Phases 0–3 built, Phase 4 next"; `architecture.md` predates the research and terminal modules; the MCP doc is missing 2 of its 11 tools.

**On the compaction idea:** it's a good one, with two conditions.
1. **Archive verbatim, never summarize-and-delete.** `TASKS.md` is "the project's memory of why", and the plan files' *Result* headings hold the evidence.
2. **The compact document holds decisions only** — no statuses, counts or test numbers. Numbers baked into a prompt-facing file rot and then get quoted confidently. That is the F7 failure this repo has already paid for twice.

## Part 1 — Compaction (do first, so the audit files into a clean index)

1. **Archive.** `git mv TASKS.md docs/archive/TASKS-T00-T107.md`. Add a one-paragraph header saying it is frozen as of 2026-09-22 and that `TASKS.md` and `context/decisions.md` supersede it for current work. Otherwise the file stays byte-identical.
2. **New `TASKS.md`** (target < 25 KB) holds only the non-finished work, with each full block copied verbatim from the archive:
   - Open: T17, T20, T25, T31, T33, T57, T58, T72, T83, T84, T85, T94, T95, T96
   - Partial: T28, T81
   - Blocked: T21–T24, on a Tradier account
   - Conditional: T26, on ThetaData

   A short header covers the block shape, the next free ID, and the rule for how a task leaves the file (step 5).

   New IDs, recorded in the plans/README allocation line:
   - **T108** is the retroactive entry for the paper-scores work (`808dee9`). It is Done; it goes in the archive's addendum and `decisions.md`, with a note that the commit subject reads "T83".
   - **T109–T113** go to the five unnumbered items listed above, as OPEN blocks.
3. **New `context/decisions.md`** (target ≤ 12 KB) has two sections.
   - **Decisions by topic**: engine and Greeks, capture and data integrity, storage and schemas, API and frontend, decisions and scoring, research, terminal, MCP, ops, process. Each line reads: decision — why, in ≤ 1 clause — `[Txx](link to the plan Result heading or archive anchor)`. The lines are seeded from the survey's per-task decision column, including:
     - IV is decimal and never divided by 100
     - walls are named by gamma, not position
     - `WALL_MIN_ABS_FRACTION` is 1e-4
     - null ≠ 0
     - decisions are insert-when-unseen, never upserted
     - stop is checked before target
     - no threshold is tuned before ~100 resolved decisions
     - never tune the engine to match vendors
     - dedupe on a content hash
     - `session_date` is distinct from `captured_at`
     - expiry is stored as a rollup
     - an alert fires only on a universe-wide gap
     - `quantdesk_ro` is the boundary, not the SQL guard
     - the hardcoded track record is deleted, never updated
     - the per-step guard catches `SystemExit`
     - scan logic lives in pure modules
     - T70: the desk is never exposed publicly
   - **Initiative ledger**: one row per phase or initiative, giving the ID range, "complete" / "has open work", and the plan folder. Status words only, no numbers.
4. **Wire it in.**
   - `CLAUDE.md`: in the context index, add `context/decisions.md` with the trigger "before changing behaviour an earlier task settled". Fix the Reference table: `TASKS.md` = open work, add the archive row, add the missing plan folders, and point "current known gaps" at `state-review-2026-09-21` plus its verification doc.
   - `context/workflow.md`: rewrite "Current position" without phase numbers. Change the "append to TASKS.md" rule to: when a task lands, move its block to the archive, add a line to `decisions.md` if it set a durable rule, and record the result in its plan file.
   - `plans/README.md`: next free ID; mark the finished initiatives "— complete"; close the ui-ux-refresh "Status: proposed" line.
5. **Plan folders stay where they are.** Code comments and the archive link into them, and they are the evidence `decisions.md` points at.
6. **Fix the confirmed doc drift, docs only**, from the survey list:
   - service count (7 services, 4 workers), in CLAUDE.md, architecture.md and README
   - architecture.md: module state, data-flow diagram, frontend api path, the ten routers
   - stale paths in gex-engine.md, data-and-ops.md and README (`app/core/config.py`, `compose.yaml`)
   - the self-contradictory `backend/data/` lines
   - CI has four jobs
   - backend.md: tables list and the five-symbol default
   - mcp-connector.md: the 11 tools and current signatures
   - the CLAUDE.md Layout additions (capture_watch, factors, outcomes, memory, paper_scores…)
   - the invariant 2 wording: the sign is attached once in `to_frame` and consumed in `contract_gex` / `gamma_profile` / `_diagnostics`. Reword it to the real property, "applied once per value, never re-signed", and don't touch the code.
   - the invariant 8 terminal `search_path` exception, stated explicitly

   Code-level drift is filed as tasks, not edited: `scripts/_common.sh` leaving out `capture-watch`, the stale `catchup.py:189` comment, and the `server.py:8` docstring.

## Part 2 — Logic audit → `docs/audit-2026-09-22.md`, done inline (Opus)

**Rule, from the 09-21 verification doc:** a data-only finding is a symptom. Every finding needs a code `file:line` and a reproducing query or test, a severity, and a proposed task. Findings are **filed as tasks (T114+) in the new `TASKS.md`, not fixed silently** (the project rule).

Lanes, each with the questions it must answer:

- **A. Decisions and scoring** (`scan/decisions.py`, `scan/outcomes.py`, `jobs/decisions.py`, MCP `gex_track_record`)
  - *Already found:* ids 23 (SLV) and 32 (XLE) are on wrong-sign walls and are still scored in the track record; 50, 77, 78 and 80 are wrong-sign and `pending`, so they will be scored too. Is there a quarantine flag at all?
  - `GAMMA_PIN` has never been emitted since T99 deployed. Is its condition reachable?
  - R computation, untriggered and expiry handling, same-bar stop and target ambiguity.
  - Whether the factor cap's marks reach the track record.
- **B. Capture and ops** (`jobs/capture.py`, `catchup.py`, `outage.py`, `workers/capture_watch.py`)
  - XBI is missing from session 09-22. Why, and does anything notice a one-symbol loss? (The alert is universe-wide by design, so is anything per-symbol surfaced anywhere?)
  - Catch-up versus `session_date` on holidays and weekends.
  - The `db-restore` script leaving out `capture-watch`.
- **C. GEX engine**
  - The three sign-consuming sites agree: walls, flip and profile all come from one signed frame.
  - The null-OI path through the T100 aggregates, `gex_by_expiry` horizons and T103 IV columns.
  - The `settings` import in `greeks.py`.
- **D. Research**
  - The noise ceiling, doubled-cost gate and walk-forward gate are enforced on *every* path to the leaderboard and paper promotion, not just the page.
  - `Registry` accepts a `sqlite://` URL with no dialect guard (invariant 9).
  - `paper_scores` (the ghost T83) — does its scoring respect the same gates?
- **E. Terminal**
  - Every read path picks the latest vintage ≤ `as_of`, including derive, graph and the brief.
  - The brief GET writes (T85, still open) — confirm how bad it is.
  - The edge sign-conflict logic, which is untested since T84 is open.
- **F. Cross-module**
  - Null-versus-zero on the wire types and the frontend formatting after T100/T103.
  - MCP notes versus the data they describe.

Explore agents are allowed only for locating code. The reading and judgment stay inline.

## Order and commits
Part 1 steps 1–5 → step 6 → Part 2 (its tasks land in the new `TASKS.md`). Changes are left uncommitted for review; I'll propose three commits (archive+index, doc drift, audit) and commit when asked.

## Verification
- A script checks that every relative Markdown link in the repo still resolves after the move, and greps for `TASKS.md#`/`TASKS.md:` references in code comments.
- Every ID T00–T113 appears exactly once as live (new `TASKS.md`) or as history (archive + ledger). This is checked by a script.
- Sizes: `TASKS.md` < 25 KB, `decisions.md` ≤ 12 KB, and `decisions.md` contains no digits except IDs and named constants.
- Each audit finding is re-run: its query through `mcp__quantdesk__query_sql` and its `file:line` re-read.
- The code isn't touched, so the suites aren't needed. If anything outside `*.md` changes, run `uv run pytest`, `ruff`, `npm test` and `npm run build`.

---

## Result — 2026-09-22

**Compaction.**
- `TASKS.md` → [docs/archive/TASKS-T00-T107.md](../../docs/archive/TASKS-T00-T107.md), unchanged
  except for a frozen-notice header and relative links re-rooted by `../../`. It also has a
  dated addendum carrying the T108 entry and every status correction the survey found.
- The new [TASKS.md](../../TASKS.md) holds only the 34 non-finished blocks, copied verbatim, plus
  T109–T121. It is 29 KB, over the 25 KB target, because the audit filed seven tasks into it.
  Pre-T75 paths inside old blocks are flagged in its header rather than rewritten.
- [context/decisions.md](../../context/decisions.md) holds about a hundred one-line rules,
  plus the initiative ledger, in 11 KB. The only digits are rules, names and phase numbers.
- `CLAUDE.md`, `workflow.md` and `plans/README.md` now point at the three files. The "current
  position" paragraph is gone from `workflow.md`: it went stale in days, and state belongs in the
  data. The next free ID lives in exactly one place, `TASKS.md`'s header.

**Doc drift fixed:**
- the worker and service counts in `CLAUDE.md`, `architecture.md`, `data-and-ops.md` and `README.md`
- the architecture overview, data-flow diagram and router list
- stale paths in `gex-engine.md`, `data-and-ops.md`, `README.md` and `docs/schema.md`
- the self-contradicting `backend/data/` paragraph
- CI now documented as four jobs
- `backend.md`'s tables and symbol count
- the MCP tool table, now 11 tools with real signatures
- the `CLAUDE.md` Layout
- invariant 2's wording: the code was right and the doc overstated it
- invariant 8's terminal `search_path` exception, now stated

Code-side drift is filed as T114, not edited.

**A mistake of my own, caught during the audit.** The first draft of `decisions.md` called
`captured_at` "the wall clock", copying `desk_status`'s note. It is Cboe's timestamp (audit
F1). That draft line was corrected the same day. It is the F7 failure again, in the file built
to prevent it: an agent-facing sentence taken from another agent-facing sentence rather than
from the code.

**Audit.** [docs/audit-2026-09-22.md](../../docs/audit-2026-09-22.md) has seven findings, all
confirmed in both the code and the data, filed as T115–T121:
- **A1 is critical.** Fade outcomes exit at the trigger bar's open, which printed before the
  fill. It produced all five scored fade wins. Re-scored conservatively, `FADE_CALL_WALL` moves
  from +0.89R to about +0.07R, and to about −0.14R once a re-emitted order is counted once (A2).
- The engine rollups, IV columns, research gates and point-in-time readers were checked and are
  sound. What was not checked is listed in the audit.

**Verified.**
- Every relative Markdown link in the repo resolves; a script checked this, and it found and
  fixed two in `docs/schema.md` that were broken before this work.
- Every ID T00–T121 is either a live block in `TASKS.md` or in the archive, with no duplicate
  blocks.
- No file outside `*.md` changed, so the test suites were not run.
