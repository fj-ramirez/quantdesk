#!/usr/bin/env bash
#
# Push the data tree (data/chains -- the raw option chains -- and data/research) to the
# homeserver as a staged archive, carrying only what is missing or different there.
#
#   scripts/data-push.sh                    # delta -> homeserver:<stack>/backups/quantdesk-data.tar.gz
#   scripts/data-push.sh -n                 # list what would be sent, send nothing
#   scripts/data-push.sh chains/SPX         # one subtree
#   scripts/data-push.sh --size-only        # ignore mtime, compare size alone
#
# Then, on the server: `sudo scripts/data-load.sh`, which unpacks it into data/ and fixes
# ownership. Two steps rather than one because `data/` belongs to the deploy user (10001) and
# your ssh account does not: writing into it needs either a permission change you would have
# to remember on every new directory, or a root prompt down a pipe that already has a tar
# stream on it. `backups/` is yours to write, so the transfer needs no privilege at all and
# the one step that does is a local command on the far side.
#
# Environment overrides: QD_REMOTE_HOST, QD_DATA_REMOTE_DIR (the tree compared against),
# QD_STAGING_DIR (where the archive lands), QD_DATA_ARCHIVE, QD_DATA_DIR, QD_SSH.
#
# The delta is computed against the **real** data tree over there, not against the staging
# directory -- reading it needs no permissions -- so a loaded archive is never resent, and an
# archive you forget to load is simply rebuilt by the next push. Same archive name every
# time, replacing the previous one, exactly like the database dump.
#
# It is a file-level delta: a chain parquet is written once by the capture that produced it
# and never edited, so a file that differs at all differs entirely.
#
# Removals are never propagated. This tree is append-only by nature and the free Cboe
# endpoint serves only "now", so a local file that has gone missing is far more likely to be
# a local accident than an instruction to delete the server's copy.
#
# `postgres/` is always excluded. On the server that path is the live PGDATA bind mount, and
# it is the one directory in this tree that must never be written by anything but Postgres.

source "$(dirname -- "${BASH_SOURCE[0]}")/_common.sh"

REMOTE_HOST=${QD_REMOTE_HOST:-homeserver}
DATA_REMOTE_DIR=${QD_DATA_REMOTE_DIR:-/srv/docker/quantdesk/data}
STAGING_DIR=${QD_STAGING_DIR:-/srv/docker/quantdesk/backups}
ARCHIVE=${QD_DATA_ARCHIVE:-quantdesk-data.tar.gz}
LOCAL_DIR=${QD_DATA_DIR:-$REPO_ROOT/data}
SSH=${QD_SSH:-ssh}
DRY_RUN=0
SIZE_ONLY=0
SUBPATH=""

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case $1 in
    -n|--dry-run)   DRY_RUN=1 ;;
    --size-only)    SIZE_ONLY=1 ;;
    --host)         REMOTE_HOST=${2:?--host needs a value}; shift ;;
    --remote-dir)   DATA_REMOTE_DIR=${2:?--remote-dir needs a value}; shift ;;
    --staging-dir)  STAGING_DIR=${2:?--staging-dir needs a value}; shift ;;
    --name)         ARCHIVE=${2:?--name needs a value}; shift ;;
    -h|--help)      usage; exit 0 ;;
    -*)             die "unknown argument: $1 (try --help)" ;;
    *)              [ -z "$SUBPATH" ] || die "only one subtree at a time"; SUBPATH=${1%/} ;;
  esac
  shift
done

[ -d "$LOCAL_DIR" ] || die "no data directory at $LOCAL_DIR"

