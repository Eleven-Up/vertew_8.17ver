#!/usr/bin/env bash
#
# Vertew Raspberry Pi one-shot installer.
#
# Run once, after cloning this repo onto the Pi:
#
#   cd vertew && ./scripts/install.sh
#
# Sets up the backend Python venv, installs dependencies, builds (or checks
# for) the frontend bundle, creates backend/.env from the example if missing,
# then installs and enables the systemd services plus the kiosk autostart
# entry -- so from then on Vertew starts automatically on every boot with NO
# manual step required. Re-running it is safe (each step is idempotent).
#
# Linux has no ".exe" equivalent for "one file that sets everything up" --
# this script IS that file.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

log() { printf '\n==> %s\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. Backend: venv + dependencies
# ---------------------------------------------------------------------------
log "Setting up backend Python venv"
cd "${REPO_ROOT}/backend"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
    log "No backend/.env found -- copying .env.example (edit it to add your LLM API key!)"
    cp .env.example .env
else
    log "backend/.env already exists, leaving it as-is"
fi
cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# 2. Frontend bundle (frontend/js/main.js + mock.js are gitignored build output)
# ---------------------------------------------------------------------------
log "Checking frontend bundle"
if [ -f frontend/js/main.js ] && [ -f frontend/js/mock.js ]; then
    echo "    frontend/js/main.js and mock.js already present -- skipping build"
elif command -v npm >/dev/null 2>&1; then
    (cd frontend && npm install && npm run build)
else
    echo "    npm not found on this Pi and the bundle is missing." >&2
    echo "    Build it on your dev machine and scp frontend/js/main.js + mock.js" >&2
    echo "    to this same path on the Pi, then re-run this script." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. systemd services (backend always; sensor bridge only if the hardware for
# it looks present, since most dev/demo Pis won't have it wired up yet)
# ---------------------------------------------------------------------------
log "Installing systemd services (needs sudo)"
CURRENT_USER="$(whoami)"

install_unit() {
    local example="$1" unit_name="$2"
    sed -e "s#/opt/vertew#${REPO_ROOT}#g" -e "s#^User=pi#User=${CURRENT_USER}#" \
        "scripts/${example}" | sudo tee "/etc/systemd/system/${unit_name}" >/dev/null
}

install_unit vertew-backend.service.example vertew-backend.service

if [ -e /dev/ttyUSB0 ] || [ -e /dev/gpiochip0 ]; then
    log "Distance-sensor device node detected -- installing vertew-sensor.service too"
    install_unit vertew-sensor.service.example vertew-sensor.service
else
    echo "    No /dev/ttyUSB0 or GPIO chip detected -- skipping vertew-sensor.service."
    echo "    (Not needed for a demo: the debug panel's Customer Detected button"
    echo "    simulates this. Re-run this script once the sensor is wired up.)"
fi

sudo systemctl daemon-reload
sudo systemctl enable --now vertew-backend.service
if [ -f /etc/systemd/system/vertew-sensor.service ]; then
    sudo systemctl enable --now vertew-sensor.service
fi

# ---------------------------------------------------------------------------
# 4. Kiosk browser autostart (user-session XDG autostart, no sudo needed)
# ---------------------------------------------------------------------------
log "Installing kiosk autostart entry"
mkdir -p "${HOME}/.config/autostart"
sed "s#/opt/vertew#${REPO_ROOT}#g" scripts/vertew-kiosk.desktop.example \
    > "${HOME}/.config/autostart/vertew-kiosk.desktop"

# ---------------------------------------------------------------------------
log "Done"
cat <<EOF

Vertew will start automatically on every boot from now on:
  - vertew-backend.service starts the FastAPI server (systemctl status vertew-backend)
  - the kiosk browser launches once the desktop session starts

To start it right now without rebooting:
  sudo systemctl status vertew-backend   # confirm it's already running
  ${REPO_ROOT}/scripts/kiosk.sh &

Sanity check:
  curl -s http://localhost:8000/api/stt/config   # should print {"provider":"local"}
EOF
