#!/usr/bin/env bash
#
# Stops whatever start-vertew.sh last started (tracked in vertew-run.json).
# Safe to run even if nothing is running.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_FILE="${ROOT}/vertew-run.json"
URL_FILE="${ROOT}/vertew-url.txt"

if [ ! -f "${RUN_FILE}" ]; then
    echo "Nothing tracked as running (no vertew-run.json). Nothing to stop."
    exit 0
fi

server_pid="$(sed -n 's/.*"serverPid":[[:space:]]*\([0-9]*\).*/\1/p' "${RUN_FILE}")"
tunnel_pid="$(sed -n 's/.*"tunnelPid":[[:space:]]*\([0-9]*\).*/\1/p' "${RUN_FILE}")"

stopped_any=false
if [ -n "${server_pid}" ] && kill -0 "${server_pid}" >/dev/null 2>&1; then
    kill "${server_pid}"
    echo "Stopped backend server (PID ${server_pid})."
    stopped_any=true
fi
if [ -n "${tunnel_pid}" ] && kill -0 "${tunnel_pid}" >/dev/null 2>&1; then
    kill "${tunnel_pid}"
    echo "Stopped cloudflared tunnel (PID ${tunnel_pid})."
    stopped_any=true
fi

if [ "${stopped_any}" != true ]; then
    echo "Tracked processes were already gone."
fi

# Belt-and-suspenders: also match by command line, in case the tracked PID
# ever doesn't correspond to a live process (e.g. vertew-run.json survived a
# crash before this script last ran). No-op if pkill isn't installed or
# nothing matches.
if command -v pkill >/dev/null 2>&1; then
    pkill -f "uvicorn main:app.*--port 8000" >/dev/null 2>&1 || true
    pkill -f "cloudflared tunnel --url http://localhost:8000" >/dev/null 2>&1 || true
fi

rm -f "${RUN_FILE}" "${URL_FILE}"
echo "Vertew stopped."
