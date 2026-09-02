#!/bin/bash
# Saca el LaunchAgent de rental-scout.
set -euo pipefail

LABEL="com.rentalscout.run"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "${DOMAIN}/${LABEL}"
fi
rm -f "$PLIST"
echo "ok: ${LABEL} desinstalado"
