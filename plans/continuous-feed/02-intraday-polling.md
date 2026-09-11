# Intraday polling and live updates (T18, T19, T20)

The free tier of the continuous feed. These three tasks were specified in `TASKS.md` on
2026-09-04 as Phase 4 and never built; this file is the full spec that supersedes those
three-line blocks. The `TASKS.md` entries stay as the numbered index and point here.

## Goal

During a session the app shows a chain that is at most 15 minutes old, refreshing on its own,
and the session's history of walls and flip point can be scrubbed after the fact.

## What the user sees

- The freshness line in `ContextBar` ticks forward through the session instead of sitting on
  yesterday's close.
- Charts update without a manual reload and without a full-page flicker.
- A new **Intraday** entry in the *Analyze* nav group: for a chosen date, flip point, call
  wall, put wall and spot plotted across that session's snapshots, with a slider that scrubs
  the GEX-by-strike chart through the day.

## Data

No new provider and no new vendor. `providers/cboe.py` unchanged, called more often.

---

## T18 · Sonnet · T32, T71

**Intraday polling job**

Register `capture_intraday` in `app/jobs/scheduler.py`: Mon–Fri, every 15 minutes from
**09:45 to 16:15 America/New_York**, all of `settings.symbols`, `is_eod=False`, with
`compute_and_store` running after each capture exactly as the EOD path already does. 27 fires
per session.

The natural trigger is `CronTrigger(day_of_week="mon-fri", hour="9-16",
minute="0,15,30,45", timezone=_TZ)` — 32 fires — with an in-job window guard dropping 09:00,
09:15, 09:30, 16:30 and 16:45. Guard on holidays too, via `is_trading_day`, the same belt-and-
suspenders the EOD job uses.

### Design decisions

**Misfire policy is the opposite of the EOD job's, deliberately.** The EOD jobs use
`misfire_grace_time=None` and `coalesce=True` because a laptop closed at 16:20 is the normal
case for this user and a late run still captures the correct settled close. **Intraday has
nothing to rescue**: the endpoint serves only "now", so a run that fires at 14:03 for the
10:00 slot does not recover the 10:00 reading — it just adds an off-grid one. Use a short
`misfire_grace_time` (300 s) with `coalesce=True`, and say why in the module docstring next to
the existing EOD rationale, because the two policies sitting side by side will otherwise read
as an inconsistency.

**`INTRADAY_ENABLED` defaults to `False`.** Shipping this must not silently start a 27×/day
poll from a laptop that is open half the time — that produces a ragged, misleading partial
series and burns the informal rate limit for nothing. Flip it to `True` on the `T70` host,
where a full series is actually achievable. Note the default and the reason in `.env.example`.

**Rate limit.** The free source's informal limit is one request per symbol per 15 minutes, and
this cadence sits exactly on it, with no headroom. So: no retry-at-the-same-interval on
failure — a failed slot is skipped and logged, not retried. `capture_all_symbols` already
turns per-symbol failure into a logged `CaptureResult` rather than an exception, which is the
behaviour this needs; do not add retry logic on top of it.

