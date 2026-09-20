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

Target: Arch Linux, Docker + compose v2, stack at `/srv/docker/gex/`, managed by a
`compose@gex` systemd template unit whose `WorkingDirectory` is the stack directory (so
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
sudo mkdir -p /srv/docker/gex/data/postgres
sudo chown -R 10001:10001 /srv/docker/gex/data          # backend's app user
sudo chown -R 999:999     /srv/docker/gex/data/postgres # postgres user inside postgres:16
```

Everything the stack persists lives under `data/`, so the whole thing tars as one unit:

```
sudo systemctl stop compose@gex
sudo tar czf gex-backup-$(date +%F).tar.gz -C /srv/docker gex
sudo systemctl start compose@gex
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
cd /srv/docker/gex
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
docker compose -f compose.yaml -f compose.prod.yaml ps      # all three should read healthy
curl -s localhost/health                                    # via Caddy: {"status":"ok",...,"db":"ok"}
```

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
