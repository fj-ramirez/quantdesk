# GEX Trading App

Personal, single-user gamma exposure (GEX) analysis app for SPX, SPY, QQQ, GLD, and DIA options.
Analysis and charts only — no order routing. See `PLAN.md` for architecture and `TASKS.md`
for the build plan.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy, Postgres, managed with `uv`.
- Frontend: React + TypeScript + Vite, TanStack Query, ECharts, Lightweight Charts.
- Storage: Postgres for computed results, Parquet files on disk for raw option chain
  snapshots.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (manages Python 3.12 automatically)
- Node.js 20+ and npm
- Docker Desktop (for `docker compose up`)

`make` is not required — a `Makefile` is provided for CI/Docker convenience, but every
target below also has the raw command listed, which is what you need on a machine
without `make` installed (e.g. plain Windows without a Unix toolchain).

## Quick start (Docker, development)

```
cp .env.example .env
docker compose up
```

- Backend health check: http://localhost:8001/health
- Frontend dev server: http://localhost:5173

Bring it down with `docker compose down` (add `-v` to also drop the Postgres volume).

## Compose layout

Three files, with development as the default:

| File | Loaded | Contains |
|---|---|---|
| `compose.yaml` | always | shared service definitions: images, build contexts, app config, ordering |
| `compose.override.yaml` | **automatically**, by plain `docker compose` | dev only: bind-mounted source, hot reload, published ports, the Vite dev server, a `pgdata` named volume |
| `compose.prod.yaml` | only when named with `-f` | production: no bind mounts, no published ports, `restart: unless-stopped`, capped logging, healthchecks, network split |

`docker compose up` reads the first two and behaves exactly as it always has. Production is
the explicit opt-in — and because `-f` replaces the automatic file list, the dev override is
not merged at all rather than being merged and then undone.

Both Dockerfiles are multi-stage with two named targets, `dev` and `runtime`, so the two
environments cannot drift apart into separate files. Each target is also tagged separately
(`gex-backend:dev` vs `gex-backend:prod`): without that they would both take compose's default
project-service name, one build would overwrite the other, and `up -d` without `--build` would
silently start whichever was built last — a prod deploy running the Vite dev server.

### Production commands

```
docker compose -f compose.yaml -f compose.prod.yaml up -d --build      # deploy
docker compose -f compose.yaml -f compose.prod.yaml ps                 # status
docker compose -f compose.yaml -f compose.prod.yaml logs -f backend    # follow logs
docker compose -f compose.yaml -f compose.prod.yaml down               # stop
```

Set it once per shell if you get tired of typing it:

```
export COMPOSE_FILE=compose.yaml:compose.prod.yaml
```

## Deploying to the homeserver

Target: Arch Linux, Docker + compose v2, stack at `/srv/docker/quantdesk/`, managed by a
`compose@quantdesk` systemd template unit whose `WorkingDirectory` is the stack directory (so
`./.env` is picked up automatically). Caddy reverse-proxies from a shared external network.

### 1. Create the shared proxy network (once per host)

```
docker network create edge
```

Caddy must also be attached to it. `compose.prod.yaml` declares `edge` as `external: true`,
so the stack refuses to start if it does not exist — rather than silently creating its own
`gex_edge` that Caddy is not on.

### 2. Add the required variables to the server's `.env`

**This is the step that will bite on first deploy.** The database credentials used to be
hardcoded in the compose file; they are now required variables with no defaults in
production. The server's existing `.env` holds only the `INTRADAY_*` values, so **all three
of these are missing and must be added**:

```
POSTGRES_USER=gex
POSTGRES_PASSWORD=<choose one>
POSTGRES_DB=gex
```

Everything else has a working default. `.env.example` documents the full set.

The failure mode is deliberately loud: compose aborts during config parsing, before creating
anything, and names the variable —

```
error while interpolating services.postgres.environment.POSTGRES_USER:
required variable POSTGRES_USER is missing a value:
required - set it in .env (see .env.example)
```

Two notes on the password:

- It is interpolated into `DATABASE_URL`, which is a URL. Avoid `@ : / ? #` or
  percent-encode them, otherwise the connection string parses wrong.
