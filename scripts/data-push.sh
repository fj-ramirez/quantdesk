#!/usr/bin/env bash
#
# Push the data tree (data/chains -- the raw option chains -- and data/research) to the
# homeserver, sending only what is missing or changed there.
#
#   scripts/data-push.sh                    # delta-push data/ -> homeserver:/srv/docker/gex/data
#   scripts/data-push.sh -n                 # list what would be sent, send nothing
#   scripts/data-push.sh --sudo             # remote writes go through `sudo -n`, then chown
#   scripts/data-push.sh chains/SPX         # one subtree
#   scripts/data-push.sh --size-only        # ignore mtime, compare size alone
#
# Environment overrides: QD_REMOTE_HOST, QD_DATA_REMOTE_DIR, QD_DATA_DIR, QD_REMOTE_OWNER,
# QD_SSH.
#
# Two transports, picked automatically:
#
#   rsync   when both ends have it. Nothing to explain; it is the right tool.
#   tar     otherwise, which is the normal case from Windows -- Git for Windows ships ssh
#           and tar but no rsync. The script compares a `find` manifest from each side and
#           streams just the differing files through `tar | ssh | tar -x`.
#
# The tar path is a *file*-level delta, not rsync's block-level one, and for this tree that
# loses nothing: a chain parquet is written once by the capture that produced it and never
# edited, so a file that differs at all differs entirely.
#
# Removals are never propagated, in either transport. This tree is append-only by nature and
# the free Cboe endpoint serves only "now", so a local file that has gone missing is far more
# likely to be a local accident than an instruction to delete the server's copy.
#
# `postgres/` is always excluded. On the server that path is the live PGDATA bind mount, and
# it is the one directory in this tree that must never be written by anything but Postgres.

source "$(dirname -- "${BASH_SOURCE[0]}")/_common.sh"

REMOTE_HOST=${QD_REMOTE_HOST:-homeserver}
REMOTE_DIR=${QD_DATA_REMOTE_DIR:-/srv/docker/gex/data}
LOCAL_DIR=${QD_DATA_DIR:-$REPO_ROOT/data}
REMOTE_OWNER=${QD_REMOTE_OWNER:-10001:10001}
SSH=${QD_SSH:-ssh}
DRY_RUN=0
USE_SUDO=0
SIZE_ONLY=0
FORCE_TAR=0
SUBPATH=""

usage() { sed -n '2,/^$/p' "${BASH_SOURCE[0]}" | sed 's/^#\{1,2\} \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case $1 in
    -n|--dry-run)  DRY_RUN=1 ;;
    --sudo)        USE_SUDO=1 ;;
    --size-only)   SIZE_ONLY=1 ;;
    --tar)         FORCE_TAR=1 ;;
    --host)        REMOTE_HOST=${2:?--host needs a value}; shift ;;
    --remote-dir)  REMOTE_DIR=${2:?--remote-dir needs a value}; shift ;;
    --owner)       REMOTE_OWNER=${2:?--owner needs a value}; shift ;;
    -h|--help)     usage; exit 0 ;;
    -*)            die "unknown argument: $1 (try --help)" ;;
    *)             [ -z "$SUBPATH" ] || die "only one subtree at a time"; SUBPATH=${1%/} ;;
  esac
  shift
done

[ -d "$LOCAL_DIR" ] || die "no data directory at $LOCAL_DIR"

