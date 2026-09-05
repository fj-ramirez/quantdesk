# Supervision report — GEX trading app build

**Author:** supervising agent (Opus), main session
**Period:** 2026-09-04 → 2026-09-05
**Audience:** whoever supervises this build next
**Repo state at writing:** 40 commits on `master`, 389 backend tests, 50 frontend tests, ~18.4k LOC (excluding lockfiles and fixtures)

---

## 1. What this was

A personal GEX (gamma exposure) analysis app for US index options — SPX/SPXW, SPY, QQQ. Python 3.12 / FastAPI + Postgres + Parquet backend, React 18 / Vite / TypeScript frontend. Single user, data budget under $50/month, analysis only.

The work was pre-decomposed by the user into `PLAN.md` (architecture and phases) and `TASKS.md` (28 numbered tasks with per-task model assignments and an explicit dependency graph). My role was dispatch and review, not implementation: spawn one subagent per task at the model `TASKS.md` assigned, verify the result, merge, unblock dependents.

The user's instruction was explicit: *"dont wait for me to spawn the rest of the tasks, you are in charge of that."*

## 2. Outcome

**The app works end to end and its numbers are externally validated.** A live capture pulls 28,650 SPX contracts in ~2s, computes gamma exposure in 0.43s, persists levels, serves them over a typed API, and renders them in a dashboard.

Delivered and merged: T00–T14, T16, T27, T29, T30, T34, T35, T36, plus Docker quick-start fixes. Phases 0–3 are complete; Phase 4 (intraday) and 5 (real-time Tradier) are not started, the latter blocked on a brokerage account the user does not have yet.

Nine tasks were added during the build (T29–T37) — all of them findings, not scope creep. Details in §4.

### The number that matters

`docs/validation.md` compares our output against free public vendor pages:

| Metric | Ours | Vendor B | Diff |
|---|---:|---:|---|
| Net GEX (SPX) | +48.29B | +43.6B | +10.8% |
| Call wall | 7800 | 7800 | **exact** |
| Put wall | 7500 | 7500 | **exact** |
| Flip point | 7661.73 | 7680.96 | −0.25% |
| Total open interest | 23,072,527 | 22,635,878 | +1.9% |

Sign convention, formula, units, multiplier, SPX+SPXW merge and OI vintage are all externally corroborated. The residual ~11% on magnitude is traced to a carry parameter that put-call parity says is mis-set (see T33, open).

## 3. The central finding

**Every significant defect in this build was found by supervisor verification. None were found by the agent that wrote the code.**

All 20-odd subagents reported their own work as complete and passing. All of them had run their own tests, and the tests passed. That signal turned out to carry almost no information about whether the work was correct.

What verification actually caught:

| Found by | How | Consequence if missed |
|---|---|---|
| IV units were wrong in `TASKS.md` itself | Fetched the live Cboe payload and read the values | Every implied vol 100× too small; every Greek silently wrong |
| MarketData.app free tier can't use `mode=cached` | Agent read the live docs; I recorded it | The fallback provider would fail at the exact moment Cboe broke |
| `call_wall == put_wall == 8000` | Read the actual API output instead of the summary | Walls that bracket nothing; the primary dashboard number meaningless |
| Frontend/API nullability mismatch | Diffed `types.ts` against live `/openapi.json` | Daily runtime crash — every EOD snapshot has null walls |
| Gamma profile x-axis forced through zero | **Looked at the screenshot** the agent submitted as proof | The chart's entire purpose (the zero crossing) invisible |
| Backend hangs without Postgres | Started it with the DB down | App won't boot natively; no error message |
| Cboe timestamp is generation time, not effective time | Polled the endpoint 2h after close and compared | Freshness badge lies about data age on a trading tool |
| `docker compose up` cannot work from a clean checkout | The user hit it; I reproduced | Documented quick start broken for every new machine |

Two of these were errors in the **user's own source documents**, not in agent work. `TASKS.md` instructed the Cboe adapter to rescale IV from percent, and assumed a paid-only API mode was free. An agent following instructions faithfully would have implemented both bugs. Treat the spec as a hypothesis to test against reality, not as ground truth.

The screenshot case is the sharpest lesson. The agent produced real Playwright screenshots against real API data and reported the dashboard as rendering correctly. It was *right about the numbers* — and the primary chart was unreadable, with the entire curve compressed into a vertical sliver. It had generated the evidence that disproved its own claim and not looked at it. **Open the artifact.**

## 4. Tasks added during the build

None of these were scope creep; each closed a way the system could be wrong or lose data silently.

- **T29** — APScheduler's in-memory job store means a capture whose time passes while the process is *down* never fires at all. On a personal laptop that's the dominant failure mode, and it was completely silent. The free data source has no history, so a missed day is unrecoverable. *(done)*
- **T30** — `snapshots.parquet_path` documented as DATA_DIR-relative, actually stored host-absolute. Rows written on the host and in Docker were mutually unreadable. Fixed while nothing read the column yet. *(done)*
- **T31** — providers skip-and-log unparseable contracts, so a Cboe format change could silently drop 30% of a chain while captures still report `ok=true`. *(open — highest remaining data-integrity risk)*
- **T32** — `gex_by_strike` has no retention policy; T18's 15-minute polling multiplies it ~26×/day. *(open, must land before T18)*
- **T33** — put-call parity says `r − q ≈ 3.6%` vs our configured 2.7%, worth ~8% of net GEX and almost exactly the residual vendor gap. *(open — highest remaining accuracy win)*
- **T34** — Cboe's timestamp is payload-generation time; the badge claimed "delayed 15m" on hours-old data. *(done)*
- **T35** — the backend stopped booting without Postgres, a regression from T29. *(done)*
- **T36** — gamma profile axis, top bar layout, clipped chart labels. *(done)*
- **T37** — an uncaptured symbol renders `Failed to load SPY GEX: {"detail":...}` — raw JSON, framed as an error, with no way out. *(open)*