- `POSTGRES_PASSWORD` **only applies when initialising an empty data directory.** It does not
  change the password of a database that already has data in it — see
  [Rotating the database password](#rotating-the-database-password).

### 3. Create the data directories with the right ownership

Both containers run as non-root and write into bind mounts, so the host directories have to
exist with matching ownership *before* the first `up`. Docker creates a missing bind-mount
source as `root:root`, which both services then refuse to use.

```
sudo mkdir -p /srv/docker/quantdesk/data/postgres
sudo chown -R 10001:10001 /srv/docker/quantdesk/data          # backend's app user
sudo chown -R 999:999     /srv/docker/quantdesk/data/postgres # postgres user inside postgres:16
```

Everything the stack persists lives under `data/`, so the whole thing tars as one unit:

```
sudo systemctl stop compose@quantdesk
sudo tar czf quantdesk-backup-$(date +%F).tar.gz -C /srv/docker quantdesk
sudo systemctl start compose@quantdesk
```

### 4. Add the Caddy site block

Caddy runs as its **own** stack, outside this repo, because it fronts every stack on the host.
The GEX stack publishes nothing; Caddy publishes 80/443 and is the single way in. If you do
not have one yet, [`deploy/compose.caddy.example.yaml`](deploy/compose.caddy.example.yaml) is
a working starting point — copy it and the Caddyfile into their own directory (e.g.
`/srv/docker/caddy/`) and `docker compose up -d`.

**Caddy must be attached to the `edge` network.** `gex-backend` and `gex-frontend` are network
aliases that exist only there, so a Caddy that is not attached fails every proxied request
with `dial tcp: lookup gex-backend: no such host` while looking perfectly healthy itself. This
is the single most likely reason a correctly-deployed stack appears unreachable.

Copy the block from [`deploy/Caddyfile.example`](deploy/Caddyfile.example) into your
Caddyfile. The short version:

```caddyfile
:80 {
	encode zstd gzip

	handle /api/* {
		reverse_proxy gex-backend:8001 {
			flush_interval -1
		}
	}

	handle /health {
		reverse_proxy gex-backend:8001
	}

	handle {
		reverse_proxy gex-frontend:80
	}
}
```

**`handle`, not `handle_path` — the prefix must be preserved.** Every module router in
`backend/app/main.py` is mounted with `prefix="/api"` and carries its own segment on top
(T75), so the backend's real paths are `/api/gex/gex/SPX/latest`, `/api/gex/health/capture`,
`/api/gex/stream/SPX`. `handle_path` strips the matched prefix before proxying, which would
deliver `/gex/gex/SPX/latest` to a backend that has no such route — every request would 404.

`GET /health` is the exception: it is mounted at the root, *outside* `/api`, so it needs its
own matcher or it falls through to the SPA and returns HTML.

`:80` rather than a hostname is deliberate — this host is reached as `homeserver.local` on the
LAN and under a different name over Tailscale, and a site block keyed to one name would serve
only one of those. `deploy/Caddyfile.example` shows the named-site variant if you want
automatic HTTPS.

### 5. Deploy

```
cd /srv/docker/quantdesk
scripts/deploy.sh                                           # pull, build, start, report
```

That is `docker compose -f compose.yaml -f compose.prod.yaml up -d --build` with the commit
stamped into every image, which is the only reason the script exists: a container has no git
repository to ask what it is, and compose cannot run `git` -- it only interpolates the
environment. The bare command still works and is still correct; its images just report
`unknown` when asked which build they are.

```
docker compose -f compose.yaml -f compose.prod.yaml up -d --build   # the unstamped equivalent
docker compose -f compose.yaml -f compose.prod.yaml ps              # every service healthy
curl -s localhost/health                                            # {"status":"ok",...,"db":"ok"}
```

**`GET /health` reports each service separately, and that is the point.** Its `services` array
carries one entry per container -- the API from its own environment, the three workers from
files they write at boot -- each with a `label` in the form
`backend: 2026-09-21T20:14:03-04:00 (cf59b11)`. The launcher at `/` shows the same list, with
the frontend's own stamp compiled into the bundle. On 2026-09-21 a single `up -d --build`
rebuilt four containers and **failed on the frontend**, leaving the stack half-updated with
nothing anywhere saying so; a stack-wide version number would have looked perfectly healthy
that day. Two different shas in that list is the answer, not a glitch.

The backend runs `alembic upgrade head` before uvicorn binds, so a schema migration needs no
separate step.

### "It's running but I can't see it"

Expected. **Production publishes no host ports**, so there is nothing at `localhost:8001`,
`localhost:5173` or `localhost:5432` — that is the entire point of the network split. Caddy on
the `edge` network is the only way in. `docker compose ... ps` showing `8001/tcp` rather than
`0.0.0.0:8001->8001/tcp` is the stack working correctly, not a failure.

To smoke-test the production stack on a machine with no Caddy (a laptop, say), run a throwaway
one on the same network for as long as you need it:

```
docker run -d --name gex-caddy-smoketest --network edge -p 8080:80   -v "$PWD/deploy/Caddyfile.example:/etc/caddy/Caddyfile:ro" caddy:2-alpine
```

The app is then at http://localhost:8080, routed exactly as it will be on the server. Remove it
with `docker rm -f gex-caddy-smoketest`. It is a one-off container on purpose — not a compose
service — so it cannot drift into the real deployment.

Also note that dev and prod share a compose project name (both derive it from the directory),
so they use the same container names and **cannot run at the same time**: each `up` recreates
the containers in the other mode. Switching back to development is just `docker compose up -d`.
The two keep entirely separate databases — dev in the `pgdata` volume, prod in
`./data/postgres` — so a prod stack on your laptop starts with an empty schema while the dev
data sits untouched in its volume.

### Syncing to the homeserver

Four scripts, all Git Bash / POSIX sh, all driven from the stack directory they live in. Both
halves work the same way: one script here writes a file into the server's `backups/`, and one
script there installs it.

```
scripts/db-dump-push.sh      # here:  pg_dump          -> backups/quantdesk.dump
scripts/db-restore.sh        # there: that dump        -> the stack's Postgres
scripts/data-push.sh         # here:  the data delta   -> backups/quantdesk-data.tar.gz
scripts/data-load.sh         # there: that archive     -> data/
```

Nothing here ever writes into `data/` or the database over ssh. `backups/` is writable by an
ordinary ssh account and `data/` belongs to the deploy user (`10001`), so the privileged step
is a local `sudo` on the far side rather than a permission grant you would have to remember
for every new directory the capture creates.

A full sync is the two pushes, then the two installs. The database and the Parquet tree are
two halves of one snapshot — `snapshots.parquet_path` is relative to `DATA_DIR` (invariant 5)
— so a restored index without the chains behind it points at files that are not there.

```
# here
scripts/db-dump-push.sh
scripts/data-push.sh

# there
cd /srv/docker/quantdesk
scripts/db-restore.sh --prod
sudo scripts/data-load.sh
```

The dump always has the same name (`quantdesk.dump`) and the push **replaces** the previous
one, so there is exactly one current dump and `db-restore.sh` needs no argument. Both ends
write to a `.part` file and rename it only on success — an interrupted transfer cannot
destroy the last good dump.

```
# laptop (dev stack)
scripts/db-dump-push.sh                       # --prod to dump a production stack instead
scripts/db-dump-push.sh --local-only          # just write backups/quantdesk.dump

# homeserver
cd /srv/docker/quantdesk && scripts/db-restore.sh --prod
```

Defaults: host `homeserver`, remote directory `/srv/docker/quantdesk/backups` — i.e. `backups/`
inside the stack directory, which is where `db-restore.sh` looks by default on that side.
Override with `--host` / `--remote-dir` or `QD_REMOTE_HOST` / `QD_REMOTE_DIR`.

Credentials are never read host-side. Both scripts run `pg_dump`/`pg_restore` inside the
`postgres` container and use the `POSTGRES_*` variables that container already has, so they
cannot disagree with the compose file about which database they are touching.

What `db-restore.sh` does beyond `pg_restore`:

- **Stops `backend` and the three workers** for the duration and starts them again afterwards,
  including on failure. `--clean` drops every table, and a capture writing through that either
  blocks the drop on a lock or writes into a table that is about to vanish. `--no-stop` opts out.
- **`--single-transaction`**, so a restore that fails half way leaves the database untouched.
  It implies `--exit-on-error`; `pg_restore`'s default is to log errors, continue, and exit 0.
- **Re-applies `quantdesk_ro`'s grants.** Necessary, not defensive: dropping a schema takes its
  `ALTER DEFAULT PRIVILEGES` entry with it, so the tables the restore then creates would carry
  no grant at all and the MCP connector (T82) would read an empty database through a role that
  still logs in fine. Alembic will not repair it — the version row came back with the dump.
- **Prints the verification the section below asks for**: per-table row counts, the alembic
  version, and the data type of every `captured_at` / `as_of` (invariant 4 — a dump/restore is
  exactly where tz-awareness degrades quietly).

Neither database script touches `data/`. That is `data-push.sh` and `data-load.sh`:

```
# here
scripts/data-push.sh                  # the delta -> homeserver:<stack>/backups/quantdesk-data.tar.gz
scripts/data-push.sh -n               # list what would be sent, send nothing
scripts/data-push.sh chains/SPX       # one subtree

# there
sudo scripts/data-load.sh             # that archive -> data/, then chown to 10001:10001
scripts/data-load.sh -n               # list what it holds, change nothing (no sudo needed)
sudo scripts/data-load.sh --clear     # ... and delete the archive afterwards
```

The push compares a `find` manifest of the local tree against one of the **real** `data/` on
the server — reading it needs no permissions — and packs just the difference. So a loaded
archive is never resent, and an archive you forget to load is simply rebuilt by the next
push. Same name every time, replacing the previous one, exactly like the database dump.

It is a file-level delta rather than rsync's block-level one, which costs this tree nothing:
a chain parquet is written once by the capture that produced it and never edited, so a file
that differs at all differs entirely. (Windows has no `rsync` to fall back on anyway — Git
for Windows ships `ssh` and `tar` and nothing else of the sort.)

Three deliberate refusals:

- **Removals are never propagated.** This tree is append-only by nature and the free Cboe
  endpoint only ever serves "now", so a local file that has gone missing is far likelier to
  be a local accident than an instruction to delete the only remaining copy.
- **`postgres/` is always excluded** — on the server that path is the live PGDATA bind mount.
  The push skips it, naming it as the subtree argument is an error, and the loader refuses an
  archive containing `postgres/`, absolute, or `../` paths outright. It arrives over the
  network; the cost of being wrong about its contents is `tar` writing into a live database.
- **`data-load.sh` will not chown `postgres/`.** It fixes ownership on everything else under
  `data/` by name rather than recursing from the top: PGDATA must stay `0700` owned by uid
  999, and a stray `chown -R` over it stops Postgres from starting with an error that reads
  like corruption.

The ownership pass is the reason the loader wants root. `tar` creates intermediate
directories owned by whoever ran it, and a directory the capture worker cannot write into
breaks the *next capture* rather than this restore — a failure that shows up hours later and
nowhere near its cause. Run without `sudo` and it says so instead of leaving that behind.

### Migrating an existing database

Production stores Postgres in a bind mount (`./data/postgres`) rather than the `pgdata` named
volume development uses, so the whole stack lives in one directory. Moving an existing
database across is a **dump and restore, not a file copy** — the on-disk format is tied to the
container's uid and PGDATA layout.

From the machine holding the current data:

```
docker compose exec -T postgres pg_dump -U gex -d gex --format=custom > gex.dump
```

On the server, after the stack is up and postgres is healthy:

```
docker compose -f compose.yaml -f compose.prod.yaml exec -T postgres \
  pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists < gex.dump
```

Then verify, rather than assuming the restore was clean:

- Row counts on both sides for `snapshots`, `gex_levels`, `gex_by_strike`, `daily_bars`.
- That `captured_at` came back **tz-aware UTC** — invariant 4, enforced by `UTCDateTime` at
  the DB boundary. Spot-check a row's `tzinfo`; a dump/restore is exactly where that silently
  degrades.
- That `snapshots.parquet_path` still resolves. The column is relative to `DATA_DIR` with
  posix separators (invariant 5), so resolve a sample through
  `storage.parquet.resolve_snapshot_path` rather than trusting the copy — especially when the
  Parquet tree came from a Windows host.

The Parquet tree itself *is* a plain copy: `data/chains/` moves across as files.

### Rotating the database password

`POSTGRES_PASSWORD` is read only when initialising an empty data directory. Changing it in
`.env` against an existing database changes nothing in Postgres — but it *does* change the
`DATABASE_URL` the backend is handed, so the app starts failing to authenticate against a
database whose password is still the old one. Change both, in this order:

```
# 1. with the stack still running on the OLD password
docker compose -f compose.yaml -f compose.prod.yaml exec postgres \
  psql -U gex -d gex -c "ALTER USER gex WITH PASSWORD 'new-password';"

# 2. update POSTGRES_PASSWORD in .env to match, then
docker compose -f compose.yaml -f compose.prod.yaml up -d
```

## Configuration

`.env` at the repo root (gitignored, never committed) is the single knob. It feeds two
different consumers:

1. **Compose substitution.** Compose reads `./.env` and substitutes `${VAR}` in the compose
   files. The services declare `environment:` and no `env_file:`, so a variable reaches a
   container only if a compose file names it. Adding a key nothing references does nothing.
2. **A backend run natively on the host** (`cd backend && uv run uvicorn ...`), where
   `app/config.py` reads a `.env` relative to the working directory — that is `backend/.env`,
   a *different* file.

`backend/.env` is host-only and is now firmly out of the production picture. Under the old
dev-only setup the source bind mount exposed it inside the container as `/app/.env`, where
pydantic-settings would read it — the trap that made "I enabled intraday in `.env`" a no-op.
The production image has no bind mount and `.dockerignore` excludes `.env*` from the build
context, so `/app/.env` does not exist there at all. Every value that file supplies
(`DATABASE_URL`, `DATA_DIR`, `PROVIDER`, `SYMBOLS`, `TZ`) is set explicitly by `compose.yaml`,
so nothing is lost by its absence.

`DATABASE_URL` and `DATA_DIR` are always set by compose inside Docker; whatever `.env` says
for them matters only for host-native runs.

### Timezones

`TZ=America/New_York` on the backend is deliberate and load-bearing: the scheduler's cron
triggers and `app/jobs/calendar.py`'s trading-day checks both read `settings.TZ`, and a host
left on UTC would fire every job at the wrong wall-clock hour while looking perfectly healthy.

Postgres runs **UTC** separately. Every timestamp stored is tz-aware UTC enforced at the DB
boundary (invariant 4), so the server's own zone should never enter into it.

## Security posture

- **No host ports are published in production.** Docker writes its own iptables rules and
  bypasses ufw, so a published port is reachable by the whole LAN whatever the firewall says.
  The old setup published Postgres on `0.0.0.0:5432` with `gex`/`gex`. Now nothing is
  published and reachability comes from Caddy on the `edge` network.
- **Postgres is on a stack-private network.** `internal` only, never `edge`, so the proxy —
  and every other stack sharing it — has no route to 5432. `backend` is the only service on
  both networks.
- **The backend runs as a non-root user** (uid 10001) in the production image.
- **No auth, by design** (`context/workflow.md` scope guardrails). The app is reached over the
  LAN or Tailscale and must not be exposed to the internet; it also holds market data under a
  single-user, non-redistribution licence.

### CORS

`app/main.py` allows exactly `http://localhost:5173`. It is **kept**, and it is not dead
config: development genuinely is cross-origin (Vite on 5173 calling the API on 8001).
Production is same-origin — the browser loads the app and calls `/api/*` on the same host via
Caddy — so the middleware never fires there. Removing it would break dev and gain nothing.

### How the frontend finds the API

`frontend/src/api/client.ts` resolves a base **origin**; every request path in that module is
already absolute and already carries its own `/api` prefix. The production image builds with
`VITE_API_BASE_URL` empty, so the bundle contains no hostname and resolves against whatever
origin served the page — which is what lets the same image work as `homeserver.local` on the
LAN and as a Tailscale name remotely, with no rebuild.

Vite inlines `VITE_*` at **build** time, so this is a build `ARG` in `frontend/Dockerfile`,
not a runtime environment variable. Setting it on the running container does nothing.

## Running locally without Docker

### Backend

```
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8001
```

Serves on http://localhost:8001, health check at `/health`. Config is read from
`backend/.env` (copy `.env.example` there, or set env vars directly) via
`app/config.py`. Keys: `DATABASE_URL`, `DATA_DIR`, `PROVIDER`, `SYMBOLS`, `TZ`.

### Frontend

```
cd frontend
npm install
npm run dev
```

Serves on http://localhost:5173.

## Tests

```
cd backend && uv run pytest
cd frontend && npm test
```

Or, via Makefile (if you have `make`): `make test`.

## Lint

```
cd backend && uv run ruff check .
cd frontend && npm run lint
```

Or: `make lint`.

## Project layout

```
backend/app/
  providers/   option chain provider interface + implementations
  models/      SQLAlchemy models
  gex/         GEX math engine (pure functions)
  api/         FastAPI routers
  jobs/        APScheduler jobs
  storage/     Parquet read/write
backend/tests/
frontend/src/
  api/         TanStack Query hooks / API client
  components/  chart and panel components
  pages/       route-level pages
frontend/nginx.conf   production static server: SPA fallback + cache policy
deploy/               Caddyfile example for the reverse proxy
```

## Notes

- `DATA_DIR` is a bind mount (`./data`) in both environments so captured Parquet snapshots
  survive container restarts and are inspectable from the host. It is the one genuinely
  unbackfillable artifact in the stack — the free Cboe endpoint serves only "now".
- Development keeps Postgres in a `pgdata` named volume rather than a bind mount: PGDATA
  bind-mounted from a Windows host fails on the `0700` ownership initdb insists on, and
  `docker compose down -v` stays a one-command reset.
