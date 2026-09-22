#!/usr/bin/env bash
#
# Build and start the production stack with each image stamped with the commit it was built
# from. Run it **on the homeserver**, from the stack directory.
#
#   scripts/deploy.sh                    # git pull, then build and start everything
#   scripts/deploy.sh --no-pull          # build what is already checked out
#   scripts/deploy.sh backend frontend   # only these services
#
# Why this exists rather than the bare compose command in the README: a container has no git
# repository to ask what it is, so the commit has to be handed in at build time. Compose
# cannot run `git` -- it only interpolates the environment -- so something has to compute
# BUILD_SHA and BUILD_TIME and export them, and that something is this file. Built without
# them, every service honestly reports "unknown" at `GET /health` rather than guessing.
#
# The bare command still works and is still correct; it just produces unstamped images.

set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_ROOT"

export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

pull=1
services=()
for arg in "$@"; do
  case "$arg" in
    --no-pull) pull=0 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) services+=("$arg") ;;
  esac
done

if [ "$pull" -eq 1 ]; then
  # --ff-only: a deploy must never be the thing that creates a merge commit on the server.
  git pull --ff-only
fi

# Both stamps describe the *source*, not the moment of building: two rebuilds of the same
# commit should be indistinguishable, because they are. `%cI` is the commit's own committer
# date in strict ISO-8601, so "backend: 2026-09-21T20:14:03-04:00 (cf59b11)" answers "which
# commit" and "how old is it" in one line without a clock of its own.
BUILD_SHA=$(git rev-parse --short HEAD)
BUILD_TIME=$(git show -s --format=%cI HEAD)

# A dirty tree means the image will not match the commit it claims. Say so rather than
# refusing: deploying a one-line fix from an unclean checkout is a legitimate thing to do at
# 16:05, and a script that blocks it is a script that gets bypassed.
if [ -n "$(git status --porcelain)" ]; then
  BUILD_SHA="$BUILD_SHA-dirty"
  echo "warning: working tree is not clean; stamping images as $BUILD_SHA" >&2
fi

export BUILD_SHA BUILD_TIME
echo "deploying $BUILD_SHA ($BUILD_TIME)"

docker compose -f compose.yaml -f compose.prod.yaml up -d --build "${services[@]}"

echo
docker compose -f compose.yaml -f compose.prod.yaml ps
echo
# The point of the whole exercise: what each container now reports about itself. The workers
# write their stamp at boot, so a service that has not finished starting is simply absent
# here -- run it again in a few seconds rather than reading that as a failure.
curl -fsS localhost/health | python3 -c 'import json,sys
for s in json.load(sys.stdin).get("services", []):
    print("  " + s["label"])' 2>/dev/null || echo "  (health not reachable yet)"
