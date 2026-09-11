# Always-on host (T70)

## Goal

The backend process runs continuously through every trading session, so that scheduled
captures fire without depending on whether a laptop happens to be open.

## What the user sees

Nothing new in the UI. What changes is that `GET /api/health/capture` stops being a way to
discover holes after the fact, because the holes stop being created.

## Why this is in a data plan at all

`app/jobs/scheduler.py` builds an `AsyncIOScheduler` with an **in-memory** job store. Jobs
fire only while the process lives. Today six jobs hang off it (EOD 16:20, safety net 20:00,
extended 16:45, bars 17:30, decisions 17:45, flows 18:30) and all six are recoverable after
the fact:

- `T29`'s `startup_catchup_job` re-runs a missed EOD, because Cboe keeps serving the settled
  chain into the evening.
- Bars, flows and decisions all read sources that can be asked about yesterday.

**Intraday capture is the first job in this project with no recovery path at all.** The Cboe
delayed-quotes endpoint serves exactly one thing: now. A 15-minute slot that is not captured
while it is happening is gone permanently, and the loss is silent — no failed job, no log
line, no row. `context/workflow.md` already classifies that as P0.

## Design decisions

**Judgment call: where it runs.** Three options, and the user picks.

| Option | ~Cost | Pros | Cons |
|---|---|---|---|
| Small VPS (Hetzner/Fly/DO) | $4–6/mo | genuinely always on, independent of the house | needs the deploy story below; power/network is someone else's problem |
| Raspberry Pi or spare box on the LAN | one-off hardware | no recurring cost, data stays local | house power and ISP outages are now data outages |
| Status quo: the laptop | $0 | nothing to build | every closed lid is a permanent hole; defeats the purpose |

The recurring cost here is **hosting, not data** — the `$50/month` guardrail in
`context/workflow.md` is a data-spend budget. Worth stating explicitly in whichever direction
the user decides, so a later reader does not think the budget was quietly broken.

**Judgment call: exposure.** The app has no auth, no multi-tenancy, and by design never will
(`context/workflow.md` scope guardrails). It also holds market data under a single-user,
non-redistribution licence. So a public VPS must **not** expose 8001 or 5173 to the internet.
Bind to loopback and reach it over Tailscale/WireGuard or an SSH tunnel. This is a
requirement, not a suggestion: a publicly reachable unauthenticated instance is both a
security problem and a licence problem.

**Hard dependency: there is no git remote.** `context/workflow.md` records this as item P0 of
the state review — the repo exists only on this machine and is unbacked-up. You cannot deploy
what you cannot push. Creating a private remote is therefore step one of this task, and it
also closes the standing backup gap, which is worth doing whether or not the host happens.

**Data lives with the scheduler.** Postgres and `DATA_DIR` move to the host with the backend;
they are not split across the network. The existing compose file already binds `./data` to
`/data` and names a `pgdata` volume, so this is a copy, not a redesign. Migrating the existing
captures matters — they are unbackfillable and represent every EOD since 2026-09-04.

## Tasks

### T70 · user decision, then Sonnet · —
**Always-on host for the scheduler**

1. Create a private git remote and push `master`. (Closes state-review P0 independently.)
2. Stand up the host the user chose. `docker compose up -d` with a restart policy, backend
   bound to loopback, private-network access only.
3. Move the existing Postgres contents and `DATA_DIR` Parquet tree across, verifying row and
   file counts on both sides before the old copy is touched. Nothing is deleted in this task.
4. Set `TZ=America/New_York` on the host explicitly — the scheduler's cron triggers and
   `app/jobs/calendar.py`'s weekday/holiday checks both read `settings.TZ`, and a host that
   defaults to UTC would fire every job at the wrong wall-clock time while looking healthy.
5. Point the local frontend at the hosted backend, or run the frontend there too.

Acceptance: the host survives a reboot with the stack coming back unattended; a capture fires
on a day the user's laptop was never opened, verified by an `is_eod=true` row whose
`captured_at` falls on such a day; `GET /api/health/capture` answers from the host.

## Verified facts (2026-09-11)

- `app/jobs/scheduler.py` uses `AsyncIOScheduler` with no persistent job store configured.
- Six cron jobs are registered plus the startup catch-up; job IDs are exported from that
  module (`EOD_JOB_ID`, `SAFETY_NET_JOB_ID`, `BARS_JOB_ID`, `EXTENDED_JOB_ID`, `FLOWS_JOB_ID`,
  `DECISIONS_JOB_ID`).
- `_TZ = ZoneInfo(settings.TZ)` drives both the trigger times and the holiday check, so a
  wrong host `TZ` moves everything together and silently.
- There is no git remote on this repo as of this date.
- Docker Desktop on the current Windows host needs a manual start before compose works, which
  is itself part of why the laptop is a poor scheduler host.

## Likely first-contact failures

- Copying Parquet between Windows and Linux and breaking `snapshots.parquet_path`: the column
  is stored **relative to `DATA_DIR` with posix separators** (invariant 5, T30). Verify by
  resolving a sample of rows through `storage.parquet.resolve_snapshot_path` on the new host
  rather than assuming the copy was clean.
- A `pg_dump`/restore that silently drops timezone awareness. `captured_at` is tz-aware UTC
  enforced by `UTCDateTime` at the boundary (invariant 4); spot-check a restored row's
  `tzinfo` rather than trusting the dump.
- Two schedulers running at once (old laptop stack still up alongside the new host) — both
  capture, and with `T71` not yet shipped both insert. Shut the old one down deliberately.

## Out of scope

Any form of auth, CI/CD, monitoring or alerting beyond the existing `/api/health/capture`.
This task buys uptime, nothing more.