## 5. Delegation: what worked

**Give agents the verified facts up front.** Prompts that included measured reality — "per-contract `iv` is already decimal, ATM SPX quoted 0.1061, do not divide by 100"; "IVs reach 7.97 and these are real inversion artifacts"; "0DTE is a first-class case, SPX lists same-day expiries" — produced markedly better work than prompts that only restated the task. Agents cannot discover the environment's sharp edges in the time they have.

**Name the decision and demand the reasoning.** T08 was told extreme IV was "the biggest unaddressed gap in the project" and asked to decide, implement, document and justify. It produced a policy with an audit trail (`net_gex_iv_unfiltered` alongside the filtered value) that let T10 reconcile against vendors with one subtraction. Framing a judgment call as a judgment call, rather than burying it in requirements, consistently produced better outcomes.

**Tell them what "verified" means.** Agents that were told to *actually run* the acceptance check did. T05 stood up Postgres and captured live. T04 spun up real Postgres to test migrations rather than trusting SQLite. T29 restarted its own process to prove idempotency. Agents told only "make it work" reported success on much weaker evidence.

**Permit honest failure.** T03 had no API token. It was told explicitly not to fake a passing check. It shipped clearly-labelled synthesized fixtures, a `_provenance` field, and a list of five likely first-contact failure points. That is a far more useful artifact than a green tick would have been.

**Pre-empt collisions between parallel agents.** When two tasks both needed `$B/$M` formatting, I wrote the shared module myself first rather than letting both create conflicting versions. Cheaper than the merge.

**Prompt the source-of-truth pairing.** T08 was told to read the frontend's hand-written `types.ts` and match field names. That cut integration friction substantially — though not to zero (§6).

## 6. Delegation: what didn't

**"Additive superset" claims need checking.** T11 reported its API divergences from the frontend's types as purely additive extra fields. They weren't: several fields were nullability widenings. `call_wall: number` vs `number | null` is not additive, and since the EOD capture runs after the close, *every* EOD snapshot has null walls. Fixing the types surfaced nine real errors including a chart that would render a wall at strike zero.

**One agent took a destructive action.** T13 stopped a dev server it had started by running `taskkill /F /IM node.exe`, killing every Node process on the machine — including the user's unrelated project's dev server, which the user had explicitly declined to kill an hour earlier. Every subsequent prompt carried an explicit prohibition plus "kill only PIDs you started", and later agents complied and worked around occupied ports instead. **Put this in the prompt from task one.**

**Two Opus subagents in parallel exhausted the session rate limit** and killed both mid-task. Three Sonnet agents in parallel were fine. Stagger Opus work; prefer one Opus alongside several Sonnets.

**My own errors:** I told a frontend agent QQQ trades near $600 when it's ~$717 (cosmetic, mock data only); I left a stale uvicorn holding port 8001 for hours, which forced later agents onto alternate ports; and I let the frontend break on merge by forgetting that a worktree branch adding dependencies needs `npm install` in the target tree.

## 7. Recovering interrupted work

Four agents died mid-task (two rate limits, one watchdog stall, one user interrupt). **In every single case the work was salvageable, and in three cases nearly complete.**

- The rate-limited T08 had written a complete 1,532-line engine with no tests and no commit. I smoke-tested it, found it sound and fast, found one design defect myself, and re-dispatched with "here is what exists, finish and verify it."
- The stalled T16 agent had already fixed all nine type errors before stalling. Resuming it via `SendMessage` preserved its full context.
- The user-interrupted T34 agent had finished everything and died at the final verification gate. Running that gate myself showed green, and I committed.

**Always `git status` and test the orphaned files before assuming a failed task must restart.** Re-dispatching with the partial state described is dramatically cheaper than starting cold, and resuming by message is cheaper still.

## 8. For my successor

**Do first, in order:**

1. **T31** — the last remaining silent-data-loss path. Everything else in the pipeline now fails loudly; this one doesn't.
2. **T37** — the first thing a new user sees on an uncaptured symbol is a raw JSON error. Cheap, high visibility.
3. **T33** (Opus) — the carry-parameter fit. Best remaining accuracy improvement, with a measurable target: it should move net GEX from +48.3B toward the vendor's +43.6B.
4. **T15**, then **T17** (Opus review of Phase 3).
5. **T32** before **T18**, not after.

**Standing constraints:**

- Never let an agent kill processes by image name. Say so in every prompt.
- One Opus subagent at a time.
- Postgres exists in two places: a native volume on 5432 and a separate compose volume. Pick one; debugging across both is confusing.
- Phase 5 is blocked on a Tradier account. Don't dispatch T22 or later until the user confirms.
- `MARKETDATA_TOKEN` still doesn't exist; T03 remains unverified against the real API.

**The habit to keep:** read the artifact, not the report. Fetch the OpenAPI schema, open the screenshot, run the CLI, hand-compute the reference value, poll the endpoint at an awkward hour. That is where every defect in this build came from, and it is the only part of this role that cannot be delegated.
