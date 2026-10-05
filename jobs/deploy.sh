#!/bin/bash
# Pull the deploy branch and refresh the worker venv. Used by CI
# and can be run on the worker as the app user.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-zonaprop-tigre-search}"

cd "$ROOT"
git fetch origin
git pull --ff-only "origin" "$BRANCH"
"$ROOT/venv/bin/pip" install -e .
