#!/bin/bash
# Pull the deploy branch and refresh the VPS venv. Used by GitHub Actions
# and can be run by hand: sudo -u scout /opt/rental-scout/jobs/deploy.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BRANCH="${DEPLOY_BRANCH:-zonaprop-tigre-search}"

cd "$ROOT"
git fetch origin
git pull --ff-only "origin" "$BRANCH"
"$ROOT/venv/bin/pip" install -e .
