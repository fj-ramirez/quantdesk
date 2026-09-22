# Working on this project

## How work is organized

`PLAN.md` holds the architecture and the six-phase roadmap. `TASKS.md` breaks it into
numbered, self-contained tasks (`T00`–`T56` so far) in `ID · Model · Depends on` form. Most
of the build has been executed by delegating those blocks to agents.

Initiatives too big for one block get a folder under `plans/` (see `plans/README.md`): one
file per tool, each carrying its tasks in the same block shape, verified facts, and likely
first-contact failures. `TASKS.md` keeps a one-line pointer per task so IDs never collide.
The first such initiative is `plans/continuation/` (T42–T56, 2026-09-09).

Current position: Phases 0–3 are built (ingestion, engine, read API, dashboard). Phase 4
(15-minute intraday polling + SSE) is next and is now planned in detail in
`plans/continuous-feed/` — read that before touching T18–T20, and note that it gates them on
T32, T71 and an always-on host (T70). Phase 5 (real-time) is blocked on a Tradier account.
`docs/state-review-2026-09-05.md` §4 is the live prioritized list of what to do next and what
is known-broken.

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

## Dispatching a cluster

Re-dispatching a fresh agent per task pays the same cost three times: the agent's cold read of
`PLAN.md`, the context doc, the plan file and the code it is about to touch; the supervisor
rewriting the same preamble, hard rules and verified facts; and the worktree cycle — branch,
merge, `uv sync`/`npm install` in the target tree. The third is usually the largest, and
chaining removes it entirely.

So the unit of dispatch is the **plan file**, not the task, whenever all of these hold:

- one model for the whole cluster (`T32` Opus and `T71` Sonnet stay two agents);
- the same paths, and a strict dependency chain (`T18`→`T19`→`T20` qualifies; two tasks that
  can run in parallel should, in separate worktrees — chaining them only serialises them);
- at most three or four tasks, ending at the plan-file boundary.

How to run one:

- **Spawn once with the whole cluster spec**, not just the first task: "you will do T18, then
  T19, then T20, here is the file." An agent that knows where the chain is going makes better
  structural calls in the first task.
- **Follow up thin.** One message per task: what landed, what the review found, which section
  is next, what changed since. Three lines, not a page.
- **The verification gate does not move.** Verify the artifact after every task, exactly as
  before; the follow-up message is simply the cheapest possible place to feed the findings
  back, because the agent still has the code in context.
- **One commit per task inside the worktree**, so the cluster can be diffed task by task and a
  mid-cluster death leaves a clean resume point.

**Do not trust a long agent's context to survive.** Require it to append to
`plans/<initiative>/NOTES-<cluster>.md` after each task: facts it verified, judgment calls it
made, open items. That file survives compaction, agent death and the end of the session, and
it is the raw material for the *Result* heading `plans/README.md` asks for. When an agent
starts re-asking something it was already told, end the chain and restart cold from the notes
file rather than pushing on.

What must never be reused:

- **`Opus (review)` passes.** Independence is the entire product; an agent reviewing its own
  cluster reviews its own assumptions.
- **Work crossing a plan file** — different paths and different failure modes mean context
  that has to be corrected rather than installed.

`subagent_type: "fork"` inherits the supervisor's context outright, which is the cheapest start
available — but a fork always runs the supervisor's model, so it counts against *never run two
Opus agents concurrently*. Use it only for an Opus task whose context the supervisor has just
finished establishing, never as the default.

## Before you commit

Run both suites and both linters (see `CLAUDE.md`). CI runs backend lint+test, frontend
lint+test, and a Docker build of both images; nothing is merged red.

Commit subjects follow the existing log: imperative, and referencing the task where one
applies — `T09: persist computed GEX levels at capture time`, `fix(backend): T35 -- unreachable
Postgres must not hang startup`, `Make a fresh docker compose up actually work`.

The remote is `origin` (GitHub). This line previously read "there is currently **no git
remote** — the repo exists only on this machine and is unbacked-up", which was true when the
state review filed it as P0 and stopped being true once the remote was added. It was corrected
on 2026-09-21, after an agent read it, repeated it back to the user as fact, and was told
otherwise — the same `F7` failure the `desk-integrity` initiative exists to fix, in the
document that tells agents how to work.

## Scope guardrails

- Analysis and charts only. **No order routing**, no broker write APIs.
- Single user. No auth, no multi-tenancy, no redistribution of market data.
- Data spend stays under $50/month.
- Free-tier data has no history: a capture that does not happen is gone permanently. Anything
  that risks the 16:20 capture is a P0.
