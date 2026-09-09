# Working on this project

## How work is organized

`PLAN.md` holds the architecture and the six-phase roadmap. `TASKS.md` breaks it into
numbered, self-contained tasks (`T00`–`T54` so far) in `ID · Model · Depends on` form. Most
of the build has been executed by delegating those blocks to agents.

Initiatives too big for one block get a folder under `plans/` (see `plans/README.md`): one
file per tool, each carrying its tasks in the same block shape, verified facts, and likely
first-contact failures. `TASKS.md` keeps a one-line pointer per task so IDs never collide.
The first such initiative is `plans/continuation/` (T42–T54, 2026-09-09).

Current position: Phases 0–3 are built (ingestion, engine, read API, dashboard). Phase 4
(15-minute intraday polling + SSE) is next. Phase 5 (real-time) is blocked on a Tradier
account. `docs/state-review-2026-09-05.md` §4 is the live prioritized list of what to do next
and what is known-broken.

When a review or a user report produces new work, **append it to `TASKS.md`** as a new
numbered task with the same block shape rather than fixing it silently — that file is the
project's memory of why things were done.

## Delegating a task

The standing prompt preamble: *"Read PLAN.md first. Work only inside the paths listed. Do not
change the public interfaces defined in earlier tasks. Run the tests before reporting done."*

Hard-won additions, from `docs/supervision-report.md` §5–6 — include them from task one:

- **Never kill processes by image name.** One agent ran `taskkill /F /IM node.exe` and killed
  every Node process on the machine, including the user's unrelated work. Only kill PIDs you
  started; work around an occupied port instead.
- **Give the agent the verified facts up front.** Measured reality ("SPX `iv` is already
  decimal, ATM quoted 0.1061, do not divide by 100"; "IVs reach 7.97 and these are real
  inversion artifacts") produces markedly better work than a restated task. Agents cannot
  discover the environment's sharp edges in the time they have.
- **Frame judgment calls as judgment calls** — name the decision, demand the reasoning and an
  audit trail, rather than burying it in requirements.
- **Define what "verified" means** and require the acceptance check to actually be run.
- **Permit honest failure.** A clearly-labelled synthetic fixture plus a list of likely
  first-contact failures beats a fake green tick.
- **Never run two Opus agents concurrently** — it exhausts the session rate limit and kills
  both. One Opus alongside several Sonnets is fine.
- **Pre-empt collisions** between parallel agents by writing the shared module yourself first.
- **Verify the artifact, not the agent's report.** "Purely additive" API changes turned out to
  include nullability widenings that hid nine real type errors.
- **A worktree branch that adds dependencies needs `npm install` / `uv sync` in the target
  tree after merge.**

If an agent dies mid-task, `git status` and test the orphaned files before restarting.
Historically the work was salvageable every time, and usually nearly complete — resuming by
message, or re-dispatching with "here is what exists, finish and verify it", is far cheaper
than starting cold.

## Before you commit

Run both suites and both linters (see `CLAUDE.md`). CI runs backend lint+test, frontend
lint+test, and a Docker build of both images; nothing is merged red.

Commit subjects follow the existing log: imperative, and referencing the task where one
applies — `T09: persist computed GEX levels at capture time`, `fix(backend): T35 -- unreachable
Postgres must not hang startup`, `Make a fresh docker compose up actually work`.

There is currently **no git remote** — the repo exists only on this machine and is
unbacked-up. That is item P0 in the state review.

## Scope guardrails

- Analysis and charts only. **No order routing**, no broker write APIs.
- Single user. No auth, no multi-tenancy, no redistribution of market data.
- Data spend stays under $50/month.
- Free-tier data has no history: a capture that does not happen is gone permanently. Anything
  that risks the 16:20 capture is a P0.
