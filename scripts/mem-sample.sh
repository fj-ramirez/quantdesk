#!/usr/bin/env bash
#
# Sample per-container memory on the homeserver and append it to a CSV, so "the services keep
# growing" can be answered with a slope instead of an impression.
#
#   scripts/mem-sample.sh                   # one sample, appended
#   scripts/mem-sample.sh -n 48 -i 1800     # 48 samples, half an hour apart (24h)
#   scripts/mem-sample.sh --report          # print per-container first/last/slope and stop
#
# Environment overrides: QD_REMOTE_HOST, QD_MEM_CSV, QD_SSH.
#
# Read-only: it reads each container's cgroup counters over ssh and nothing else. No
# container is entered, nothing on the server is written.
#
# **Why a sampler rather than a one-off look.** A Python process doing pandas/pyarrow work
# reaches a high RSS plateau and stays there -- freed memory is retained by the allocator
# rather than returned to the OS -- and a single reading of 762 MB is equally consistent with
# that plateau and with a genuine leak. The two are distinguished only by the shape over days:
# a plateau flattens, a leak does not. Hence: same metric, many times, in one file.
#
# **Why anon and file are recorded separately, and why that column matters more than the
# total.** What `docker stats` calls memory usage is the cgroup's total, and that total
# includes the page cache for every file the container touched. `gex-capture` writes a Parquet
# file per capture and `postgres` reads its own data files, so both accumulate charged-but-
# reclaimable cache all day: their "memory" climbs steadily, never falls, and looks exactly
# like a leak while nothing is leaking at all. `anon` is the part that cannot be reclaimed
# under pressure -- the heap. **A leak is growth in `anon`.** Growth confined to `file` is the
# kernel doing its job.
#
# The CSV is append-only and one row per container per sample, which is the shape every later
# question wants (per-container slope, a restart showing up as a step down, a nightly job
# showing up as a sawtooth). It lives in backups/ because that directory is already the
# repo's "generated, not source" corner and is already gitignored.

set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

REMOTE_HOST=${QD_REMOTE_HOST:-homeserver}
MEM_CSV=${QD_MEM_CSV:-$REPO_ROOT/backups/mem-samples.csv}
SSH=${QD_SSH:-ssh}

# Same Git Bash path-mangling guard the sync scripts use -- see scripts/_common.sh.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

log() { printf '%s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

samples=1
interval=1800
report_only=false

while [ $# -gt 0 ]; do
  case "$1" in
    -n) samples=${2:?-n needs a count}; shift 2 ;;
    -i) interval=${2:?-i needs seconds}; shift 2 ;;
    --report) report_only=true; shift ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

# The remote half, in one ssh round trip: for every running container, its cgroup total, the
# anon/file split and its process count, in bytes, already machine-readable.
#
# Read from the cgroup rather than parsed out of `docker stats`. Three reasons: `docker stats`
# gives only the total (no anon/file split, which is the whole point -- see the header), it
# prints human units that have to be converted back, and it costs a second per container
# because it samples CPU. The `find` is how the cgroup path is located without assuming a
# driver layout: systemd puts it under system.slice/docker-<id>.scope, cgroupfs under
# docker/<id>, and this works either way.
read_remote='
for c in $(docker ps -q); do
  n=$(docker inspect -f "{{.Name}}" "$c" | tr -d /)
  s=$(find /sys/fs/cgroup -maxdepth 4 -name memory.stat -path "*$c*" 2>/dev/null | head -1)
  [ -n "$s" ] || continue
  d=$(dirname "$s")
  printf "%s|%s|%s|%s|%s\n" "$n" \
    "$(cat "$d/memory.current" 2>/dev/null || echo 0)" \
    "$(awk "/^anon /{print \$2}" "$s")" \
    "$(awk "/^file /{print \$2}" "$s")" \
    "$(cat "$d/pids.current" 2>/dev/null || echo 0)"
done'

sample_once() {
  local ts raw name total anon file pids
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # `|| true` is deliberate: a sampler that dies because Tailscale blipped is worse than one
  # that records a gap and keeps going, and the gap is visible in the CSV anyway.
  raw=$($SSH -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE_HOST" "$read_remote" 2>/dev/null) || true

  if [ -z "$raw" ]; then
    log "$ts  no sample (host unreachable)"
    return 0
  fi

  while IFS='|' read -r name total anon file pids; do
    [ -n "$name" ] || continue
    printf '%s,%s,%s,%s,%s,%s\n' "$ts" "$name" "$total" "$anon" "$file" "$pids" >> "$MEM_CSV"
  done <<< "$raw"

  log "$ts  sampled $(printf '%s\n' "$raw" | wc -l | tr -d ' ') containers"
}

# Per-container first and last readings and the implied MB/day between them. A least squares
# fit would be more defensible statistically and less readable at a glance; the question here
# ("is this flat or is it climbing") does not need the precision, and the raw rows are in the
# file for anything that does.
#
# **The `anon MB/day` column is the answer; the total is context.** See the header for why.
report() {
  [ -s "$MEM_CSV" ] || die "no samples yet in $MEM_CSV"
  awk -F, '
    NR == 1 && $1 == "timestamp" { next }
    {
      if (!(($2) in n)) { first_t[$2] = $1; first_a[$2] = $4 }
      last_t[$2] = $1; last_a[$2] = $4; last_tot[$2] = $3; last_f[$2] = $5; n[$2]++
    }
    function epoch(s) { gsub(/[-:TZ]/, " ", s); return mktime(s) }
    END {
      printf "%-30s %9s %9s %9s %12s %8s\n", \
             "container", "anon MB", "file MB", "total MB", "anon MB/day", "samples"
      for (c in n) {
        days = (epoch(last_t[c]) - epoch(first_t[c])) / 86400
        slope = days > 0 ? ((last_a[c] - first_a[c]) / 1048576) / days : 0
        printf "%-30s %9.1f %9.1f %9.1f %12.1f %8d\n", \
               c, last_a[c]/1048576, last_f[c]/1048576, last_tot[c]/1048576, slope, n[c]
      }
    }
  ' "$MEM_CSV"
}

if $report_only; then
  report
  exit 0
fi

mkdir -p "$(dirname -- "$MEM_CSV")"
[ -s "$MEM_CSV" ] || printf 'timestamp,container,total_bytes,anon_bytes,file_bytes,pids\n' > "$MEM_CSV"

# One sampler per CSV. Two of them appending to one file is not a harmless duplicate: a long
# run started before an edit to this script keeps writing whatever column layout it was
# started with, so the file ends up holding two row shapes and every reader silently
# mis-parses one of them. That happened during the investigation this script was written for.
#
# The lock is the PID file plus a liveness check rather than `flock`, which Git Bash on the
# Windows dev host does not have. A stale file (the laptop slept, the shell was killed) is
# taken over rather than treated as a lock, because the alternative is a sampler that refuses
# to start and says nothing useful about why.
LOCK="$MEM_CSV.pid"
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK" 2>/dev/null)" 2>/dev/null; then
  die "a sampler is already running (pid $(cat "$LOCK")) and appending to $MEM_CSV"
fi
printf '%s\n' "$$" > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

i=0
while [ "$i" -lt "$samples" ]; do
  i=$((i + 1))
  sample_once
  [ "$i" -lt "$samples" ] && sleep "$interval"
done

log ""
report
