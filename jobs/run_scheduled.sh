#!/bin/bash
# Una pasada de rental-scout. Usado por launchd cada 30 min.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOCK="$ROOT/data/run_once.lock"
mkdir -p "$ROOT/data/logs"

if ! mkdir "$LOCK" 2>/dev/null; then
  echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") skip: otra pasada sigue corriendo"
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null || true' EXIT

if [[ ! -x "$ROOT/venv/bin/python" ]]; then
  echo "faltá $ROOT/venv/bin/python (python3.12 -m venv venv && pip install -e '.[dev]')" >&2
  exit 1
fi

exec "$ROOT/venv/bin/python" -m jobs.run_once
