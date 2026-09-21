#!/usr/bin/env bash
#
# Restore a dump produced by db-dump-push.sh into the stack running on THIS host. Destructive:
# --clean drops every object the dump contains before recreating it.
#
#   scripts/db-restore.sh                   # backups/quantdesk.dump -> the dev stack here
#   scripts/db-restore.sh --prod            # ... into the production stack (homeserver)
#   scripts/db-restore.sh --prod -y         # no confirmation prompt (for a scripted run)
#   scripts/db-restore.sh some/other.dump   # a different file
#
# Environment overrides: QD_DUMP_NAME, QD_DUMP_DIR, QD_PG_SERVICE, QD_APP_SERVICES.
#
# The backend and the three workers are stopped for the duration and started again afterwards,
# including if the restore fails: pg_restore --clean drops tables out from under whatever is
# writing to them, and an open connection holding a lock makes the drop block instead.
#
# Restores the database only. data/chains/ (the Parquet tree the snapshot index points into,
# invariant 5) is pushed separately, by scripts/data-push.sh and scripts/data-load.sh.

source "$(dirname -- "${BASH_SOURCE[0]}")/_common.sh"

ASSUME_YES=0
STOP_SERVICES=1
DUMP_PATH=""

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case $1 in
    --prod)     use_prod ;;
    -y|--yes)   ASSUME_YES=1 ;;
    --no-stop)  STOP_SERVICES=0 ;;
    -h|--help)  usage; exit 0 ;;
    -*)         die "unknown argument: $1 (try --help)" ;;
    *)          [ -z "$DUMP_PATH" ] || die "only one dump file can be restored"; DUMP_PATH=$1 ;;
  esac
  shift
done

DUMP_PATH=${DUMP_PATH:-$DUMP_DIR/$DUMP_NAME}

[ -f "$DUMP_PATH" ] || die "no dump at $DUMP_PATH (push one with scripts/db-dump-push.sh)"
[ -s "$DUMP_PATH" ] || die "$DUMP_PATH is empty"

require_pg

size=$(wc -c < "$DUMP_PATH" | tr -d ' ')
target=$(target_line)

step "About to restore"
log "  dump:   $DUMP_PATH ($(human_size "$size"), modified $(date -r "$DUMP_PATH" '+%Y-%m-%d %H:%M'))"
log "  target: $target  [stack: $REPO_ROOT]"
log ""
log "  Everything below is dropped and rebuilt from the dump:"
table_counts

if [ "$ASSUME_YES" -eq 0 ]; then
  { : < /dev/tty; } 2>/dev/null || die "no terminal to confirm at -- pass -y if you mean it"
  printf '\nType the database name to confirm: ' >&2
  read -r reply < /dev/tty
  [ "$reply" = "${target##*/}" ] || die "not confirmed -- nothing changed"
fi

STOPPED=""
restart_services() {
  [ -n "$STOPPED" ] || return 0
  step "Starting back up:$STOPPED"
  # shellcheck disable=SC2086
  dc start $STOPPED
  STOPPED=""
}
trap restart_services EXIT

if [ "$STOP_SERVICES" -eq 1 ]; then
  for svc in $APP_SERVICES; do
    cid=$(dc ps -q "$svc" 2>/dev/null || true)
    [ -n "$cid" ] || continue
    [ "$(docker inspect -f '{{.State.Running}}' "$cid")" = "true" ] || continue
    STOPPED="$STOPPED $svc"
  done
  if [ -n "$STOPPED" ]; then
    step "Stopping while the restore runs:$STOPPED"
    # shellcheck disable=SC2086
    dc stop $STOPPED
  fi
fi

step "Restoring"

# --single-transaction, so a restore that fails half way leaves the database exactly as it
# was rather than partially wiped. It implies --exit-on-error, which is the behaviour worth
# having: pg_restore's default is to log errors, carry on and exit 0.
dc exec -T "$PG_SERVICE" sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     --clean --if-exists --no-owner --no-privileges --single-transaction' \
  < "$DUMP_PATH"

step "Re-applying quantdesk_ro's grants"

# Necessary, not belt-and-braces. `--clean` drops the module schemas, and a schema's
# ALTER DEFAULT PRIVILEGES entry (pg_default_acl) dies with it -- so the tables the restore
# then creates inherit no grant at all and the MCP connector (T82) reads an empty database
# through a role that can technically still log in. This is the same SQL the T76 migration
# runs, which alembic will not re-run because the version row came back with the dump.
psql_stdin <<'SQL'
DO $$
DECLARE
  s  text;
  ro text := 'quantdesk_ro';
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ro) THEN
    RAISE NOTICE 'role % does not exist here; skipping grants', ro;
    RETURN;
  END IF;
  FOREACH s IN ARRAY ARRAY['gex', 'research', 'terminal'] LOOP
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = s) THEN
      EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', s, ro);
      EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I', s, ro);
      EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO %I',
        current_user, s, ro);
    END IF;
  END LOOP;
END $$;
SQL

step "Analyzing"
psql_stdin <<'SQL'
ANALYZE;
SQL

step "What came back"
table_counts

# Two things a dump/restore is known to degrade quietly, both worth a look rather than a
# shrug: captured_at must still be tz-aware UTC (invariant 4, enforced by UTCDateTime at the
# DB boundary), and alembic must agree about which migration this database is on.
psql_stdin <<'SQL'
\pset border 2
SELECT c.table_schema || '.' || c.table_name || '.' || c.column_name AS column,
       c.data_type
FROM information_schema.columns c
WHERE c.table_schema IN ('gex', 'research', 'terminal')
  AND c.column_name IN ('captured_at', 'as_of')
ORDER BY 1;

SELECT version_num AS alembic_version FROM public.alembic_version;
SQL

restart_services
trap - EXIT

log ""
log "Restored. Worth a glance before trusting it:"
log "  * every captured_at / as_of above reads 'timestamp with time zone' (invariant 4)"
log "  * data/chains/ holds the Parquet this index points at (invariant 5) -- scripts/data-push.sh"
