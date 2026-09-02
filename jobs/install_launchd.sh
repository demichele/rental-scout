#!/bin/bash
# Instala (o reinstala) el LaunchAgent que corre rental-scout cada 30 min.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.rentalscout.run"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
UID_NUM="$(id -u)"
DOMAIN="gui/${UID_NUM}"
SUPPORT="$HOME/Library/Application Support/rental-scout"
LOG_DIR="$HOME/Library/Logs/rental-scout"
WRAPPER="$SUPPORT/run.sh"
PYTHON="$ROOT/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "faltá $PYTHON" >&2
  echo "Creá el venv: python3.12 -m venv venv && source venv/bin/activate && pip install -e '.[dev]'" >&2
  exit 1
fi
if [[ ! -f "$ROOT/.env" ]]; then
  echo "faltá $ROOT/.env (copiá .env.example y completá Telegram)" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$SUPPORT" "$LOG_DIR"

# El wrapper vive fuera de Documents: launchd no puede ejecutar scripts ahí (TCC).
cat > "$WRAPPER" << EOF
#!/bin/bash
set -euo pipefail
ROOT=$(printf '%q' "$ROOT")
LOCK="\$ROOT/data/run_once.lock"
mkdir -p "\$ROOT/data"
if ! mkdir "\$LOCK" 2>/dev/null; then
  echo "\$(date -u +"%Y-%m-%dT%H:%M:%SZ") skip: otra pasada sigue corriendo"
  exit 0
fi
trap 'rmdir "\$LOCK" 2>/dev/null || true' EXIT
cd "\$HOME"
# sin exec: si reemplazamos el shell, el trap nunca borra el lock
$(printf '%q' "$PYTHON") -m jobs.run_once
EOF
chmod +x "$WRAPPER"

cat > "$PLIST" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${HOME}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${WRAPPER}</string>
  </array>
  <key>StartInterval</key>
  <integer>1800</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>HOME</key>
    <string>${HOME}</string>
    <key>PYTHONPATH</key>
    <string>${ROOT}</string>
  </dict>
</dict>
</plist>
EOF

if launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1; then
  launchctl bootout "${DOMAIN}/${LABEL}" >/dev/null 2>&1 || true
fi
launchctl bootstrap "${DOMAIN}" "$PLIST"
launchctl enable "${DOMAIN}/${LABEL}" 2>/dev/null || true

echo "ok: ${LABEL} cada 30 min (RunAtLoad: corre ahora también)"
echo "logs: ${LOG_DIR}/"
echo "estado: launchctl print ${DOMAIN}/${LABEL} | head"
echo "parar:  ${ROOT}/jobs/uninstall_launchd.sh"
echo
echo "Si el log dice Operation not permitted: Ajustes → Privacidad y seguridad → Acceso completo al disco → agregá ${PYTHON}"
