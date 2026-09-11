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