**Cost per round.** `compute_all` on a full 28,650-contract SPX chain including the 201-point
gamma profile runs inside the 2 s budget (T08's measured figure), so a five-symbol round is
on the order of ten seconds of work every fifteen minutes. No batching or queueing is needed;
resist adding any.

Acceptance: with the clock faked across a trading day, exactly 27 captures fire, the first at
09:45 and the last at 16:15, none on a holiday, and the 16:20 EOD job still produces its
`is_eod=true` row (or promotes one, per `T71`).

---

## T19 · Sonnet · T18

**Server-Sent Events stream**

`GET /api/stream/{underlying}`, `text/event-stream`, emitting an event whenever a new
snapshot's levels are stored. Frontend hook `useLiveLevels` invalidates the relevant
TanStack Query keys on each event; `ContextBar` shows last-updated and a delay badge.

### Design decisions

**This is greenfield — verified 2026-09-11, there is no SSE, `EventSource` or
`text/event-stream` anywhere in the backend or frontend today.** There is no existing pattern
to follow, so the shape is this task's to establish.

**In-process pub/sub, deliberately.** One process, one user, at most a couple of browser tabs.
An `asyncio` fan-out of `Queue`s registered by connected clients is the whole mechanism —
`compute_and_store`'s caller publishes after the store commits. Do **not** reach for Redis, a
broker, or a database `LISTEN/NOTIFY` channel. If the app ever becomes multi-process this
decision gets revisited with real requirements; today that would be architecture for an
audience of one.

**Publish from the capture path, not from inside `app/gex/`.** Invariant 1: `engine.py` and
`greeks.py` are pure — no HTTP, DB, filesystem or logging. The publish belongs in
`app/jobs/capture.py` after the store returns, alongside the existing structured log line.

**The badge must read `effective_data_time`, not `captured_at`.** T34's finding: Cboe's
timestamp keeps advancing after the close while the data underneath is frozen, so a naive
"updated 30 seconds ago" badge lies every evening. `app/jobs/calendar.py` already exports
`effective_data_time(captured_at, delayed_minutes)` and `app/api/` already uses it in three
routers (`chains.py`, `gex.py`, `scan.py`) under the field name `effective_at`. Use the same
field; do not invent a second freshness concept.

**Keep-alive.** Emit a comment line every ~15 s so an idle connection is not culled by a proxy
or by the browser. `EventSource` reconnects on its own, so no client-side retry logic is
needed — but the server must tolerate a client vanishing mid-write without leaking its queue.

Acceptance: with the backend running, a manual `POST /api/snapshots/capture` causes a
connected browser to update its levels without a reload, and closing the tab leaves no
orphaned subscriber.

---

## T20 · Sonnet · T18

**Intraday timeline view**

For a chosen date: flip point, call wall, put wall and spot across that session's snapshots,
plus a slider scrubbing the GEX-by-strike chart through the day.

### Design decisions

**Route placement.** The UI was rebuilt into Today / Analyze / Review workspaces by T62–T69.
A twelfth top-level route added carelessly would undo that. Add **Intraday** to the *Analyze*
group in `frontend/src/components/layout/navConfig.ts` — that file is, by its own docstring,
the single place a new route must be declared, and both `SideRail` and the Ctrl/Cmd+K
`CommandPalette` pick it up from there. Do not touch either component directly.

**The timeline reads `gex_levels`, not Parquet.** One row per (snapshot, filter) already
carries `flip_point`, `call_wall`, `put_wall`, `spot` and `computed_at` — that is precisely
the timeline's series, and the table exists so this kind of view never reopens Parquet.
`GET /api/gex/{underlying}/levels/history` already exists; check whether it can be given a
single-day/intraday mode rather than adding a parallel endpoint.

**The scrubber does hit strike detail**, which after `T32` exists only for the last
`INTRADAY_STRIKE_RETENTION_DAYS` days. Scrubbing a pruned day must show a clear "strike detail
for this day has been pruned; levels still available" empty state, not a crash and not a blank
chart. This is the one user-visible consequence of the retention policy and it has to be
designed, not discovered.

Acceptance: for a day with a full 27-snapshot series, the timeline renders four series and the
slider moves the by-strike chart through all 27 without refetching the whole day per step.

## Verified facts (2026-09-11)

- No SSE/`EventSource`/`text/event-stream` anywhere in `backend/app` or `frontend/src`.
- `app/jobs/scheduler.py` currently registers six cron jobs plus the startup catch-up, with
  `_TZ = ZoneInfo(settings.TZ)` shared by triggers and calendar checks.
- `app/jobs/calendar.py` exports `MARKET_OPEN` (09:30), `MARKET_CLOSE` (16:00),
  `is_regular_session`, `is_trading_day` and `effective_data_time`. Its docstring explicitly
  notes that `MARKET_CLOSE` is the exchange close and **not** `catchup.EOD_CUTOFF` (16:20),
  "which would clamp a legitimate 16:05 intraday capture (T18)" — this module was written with
  T18 in mind and should not need changes.
- `gex_levels` columns include `flip_point`, `call_wall`, `call_wall_gex`, `put_wall`,
  `put_wall_gex`, `max_abs_strike`, `max_call_gex_strike`, `max_put_gex_strike`, `net_gex`,
  `spot`, `computed_at`. Every level column is nullable **on purpose** — a missing wall reads
  back as `None`, never `0`, and the timeline must not plot `None` as zero.
- `navConfig.ts` is the shared nav table for `SideRail` and `CommandPalette`.
- Existing routes: `/`, `/decisions`, `/dashboard`, `/regime`, `/scan`, `/rotation`, `/flows`,
  plus the Review group.

## Likely first-contact failures

- **The working tree is dirty.** The T62–T69 UI refresh is uncommitted as of 2026-09-11
  (`AppShell.tsx` deleted, `AppFrame`/`SideRail`/`ContextBar`/`CommandPalette` untracked).
  A frontend agent that starts from `HEAD` will rebuild against a shell that no longer exists.
  Commit the refresh, or hand the agent the working tree and say so explicitly.
- Plotting `None` walls as `0` on the timeline — see the nullability note above. `ZERO_DTE`
  legitimately has no levels at all on an EOD capture.
- Letting the SSE generator hold a DB session open for the life of the connection.
- Testing the cron trigger by waiting for real time rather than by inspecting the registered
  job — `tests/` already has the pattern of building a scheduler and reading its jobs without
  starting it.
- Assuming 27 snapshots exist while developing. Until `T70` and `INTRADAY_ENABLED=true`, the
  database has one snapshot per day; build the timeline against a seeded fixture series.

## Out of scope

Real-time anything — that is `T21`–`T23`. Backfilling intraday history for days already past:
impossible on this source, by construction.

---

## Result — T18, shipped 2026-09-11

`INTRADAY_ENABLED` in `app/config.py`, `capture_intraday_job` plus conditional registration in
`app/jobs/scheduler.py`, and 11 new tests. Backend suite 938 passed (was 927), `ruff check .`
clean.

**Shipped as specified**, with the three guards in order (flag, trading day, window), the
inverted misfire policy (`misfire_grace_time=300`, against the capture jobs' `None`), and a
test asserting those two policies differ so the inconsistency cannot later be "fixed" by
mistake. Registration is conditional on the flag, so `scheduler.get_jobs()` states what will
actually run rather than listing a job that always no-ops.

`INTRADAY_ENABLED` defaults to **False**, now reinforced by the user's 2026-09-11 decision to
stay on the laptop (T70 deferred): leaving polling on under that arrangement accumulates a
series whose gaps are invisible in the data itself.

### Verified live, 2026-09-11 13:15-13:16 ET (inside the window, a trading Friday)

Ran `capture_intraday_job` twice against the real Cboe endpoint, 45 seconds apart. Both rounds
captured all five symbols; SPX 29,162 contracts in 2.4 s, spot 7671.24, with levels computed
for all three filters (ALL net GEX +38.4 B, flip 7645.6, call wall 7675, put wall 7500) and
`ZERO_DTE` correctly populated on a Friday. Nine new `is_eod=false` rows, every one carrying a
`content_hash`.

**Two findings worth recording.**

1. **Cboe refreshes faster than every 15 minutes.** Four of five symbols returned genuinely
   new data 45 seconds apart (SPX spot moved 7671.24 → 7670.28). So the 15-minute cadence is a
   *rate-limit policy*, not a data-availability limit — there is more resolution available than
   this app takes, and taking it would burn the informal budget. Worth knowing before anyone is
   tempted to "fix" a cadence that looks conservative.

2. **A correction to this plan's framing of the content key.** DIA's second fetch returned a
   payload whose vendor timestamp had *not* advanced (16:58:31 both times), and it was caught
   on the `captured_at` key — `duplicate_reason: "captured_at"`, one row, one Parquet file. So
   intraday, when the feed has not refreshed, the timestamp has not refreshed either, and the
   **timestamp key does the everyday work**. T34's divergence — the timestamp advancing over
   frozen quotes — was observed *after the close*, which is where the content key earns its
   place. This plan and T71's implied that the content key would be the common case for
   polling. On this evidence it is the safety net, not the workhorse. It costs one hash per
   capture and is still exactly right for the after-hours case, so nothing changes in the code;
   the claim is corrected rather than left overstated.

---

## Result — T19, shipped 2026-09-11

Backend: `app/events.py` (new broker), `app/api/stream.py` (new SSE route), a publish in
`app/jobs/capture.py`, router registration. Frontend: `src/api/useLiveLevels.ts` (new hook), a
`LiveIndicator` in `ContextBar`, styles on the existing `--status-positive`/`--status-negative`
tokens. 15 new backend tests and 9 new frontend tests; suites 953 backend / 343 frontend,
`ruff`, `eslint` and `tsc -b` all clean.

**Shipped as specified.** In-process `asyncio.Queue` fan-out, no broker or Redis; publish from
the capture path rather than from `app/gex/` (invariant 1); `effective_at` on the event rather
than raw `captured_at` (T34); a `ready` frame on connect; keep-alive comments; the subscriber
queue released in a `finally`.

**Decisions made while building.**

- *The publish is in the `else` of the level-computation `try`, not after it.* A client woken by
  the event re-fetches immediately, so publishing before the level rows are committed — or after
  a failed computation — would serve it the *previous* snapshot's levels and leave it stale until
  the next capture. There is a test for the failure case.
- *A duplicate capture still publishes.* The levels are unchanged, but a client that reconnected
  since the last event cannot know that, and a redundant re-fetch is cheaper than a dashboard
  sitting on stale data because the server decided the nudge was unnecessary.
- *Oldest-first dropping under backpressure.* For a "something changed" signal the newest event
  strictly dominates: a client that receives only the latest of several dropped events does the
  right thing, while one that receives the oldest re-fetches stale data.
- *An explicit `is_disconnected` check each loop.* At one capture per fifteen minutes almost every
  disconnect happens while nothing is being published; without it the generator would sit in
  `wait_for` until the next capture before noticing, holding its queue the whole time.
- *`LiveIndicator` renders nothing where `EventSource` does not exist,* and says "Reconnecting…"
  rather than hiding a dead stream. The value of this channel is that the freshness stamp can be
  trusted without a reload, and that trust is only warranted while the connection is up.

**A testing note worth keeping.** Driving the SSE route through `TestClient.stream` hangs: the
response is an endless generator and the client's context exit waits for a body that never ends.
That is correct behaviour for an SSE channel and a bad shape for a test. The route's headers are
therefore asserted by calling it directly, and the streaming behaviour by driving
`_event_stream` on the test's own loop with a fake request. A `broker.publish` from the
`TestClient`'s thread would also enqueue without waking the loop's pending `get()` —
`asyncio.Queue` is not thread-safe, and a test built that way passes or hangs on timing.

**Verified live, 2026-09-11 13:30 ET,** against a real backend on a spare port with a real Cboe
capture:

```
event: ready
data: {"underlying": "SPX"}

event: levels
data: {"underlying": "SPX", "snapshot_id": 100, "captured_at": "2026-09-11 17:29:32+00:00",
       "effective_at": "2026-09-11 17:29:32+00:00", "is_eod": false, "spot": 7669.1699,
       "skipped_duplicate": false}
```

`ready` arrived on connect; the `levels` frame arrived when `POST /api/snapshots/capture`
returned 201; two keep-alive comments went out during the idle stretch; and the server logged
`stream_subscribed subscribers: 1` then `stream_unsubscribed subscribers: 0` when the client
went away — so the queue is released on disconnect in practice, not only in the unit test.
