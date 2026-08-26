#!/usr/bin/env bash
#
# Starts the Vertew backend (serving all three frontends) plus a Cloudflare
# quick tunnel, then opens the public URL in a normal Chromium window.
#
# This is the "just get a demo up and share the link" launcher -- for the
# always-on fullscreen kiosk display itself, use kiosk.sh (autostarted by
# install.sh's systemd services), which points at the LOCAL server, not a
# public tunnel.
#
# Safe to re-run: it first stops whatever a previous run of this script left
# behind (tracked in vertew-run.json), so it never piles up stray processes.
#
# Requires: backend/.venv already set up (see install.sh or README.md "Local
# setup") and the frontend/apps builds already in place. cloudflared is
# optional -- without it, the server still starts but no public URL/browser
# launch happens (you'd use kiosk.sh / localhost:8000 directly instead).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

RUN_FILE="${ROOT}/vertew-run.json"
URL_FILE="${ROOT}/vertew-url.txt"
SERVER_LOG="${ROOT}/backend/server_run.log"
TUNNEL_LOG="${ROOT}/backend/cloudflared.log"

log() { printf '\n==> %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Stop whatever a previous run of this script left behind, so re-running
# never accumulates stray background processes.
# ---------------------------------------------------------------------------
stop_tracked_run() {
    [ -f "${RUN_FILE}" ] || return 0
    local server_pid tunnel_pid
    server_pid="$(sed -n 's/.*"serverPid":[[:space:]]*\([0-9]*\).*/\1/p' "${RUN_FILE}")"
    tunnel_pid="$(sed -n 's/.*"tunnelPid":[[:space:]]*\([0-9]*\).*/\1/p' "${RUN_FILE}")"
    [ -n "${server_pid}" ] && kill "${server_pid}" >/dev/null 2>&1
    [ -n "${tunnel_pid}" ] && kill "${tunnel_pid}" >/dev/null 2>&1
    rm -f "${RUN_FILE}"
}

log "Stopping any previous run..."
stop_tracked_run

# ---------------------------------------------------------------------------
# Start the backend server.
# ---------------------------------------------------------------------------
VENV_PY="${ROOT}/backend/.venv/bin/python"
if [ ! -x "${VENV_PY}" ]; then
    echo "No backend/.venv found at ${VENV_PY}." >&2
    echo "Set it up first: ./scripts/install.sh (or see README.md 'Local setup')." >&2
    exit 1
fi

log "Starting backend server..."
rm -f "${SERVER_LOG}"
# No --reload: this launcher is for quickly getting a demo/testing session
# up, not active development.
(
    cd "${ROOT}/backend"
    exec "${VENV_PY}" -m uvicorn main:app --host 0.0.0.0 --port 8000 >"${SERVER_LOG}" 2>&1
) &
# The subshell's own `exec` replaces its process image with uvicorn itself
# (same PID, no separate child) -- $! of the backgrounded subshell is exactly
# the PID to track, no pgrep/pattern-matching needed.
SERVER_PID=$!

server_ready=false
for _ in $(seq 1 40); do
    if curl -fsS -o /dev/null "http://127.0.0.1:8000/api/stores/demo/menu" 2>/dev/null; then
        server_ready=true
        break
    fi
    sleep 0.5
done

if [ "${server_ready}" != true ]; then
    echo "Server did not come up. Last lines of backend/server_run.log:" >&2
    tail -n 30 "${SERVER_LOG}" >&2 2>/dev/null
    [ -n "${SERVER_PID}" ] && kill "${SERVER_PID}" >/dev/null 2>&1
    exit 1
fi
log "Backend ready: http://localhost:8000"

# ---------------------------------------------------------------------------
# Start the Cloudflare quick tunnel (optional -- only if cloudflared is on PATH).
# ---------------------------------------------------------------------------
TUNNEL_PID=""
PUBLIC_URL=""
if command -v cloudflared >/dev/null 2>&1; then
    log "Starting Cloudflare tunnel..."
    rm -f "${TUNNEL_LOG}"
    cloudflared tunnel --url http://localhost:8000 >"${TUNNEL_LOG}" 2>&1 &
    TUNNEL_PID=$!

    for _ in $(seq 1 40); do
        PUBLIC_URL="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "${TUNNEL_LOG}" 2>/dev/null | head -n1)"
        [ -n "${PUBLIC_URL}" ] && break
        sleep 0.5
    done

    if [ -z "${PUBLIC_URL}" ]; then
        echo "Tunnel did not report a URL in time -- check backend/cloudflared.log." >&2
    fi
else
    echo "cloudflared not found on PATH -- skipping the public tunnel (local URL still works)." >&2
fi

cat > "${RUN_FILE}" <<JSON
{
  "serverPid": ${SERVER_PID:-null},
  "tunnelPid": ${TUNNEL_PID:-null},
  "url": $( [ -n "${PUBLIC_URL}" ] && printf '"%s"' "${PUBLIC_URL}" || printf 'null' )
}
JSON

if [ -n "${PUBLIC_URL}" ]; then
    printf '%s' "${PUBLIC_URL}" > "${URL_FILE}"
else
    rm -f "${URL_FILE}"
fi

echo ""
echo "================================================="
echo " Vertew is running"
echo " Hologram (local):  http://localhost:8000/?display=hologram"
echo " Customer (local):  http://localhost:8000/order/store/demo"
echo " Vendor (local):    http://localhost:8000/vendor?store=demo"
if [ -n "${PUBLIC_URL}" ]; then
    echo " Public (anyone):   ${PUBLIC_URL}"
else
    echo " Public URL:        unavailable (see warning above)"
fi
echo "================================================="
echo ""

# ---------------------------------------------------------------------------
# Open the public URL (falling back to the local one) in a normal Chromium
# window -- NOT kiosk/fullscreen mode; use kiosk.sh for the always-on display.
# ---------------------------------------------------------------------------
OPEN_URL="${PUBLIC_URL:-http://localhost:8000/?display=hologram}"
BROWSER=""
for candidate in chromium-browser chromium google-chrome google-chrome-stable; do
    if command -v "${candidate}" >/dev/null 2>&1; then
        BROWSER="${candidate}"
        break
    fi
done

if [ -n "${BROWSER}" ] && [ -n "${DISPLAY:-}" ]; then
    log "Opening ${OPEN_URL} in ${BROWSER}..."
    "${BROWSER}" --new-window "${OPEN_URL}" >/dev/null 2>&1 &
else
    if [ -z "${BROWSER}" ]; then
        echo "No Chromium/Chrome found on PATH -- open ${OPEN_URL} manually." >&2
    else
        echo "No graphical display (\$DISPLAY unset) -- open ${OPEN_URL} manually." >&2
    fi
fi

echo "Run stop-vertew.sh when you're done."