# A subtree narrows *which* files are considered, never where they sit in the archive: paths
# stay relative to the data root so the loader always extracts into the same place.
FILTER=()
if [ -n "$SUBPATH" ]; then
  case $SUBPATH in
    /*|*..*) die "the subtree must be a relative path inside $LOCAL_DIR" ;;
    postgres|postgres/*) die "postgres/ is the live PGDATA on the server and is never pushed" ;;
  esac
  [ -d "$LOCAL_DIR/$SUBPATH" ] || die "no such subtree: $LOCAL_DIR/$SUBPATH"
  FILTER=(-path "./$SUBPATH/*")
fi

step "Staging area: $REMOTE_HOST:$STAGING_DIR"

$SSH "$REMOTE_HOST" "mkdir -p '$STAGING_DIR' && test -w '$STAGING_DIR'" \
  || die "cannot write $STAGING_DIR on $REMOTE_HOST.
       That directory is yours by design -- the data tree is not. Create it there with
       write access for your ssh user, or point somewhere else with --staging-dir."

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# path \t size \t mtime, truncated to whole seconds: %T@ is fractional and the two
# filesystems do not agree past the second, which would make every file look changed.
manifest_awk='{ n = split($0, f, "\t"); split(f[3], t, "."); printf "%s\t%s\t%s\n", f[1], f[2], t[1] }'
find_args=(. -type f ! -path './postgres/*' ! -name '*.part' ! -name '*.tmp')

( cd "$LOCAL_DIR" && find "${find_args[@]}" ${FILTER[@]+"${FILTER[@]}"} -printf '%P\t%s\t%T@\n' ) \
  | awk -F'\t' "$manifest_awk" | sort > "$TMP/local"

# Read-only, and that is the whole point of the two-step: `find` needs no write permission on
# the tree it is describing.
remote_find="find . -type f ! -path './postgres/*'"
[ -n "$SUBPATH" ] && remote_find="$remote_find -path './$SUBPATH/*'"
$SSH "$REMOTE_HOST" "cd '$DATA_REMOTE_DIR' && $remote_find -printf '%P\t%s\t%T@\n'" \
  2>/dev/null | awk -F'\t' "$manifest_awk" | sort > "$TMP/remote" || true

# In local and not remote, or there with a different size (and mtime, unless --size-only).
#
# Keyed on FILENAME rather than the usual `NR == FNR`: that idiom inverts silently when the
# first file is empty, which is precisely the case that matters here -- against a remote with
# nothing on it yet, every local file would look already-present and nothing would be sent.
awk -F'\t' -v size_only="$SIZE_ONLY" -v remote_manifest="$TMP/remote" '
  FILENAME == remote_manifest { r[$1] = (size_only ? $2 : $2 "\t" $3); next }
  {
    mine = (size_only ? $2 : $2 "\t" $3)
    if (!($1 in r) || r[$1] != mine) { print $1 "\t" $2 }
  }
' "$TMP/remote" "$TMP/local" > "$TMP/changed"

cut -f1 "$TMP/changed" > "$TMP/sendlist"

total_local=$(wc -l < "$TMP/local" | tr -d ' ')
n=$(wc -l < "$TMP/sendlist" | tr -d ' ')
bytes=$(awk -F'\t' '{ s += $2 } END { printf "%d", s + 0 }' "$TMP/changed")

step "Delta against $REMOTE_HOST:$DATA_REMOTE_DIR"
log "  here:    $total_local files"
log "  there:   $(wc -l < "$TMP/remote" | tr -d ' ') files"
log "  to send: $n files ($(human_size "$bytes"))"

if [ "$n" -eq 0 ]; then
  log ""
  log "Already in sync. Nothing staged."
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log ""
  sed 's/^/  /' "$TMP/sendlist"
  log ""
  log "Dry run -- nothing was sent."
  exit 0
fi

step "Staging $ARCHIVE"

# Written to .part on the far side and renamed only once tar and ssh have both exited 0, so
# an interrupted push never leaves a truncated archive for the loader to unpack.
( cd "$LOCAL_DIR" && tar -czf - -T "$TMP/sendlist" ) \
  | $SSH "$REMOTE_HOST" "cat > '$STAGING_DIR/$ARCHIVE.part'"
$SSH "$REMOTE_HOST" "mv -f '$STAGING_DIR/$ARCHIVE.part' '$STAGING_DIR/$ARCHIVE'"
$SSH "$REMOTE_HOST" "ls -lh '$STAGING_DIR/$ARCHIVE'"

log ""
log "Staged, not installed. On $REMOTE_HOST:"
log "    cd ${DATA_REMOTE_DIR%/data} && sudo scripts/data-load.sh"
