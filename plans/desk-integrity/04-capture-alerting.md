# Something has to watch the health endpoint

`F4` from the 2026-09-21 review. Second in the review's own priority order, and the only item
here that is about a failure *recurring* rather than a failure *existing*.

## Goal

A multi-session capture outage becomes loud within one session instead of being discovered
twelve days later by someone reading a leaderboard.

## What the user sees

September quarterly opex week is simply missing:

| Date | Symbols | Snapshots |
|---|---|---|
| 09-09 | 28 | 48 |
| 09-10 | 28 | 28 |
| 09-11 | 28 | 85 |
| **09-14 → 09-18** | **0** | **0** |
| 09-19 (Sat) | 3 | 3 |
| 09-20 (Sun) | 25 | 25 |
| 09-21 | 28 | 144 |

Five open sessions, the whole 28-symbol universe, nothing. Recovery was partial and staggered
— 3 symbols Saturday, 25 Sunday, 28 Monday — which suggests the worker came back on its own
rather than being restarted.

**Cost:** QQQ's gamma regime flipped from −2.17bn (09-11) to +4.80bn (09-21) entirely inside
the gap, across the quarterly. Both endpoints exist; the path does not. Per the project's own
scope guardrails, "a capture that does not happen is gone permanently" and anything risking a
capture is a P0 — which makes an unwatched outage a P0 by the same logic.

## Data

Nothing new. `GET /api/gex/health/capture` already exists (`T29`) and already computes exactly
the right thing. `api/health.py:210` even logs it:

```python
log.warning("health.capture: %s EOD capture is stale (last=%s, expected up to=%s)", ...)
```

**That line only runs when someone calls the endpoint.** The detection was built; the watching
was not. This task is the watching.

## Design decisions

### 1. Who polls, and from where

The API process **starts no background work** — invariant 7, and two schedulers means every
capture fires twice. So the poller is not in the API. Options, in rough order of preference:

- a small periodic check inside an existing worker container;
- a new tiny container alongside the others;
- an external uptime checker hitting `/api/gex/health/capture`.

An external checker has one decisive advantage: **it survives the thing it is watching.** An
in-container poller that dies with the container it lives in reproduces exactly the silence
being fixed. Weigh that against this being a single-user homeserver with no existing external
monitoring, and argue the choice.

### 2. Where the alert goes — Telegram

