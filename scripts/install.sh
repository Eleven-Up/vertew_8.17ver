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
# 2. Frontend bundles. All three are esbuild/vite build output and gitignored,
# so none of them ship in the repo -- build on the Pi (needs Node) or scp a
# prebuilt copy over from your dev machine.
# ---------------------------------------------------------------------------
build_or_instruct() {
    local dir="$1" desc="$2"
    shift 2
    local check_files=("$@")
    local missing=0
    for f in "${check_files[@]}"; do
        [ -f "${dir}/${f}" ] || missing=1
    done
    if [ "${missing}" -eq 0 ]; then
        echo "    ${desc}: build output already present -- skipping"
        return 0
    fi
    if command -v npm >/dev/null 2>&1; then
        log "Building ${desc}"
        (cd "${dir}" && npm install && npm run build)
    else
        echo "    npm not found and ${desc}'s build output is missing." >&2
        echo "    Build it on your dev machine and scp the result to ${dir} on the Pi," >&2
        echo "    then re-run this script." >&2
        exit 1
    fi
}

log "Checking frontend bundles (Kiosk_UI, customer order page, vendor dashboard)"
build_or_instruct frontend "Kiosk_UI" js/main.js js/mock.js
build_or_instruct apps/customer "customer order page" dist/index.html
build_or_instruct apps/vendor "vendor dashboard" dist/index.html

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
    log "Distance-sensor device node detected -- installing pyserial/gpiozero and vertew-sensor.service too"
    "${REPO_ROOT}/backend/.venv/bin/pip" install -r "${REPO_ROOT}/backend/requirements-hardware.txt"
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
  curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/order/store/demo   # should print 200
  curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/vendor            # should print 200

The customer order page is what the kiosk's QR code points to; the vendor
dashboard (order alerts + "call the owner" alerts) is meant to be opened on
the vendor's own phone at http://<pi-lan-ip>:8000/vendor?store=demo.
EOF
