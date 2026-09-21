#!/usr/bin/env bash
#
# Unpack a staged data archive into the data tree. Runs on the server, next to the stack:
#
#   sudo scripts/data-load.sh              # backups/quantdesk-data.tar.gz -> data/
#   scripts/data-load.sh -n                # list what it holds, change nothing (no sudo needed)
#   sudo scripts/data-load.sh --clear      # ... and delete the archive afterwards
#   sudo scripts/data-load.sh some.tar.gz  # a different archive
#
# The other half of scripts/data-push.sh, which stages the archive in backups/ because that
# directory is writable by an ordinary ssh user and data/ is not. This is the step that needs
# root, and it is a local command with a local password prompt rather than a privileged shell
# hanging off the end of an ssh pipe.
#
# Everything it extracts is chowned to the deploy user afterwards -- including directories
# tar created along the way, which would otherwise be left owned by root and unwritable by
# the capture worker, breaking the next capture rather than this restore.
#
# Environment overrides: QD_DATA_DIR, QD_STAGING_DIR, QD_DATA_ARCHIVE, QD_REMOTE_OWNER.

source "$(dirname -- "${BASH_SOURCE[0]}")/_common.sh"

DATA_DIR=${QD_DATA_DIR:-$REPO_ROOT/data}
STAGING_DIR=${QD_STAGING_DIR:-$REPO_ROOT/backups}
ARCHIVE=${QD_DATA_ARCHIVE:-quantdesk-data.tar.gz}
OWNER=${QD_REMOTE_OWNER:-10001:10001}
DRY_RUN=0
CLEAR=0
ARCHIVE_PATH=""

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case $1 in
    -n|--dry-run)  DRY_RUN=1 ;;
    --clear)       CLEAR=1 ;;
    --owner)       OWNER=${2:?--owner needs a value}; shift ;;
    --data-dir)    DATA_DIR=${2:?--data-dir needs a value}; shift ;;
    -h|--help)     usage; exit 0 ;;
    -*)            die "unknown argument: $1 (try --help)" ;;
    *)             [ -z "$ARCHIVE_PATH" ] || die "only one archive at a time"; ARCHIVE_PATH=$1 ;;
  esac
  shift
done

ARCHIVE_PATH=${ARCHIVE_PATH:-$STAGING_DIR/$ARCHIVE}

[ -f "$ARCHIVE_PATH" ] || die "no archive at $ARCHIVE_PATH (push one with scripts/data-push.sh)"
[ -s "$ARCHIVE_PATH" ] || die "$ARCHIVE_PATH is empty"
[ -d "$DATA_DIR" ]     || die "no data directory at $DATA_DIR"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

tar -tzf "$ARCHIVE_PATH" > "$TMP/entries" || die "$ARCHIVE_PATH is not a readable tar.gz"

# Refuse an archive that could write outside data/, or into PGDATA. This one is built by
# data-push.sh from a filtered manifest and cannot contain either -- which is exactly why the
# check is cheap enough to keep: the archive arrives over the network, and the cost of being
# wrong about that is a live database directory written by tar.
if grep -qE '^/|(^|/)\.\./|^postgres/' "$TMP/entries"; then
  grep -nE '^/|(^|/)\.\./|^postgres/' "$TMP/entries" | head -5 >&2
  die "refusing: the archive contains absolute, traversing, or postgres/ paths"
fi

count=$(wc -l < "$TMP/entries" | tr -d ' ')
size=$(wc -c < "$ARCHIVE_PATH" | tr -d ' ')

step "Archive"
log "  $ARCHIVE_PATH ($(human_size "$size"), $count entries)"
log "  into $DATA_DIR, owned by $OWNER afterwards"

if [ "$DRY_RUN" -eq 1 ]; then
  log ""
  sed 's/^/  /' "$TMP/entries"
  log ""
  log "Dry run -- nothing was written."
  exit 0
fi

# Root, or an account that genuinely owns the tree. Checked before extracting rather than
# half way through it.
[ -w "$DATA_DIR" ] || die "$DATA_DIR is not writable by $(id -un) -- re-run with sudo"

step "Extracting"
tar -xzf "$ARCHIVE_PATH" -C "$DATA_DIR" --no-same-owner

if [ "$(id -u)" -eq 0 ]; then
  step "Ownership -> $OWNER"
  # Everything but postgres/, and by name rather than a recursive chown of DATA_DIR: PGDATA
  # must stay 0700 owned by the postgres uid, and a stray -R over it stops the database from
  # starting on the next boot with a permissions error that looks like corruption.
  find "$DATA_DIR" -mindepth 1 -maxdepth 1 ! -name postgres -exec chown -R "$OWNER" {} +
else
  log ""
  log "note: not root, so ownership was left as $(id -un). The capture worker runs as"
  log "      ${OWNER%%:*} and may not be able to write here -- re-run with sudo if so."
fi

step "Result"
now=$(find "$DATA_DIR" -type f ! -path "$DATA_DIR/postgres/*" | wc -l | tr -d ' ')
log "  $now files under $DATA_DIR (excluding postgres/)"
log "  $(du -sh "$DATA_DIR" 2>/dev/null | cut -f1) on disk"

if [ "$CLEAR" -eq 1 ]; then
  rm -f "$ARCHIVE_PATH"
  log "  removed $ARCHIVE_PATH"
else
  log ""
  log "The archive is kept at $ARCHIVE_PATH; the next push replaces it."
fi
