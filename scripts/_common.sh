# Shared helpers for the database sync scripts. Sourced, never executed.
#
# The two callers -- db-dump-push.sh and db-restore.sh -- both talk to Postgres the same way:
# through `docker compose exec` on the stack that lives in this repo directory. Nothing here
# ever needs credentials from the host, because the container already has POSTGRES_USER,
# POSTGRES_PASSWORD and POSTGRES_DB in its own environment. That matters more than it looks:
# dev defaults them to gex/gex/gex and production requires them from .env, so reading them
# host-side would mean two places that can disagree about which database is being dumped.

set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

# One name, always. The push overwrites the previous dump on the server rather than
# accumulating dated files, which is what keeps "the dump" unambiguous on both ends.
DUMP_NAME=${QD_DUMP_NAME:-quantdesk.dump}
DUMP_DIR=${QD_DUMP_DIR:-$REPO_ROOT/backups}

PG_SERVICE=${QD_PG_SERVICE:-postgres}

# The containers that hold connections to Postgres. The restore stops them for the duration:
# `pg_restore --clean` drops every table, and a capture writing through that is either
# blocked on a lock (restore hangs) or writing into a table about to vanish.
APP_SERVICES=${QD_APP_SERVICES:-"backend gex-capture research-search terminal-ingest"}

# Git Bash rewrites anything that looks like a POSIX path before handing it to a native
# Windows binary, which turns `homeserver:/srv/docker/gex` into `homeserver;C:/Program
# Files/Git/srv/...` on its way into ssh.exe. Harmless to export on Linux.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

COMPOSE_ARGS=()

log()  { printf '%s\n' "$*" >&2; }
step() { printf '\n== %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

# Production is the explicit opt-in here exactly as it is for `docker compose up`: the prod
# overlay must be named, or compose.override.yaml (dev ports, dev volume) is merged in.
use_prod() { COMPOSE_ARGS=(-f compose.yaml -f compose.prod.yaml); }

dc() {
  ( cd "$REPO_ROOT" && docker compose ${COMPOSE_ARGS[@]+"${COMPOSE_ARGS[@]}"} "$@" )
}

require_pg() {
  local cid
  # stderr deliberately not swallowed: in --prod mode compose aborts here naming the
  # variable that .env is missing, and that message is far more useful than ours.
  cid=$(dc ps -q "$PG_SERVICE" || true)
  [ -n "$cid" ] || die "no '$PG_SERVICE' container for the stack in $REPO_ROOT. Start it first
       dev:  docker compose up -d $PG_SERVICE
       prod: docker compose -f compose.yaml -f compose.prod.yaml up -d $PG_SERVICE (and pass --prod)"
  [ "$(docker inspect -f '{{.State.Running}}' "$cid")" = "true" ] \
    || die "the '$PG_SERVICE' container exists but is not running"
}

# SQL on stdin, as the database's own owner. ON_ERROR_STOP so a failed statement fails the
# script rather than scrolling past.
psql_stdin() {
  dc exec -T "$PG_SERVICE" sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f -'
}

target_line() {
  dc exec -T "$PG_SERVICE" sh -c 'printf "%s@%s/%s" "$POSTGRES_USER" "$(hostname)" "$POSTGRES_DB"'
}

# Exact row counts for every table in the three module schemas -- discovered, not hardcoded,
# so a table added by a later migration shows up here without this script being touched.
# Exact rather than reltuples: reltuples is 0 on a freshly restored table until ANALYZE runs,
# which would make a perfectly good restore look empty.
table_counts() {
  psql_stdin <<'SQL'
\pset border 2
SELECT
  table_schema AS schema,
  table_name   AS "table",
  (xpath(
     '/row/c/text()',
     query_to_xml(format('SELECT count(*) AS c FROM %I.%I', table_schema, table_name),
                  false, true, '')
   ))[1]::text::bigint AS rows
FROM information_schema.tables
WHERE table_schema IN ('gex', 'research', 'terminal')
  AND table_type = 'BASE TABLE'
ORDER BY 1, 2;
SQL
}

human_size() {
  local bytes=$1
  awk -v b="$bytes" 'BEGIN {
    split("B KB MB GB TB", u, " ");
    i = 1; while (b >= 1024 && i < 5) { b /= 1024; i++ }
    printf (i == 1 ? "%d %s\n" : "%.1f %s\n"), b, u[i]
  }'
}