**Decided by the user, 2026-09-21: Telegram.** A bot message via the Bot API — one outbound
HTTPS POST, no inbound port, no third-party service to pay for, and it reaches a phone. That
last part is the requirement: the failure mode is twelve days of not looking at the desk, so
the launcher at `/` showing a red banner is necessary and not sufficient.

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` come from the environment, never from a compose
file. Note the existing trap, already documented in the `market-research` skill for
`XA_FRED_API_KEY`: **compose only picks up a changed `.env` on container *recreate*, not
restart.**

### 2b. Telegram does not solve the dead-man's-switch problem

An alerter that posts to Telegram still only posts while it is running. If it lives in the
container that died, the outage is silent exactly as before — see decision 1. Two ways to
close it, and picking one is part of this task:

- **a heartbeat** — the alerter posts a short "capture healthy" message on a slow cadence
  (daily, say), so silence itself becomes the signal a human will notice;
- **an external cron** — something off the box hits `/api/gex/health/capture` and posts.

The heartbeat is cheaper and needs no new infrastructure, but it relies on the user noticing
an absence. Argue the choice and record it.

### 3. Alert on the gap, not on every stale symbol

A single symbol 27 minutes stale is normal — `STALE_THRESHOLD_MINUTES` in `scan/regime.py`
documents measured per-symbol lags (XBI 4h21m, GDX 27m). The alertable event is *the whole
universe silent across a session*, which is categorically different and unambiguous in the
data. An alert that fires on ordinary staleness will be muted within a week and then the next
outage is silent again.

### 4. Say in the docs that backfill is impossible

The review asks for this explicitly. Options open interest cannot be reconstructed after the
fact. Whether `jobs/catchup.py` should have tried is a separate question; what must be written
down is that **recovery is not available**, so nobody plans around it.

## Tasks

### T104 · Sonnet · —

An outage alert on top of the existing capture-health endpoint, delivered to **Telegram**.
Something outside the API process polls it on a schedule, distinguishes a universe-wide
multi-session gap from ordinary per-symbol staleness, and posts to a bot. A heartbeat or an
external check closes the case where the alerter dies with the thing it watches (decision 2b).
`context/data-and-ops.md` states plainly that a missed options capture is unrecoverable.

Paths: `backend/app/workers/`, `backend/app/modules/gex/api/health.py` (read-only unless the
poller needs a field it does not expose), `compose.yaml`, `compose.prod.yaml`,
`context/data-and-ops.md`, `backend/tests/`.

Independent of every other task here; dispatch in parallel with `T105`.

## Verified facts

- The 09-14 → 09-18 gap is real, universe-wide, and reproduces with:

  ```sql
  SELECT captured_at::date AS d, count(DISTINCT underlying) AS symbols, count(*) AS snaps
  FROM gex.snapshots WHERE captured_at >= '2026-09-05'
  GROUP BY 1 ORDER BY 1;
  ```

- `GET /api/gex/health/capture` exists, is the container healthcheck's sibling, and already
  computes per-symbol staleness plus `eod_captured_today`.
- `api/health.py` already logs a warning for stale EOD capture — on request only.
- `catchup_skipped … "not a trading day"` is **correct behaviour**, not an outage. An alert
  that fires on it is a false positive and will get muted.
- Invariant 7: the API process runs no background work, and `app/main.py` has no lifespan.
- **Not checked, and still needs shell access on the homeserver:** the `gex-capture` container
  logs for 09-12 → 09-19, and whether `jobs/catchup.py` attempted anything.

## Acceptance

1. Simulate a universe-wide gap — a test clock or a fixture database with 09-14 → 09-18 missing
   — and the alert fires. Run it.
2. Simulate ordinary staleness (GDX 27 minutes; a legitimate non-trading day) and it does
   **not** fire. This is the half that decides whether the alert survives contact.
3. **A real Telegram message arrives on the user's phone** during the simulated outage. Not a
   mocked transport — send one.
4. Kill the container the poller runs in and state, in the `Result` section, what happens. If
   the answer is "nothing alerts", that is the decision 1 / 2b tradeoff and must be recorded,
   not hidden.
5. `context/data-and-ops.md` says backfill is impossible, in those words.
6. `uv run pytest` and `uv run ruff check .` clean; `docker compose up` still works with and
   without the override.

## Likely first-contact failures

- **Putting the scheduler in the API process.** Invariant 7. Every capture then fires twice.
- **Alerting on per-symbol staleness**, which is normal and will train the user to ignore it.
- **Treating `catchup_skipped` as an outage.**
- **A poller inside the container it watches**, without saying so — see acceptance 4.
- Assuming the outage can be backfilled.
- **Committing `TELEGRAM_BOT_TOKEN` to a compose file** rather than reading it from the
  environment. And note compose only picks up a changed `.env` on container **recreate**, not
  restart -- the same trap that silently disables FRED ingestion.
- Mocking the Telegram transport and never sending a real message. Acceptance 3 exists because
  a bot token, a chat id and a blocked outbound port all fail identically in a unit test.
- A Telegram rate limit or a network blip taking down the poller. Failing to alert must never
  crash the thing doing the alerting.

## Out of scope

- Root-causing the September outage. The logs need homeserver shell access and were unreadable
  from both review passes. If they survive, file the post-mortem separately.
- Whether `jobs/catchup.py` should have backfilled. Worth answering; not this task.
- Alerting for the research or terminal modules. Same pattern, different data, later.
