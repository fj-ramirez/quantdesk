#!/usr/bin/env bash
#
# Dump this host's quantdesk Postgres and push it to the homeserver, replacing the previous
# dump. One file, one name -- `backups/quantdesk.dump` here, `<stack>/backups/quantdesk.dump`
# there -- so `db-restore.sh` on the other end never has to be told which file to use.
#
#   scripts/db-dump-push.sh                 # dev stack here -> homeserver
#   scripts/db-dump-push.sh --prod          # dump the production stack instead
#   scripts/db-dump-push.sh --local-only    # write the dump, send nothing
#   scripts/db-dump-push.sh --host nas --remote-dir /srv/docker/quantdesk/backups
#
# Environment overrides: QD_REMOTE_HOST, QD_REMOTE_DIR, QD_DUMP_NAME, QD_DUMP_DIR,
# QD_PG_SERVICE, QD_SSH (e.g. QD_SSH="ssh -p 2222").
#
# This moves the *database* only. The Parquet tree under data/chains/ is the other half of a
# snapshot (invariant 5: snapshots.parquet_path is relative to DATA_DIR) and is a plain file
# copy: send it with scripts/data-push.sh, or the restored index points at files that the
# target does not have.

source "$(dirname -- "${BASH_SOURCE[0]}")/_common.sh"

REMOTE_HOST=${QD_REMOTE_HOST:-homeserver}
REMOTE_DIR=${QD_REMOTE_DIR:-/srv/docker/quantdesk/backups}
SEND=1

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case $1 in
    --prod)        use_prod ;;
    --local-only)  SEND=0 ;;
    --host)        REMOTE_HOST=${2:?--host needs a value}; shift ;;
    --remote-dir)  REMOTE_DIR=${2:?--remote-dir needs a value}; shift ;;
    --name)        DUMP_NAME=${2:?--name needs a value}; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

SSH=${QD_SSH:-ssh}
SCP=${QD_SCP:-scp}
DUMP_PATH="$DUMP_DIR/$DUMP_NAME"

require_pg

step "Dumping $(target_line)"

mkdir -p "$DUMP_DIR"

# Written to .part first and renamed only once pg_dump has exited 0, so a dump interrupted
# half way through never replaces the last good one -- locally or on the server.
#
# --no-owner/--no-privileges: the restore re-creates objects as whatever role owns the target
# database, which is not necessarily the one that owned them here, and the GRANTs to
# quantdesk_ro are re-applied by db-restore.sh rather than carried in the dump (they would
# fail outright on a target where that role does not exist yet).
dc exec -T "$PG_SERVICE" sh -c \
  'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     --format=custom --compress=9 --no-owner --no-privileges' \
  > "$DUMP_PATH.part"

[ -s "$DUMP_PATH.part" ] || { rm -f "$DUMP_PATH.part"; die "pg_dump produced an empty file"; }
mv -f "$DUMP_PATH.part" "$DUMP_PATH"

size=$(wc -c < "$DUMP_PATH" | tr -d ' ')
log "wrote $DUMP_PATH ($(human_size "$size"))"

step "What is in it"
table_counts

if [ "$SEND" -eq 0 ]; then
  log ""
  log "--local-only: nothing sent. Restore it with: scripts/db-restore.sh"
  exit 0
fi

step "Sending to $REMOTE_HOST:$REMOTE_DIR/$DUMP_NAME"

$SSH "$REMOTE_HOST" "mkdir -p '$REMOTE_DIR'"

# Relative path on the local side on purpose: scp reads `C:\...` as a host named C, and Git
# Bash's own path rewriting is already off (see _common.sh).
( cd "$DUMP_DIR" && $SCP "$DUMP_NAME" "$REMOTE_HOST:$REMOTE_DIR/$DUMP_NAME.part" )
$SSH "$REMOTE_HOST" "mv -f '$REMOTE_DIR/$DUMP_NAME.part' '$REMOTE_DIR/$DUMP_NAME'"

$SSH "$REMOTE_HOST" "ls -lh '$REMOTE_DIR/$DUMP_NAME'"

log ""
log "Done. On $REMOTE_HOST:"
log "    cd /srv/docker/quantdesk && ./scripts/db-restore.sh --prod"
log ""
log "The Parquet tree is not included -- send it with scripts/data-push.sh."