SRC="$LOCAL_DIR"
DST="$REMOTE_DIR"
if [ -n "$SUBPATH" ]; then
  case $SUBPATH in
    /*|*..*) die "the subtree must be a relative path inside $LOCAL_DIR" ;;
    postgres|postgres/*) die "postgres/ is the live PGDATA on the server and is never pushed" ;;
  esac
  [ -d "$LOCAL_DIR/$SUBPATH" ] || die "no such subtree: $LOCAL_DIR/$SUBPATH"
  SRC="$LOCAL_DIR/$SUBPATH"
  DST="$REMOTE_DIR/$SUBPATH"
fi

# `sudo -n` -- never a password prompt down a pipe that has a tar stream in it.
remote_prefix=""
[ "$USE_SUDO" -eq 1 ] && remote_prefix="sudo -n "

step "Checking $REMOTE_HOST:$DST"

$SSH "$REMOTE_HOST" "${remote_prefix}mkdir -p '$DST'" \
  || die "cannot create $DST on $REMOTE_HOST$([ "$USE_SUDO" -eq 1 ] && echo ' (is sudo -n allowed?)')"

if [ "$USE_SUDO" -eq 0 ] && ! $SSH "$REMOTE_HOST" "test -w '$DST'"; then
  die "$REMOTE_HOST:$DST is not writable by your ssh user.
       The deploy owns that tree as $REMOTE_OWNER (see the README), so either add yourself to
       that group or re-run with --sudo, which writes through \`sudo -n\` and chowns after."
fi

have_remote_rsync=0
$SSH "$REMOTE_HOST" "command -v rsync >/dev/null 2>&1" && have_remote_rsync=1

if [ "$FORCE_TAR" -eq 0 ] && command -v rsync >/dev/null 2>&1 && [ "$have_remote_rsync" -eq 1 ]; then
  step "Syncing with rsync"
  set -- -a --human-readable --info=stats1,progress2 \
         --exclude 'postgres/' --exclude '*.part' --exclude '*.tmp'
  [ "$SIZE_ONLY" -eq 1 ] && set -- "$@" --size-only
  [ "$DRY_RUN" -eq 1 ] && set -- "$@" --dry-run
  if [ "$USE_SUDO" -eq 1 ]; then
    set -- "$@" --rsync-path="sudo -n rsync" --chown="$REMOTE_OWNER"
  fi
  rsync "$@" "$SRC/" "$REMOTE_HOST:$DST/"
  log ""
  log "$([ "$DRY_RUN" -eq 1 ] && echo 'Dry run -- nothing was sent.' || echo 'Done.')"
  exit 0
fi

# ---------------------------------------------------------------------------------------
# tar transport: manifest diff, then stream the difference.
# ---------------------------------------------------------------------------------------

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# path \t size \t mtime, truncated to whole seconds: %T@ is fractional and the two
# filesystems do not agree past the second, which would make every file look changed.
manifest_awk='{ n = split($0, f, "\t"); split(f[3], t, "."); printf "%s\t%s\t%s\n", f[1], f[2], t[1] }'

( cd "$SRC" && find . -type f \
    ! -path './postgres/*' ! -name '*.part' ! -name '*.tmp' \
    -printf '%P\t%s\t%T@\n' ) | awk -F'\t' "$manifest_awk" | sort > "$TMP/local"

$SSH "$REMOTE_HOST" "cd '$DST' && find . -type f ! -path './postgres/*' -printf '%P\t%s\t%T@\n'" \
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

step "Delta"
log "  local:   $total_local files"
log "  remote:  $(wc -l < "$TMP/remote" | tr -d ' ') files"
log "  to send: $n files ($(human_size "$bytes"))"

if [ "$n" -eq 0 ]; then
  log ""
  log "Already in sync."
  exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log ""
  sed 's/^/  /' "$TMP/sendlist"
  log ""
  log "Dry run -- nothing was sent."
  exit 0
fi

step "Sending"

# -z is worth it despite parquet being compressed internally: research/ is HTML and JSON,
# and on the chains the cost is a few seconds of CPU against a tree that is mostly small
# files where the per-file overhead dominates anyway.
( cd "$SRC" && tar -czf - -T "$TMP/sendlist" ) \
  | $SSH "$REMOTE_HOST" "${remote_prefix}tar -xzf - -C '$DST'"

if [ "$USE_SUDO" -eq 1 ]; then
  step "Restoring ownership to $REMOTE_OWNER"
  $SSH "$REMOTE_HOST" "sudo -n chown -R '$REMOTE_OWNER' '$DST'"
fi

step "Verifying"
remote_now=$($SSH "$REMOTE_HOST" "cd '$DST' && find . -type f ! -path './postgres/*' | wc -l" | tr -d ' ')
log "  remote now holds $remote_now files (local: $total_local)"
[ "$remote_now" -ge "$total_local" ] \
  || log "  note: fewer files there than here -- rerun, or check the excluded patterns"
