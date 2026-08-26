#!/usr/bin/env bash
#
# Vertew kiosk autostart (Raspberry Pi 5 / Chromium).
#
# Launches the Kiosk_UI in Chromium fullscreen (kiosk) mode pointing at the
# local Conversation_Server, which serves the Kiosk_UI from localhost
# (backend/main.py, Req 9.1). The script implements Requirement 11:
#
#   11.1 Launch the Kiosk_UI in Chromium fullscreen on autostart.
#   11.2 Fill 100% of the screen with no visible scrollbars/borders/title bar.
#   11.3 Hide browser navigation controls and the address bar.
#   11.4 Hide the mouse cursor after 5 seconds of input inactivity.
#   11.5 Retry the launch up to 3 times; after the final failed attempt show a
#        visible on-screen indication that the display failed to start.
#
# The script degrades gracefully when optional helpers (unclutter, xmessage,
# zenity) are not installed so it still runs on a minimal image.

# Safety options:
#   -e  exit on unhandled error      -u  error on unset variables
#   -o pipefail  surface errors from any stage of a pipeline
# We deliberately do NOT abort on the Chromium launch itself; that failure is
# handled explicitly by the retry loop below (Req 11.5).
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration (overridable via environment variables)
# ---------------------------------------------------------------------------
# URL of the local Conversation_Server that serves the Kiosk_UI. Uvicorn's
# default bind is 0.0.0.0:8000, so localhost:8000 is the sensible default.
KIOSK_URL="${KIOSK_URL:-http://localhost:8000}"

# Maximum number of launch attempts before showing the failure indication.
KIOSK_MAX_ATTEMPTS="${KIOSK_MAX_ATTEMPTS:-3}"

# Idle seconds before the mouse cursor is hidden (Req 11.4).
KIOSK_CURSOR_IDLE="${KIOSK_CURSOR_IDLE:-5}"

# How long (seconds) Chromium must stay up before a launch is considered
# successful. If Chromium exits before this window elapses we treat it as a
# failed launch and retry (Req 11.5).
KIOSK_LAUNCH_GRACE="${KIOSK_LAUNCH_GRACE:-5}"

log() {
    # Timestamped log line to stderr so it does not interfere with anything
    # parsing stdout.
    printf '%s vertew-kiosk: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$*" >&2
}

# ---------------------------------------------------------------------------
# Locate the Chromium binary across the common names used on Raspberry Pi OS
# (chromium-browser) and Debian/derivatives (chromium).
# ---------------------------------------------------------------------------
find_chromium() {
    local candidate
    for candidate in chromium-browser chromium chromium-browser-stable; do
        if command -v "$candidate" >/dev/null 2>&1; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# Hide the mouse cursor after KIOSK_CURSOR_IDLE seconds of inactivity (Req 11.4).
# unclutter is optional: if it is missing we log and continue rather than fail.
# ---------------------------------------------------------------------------
start_cursor_hider() {
    if command -v unclutter >/dev/null 2>&1; then
        log "hiding cursor after ${KIOSK_CURSOR_IDLE}s idle via unclutter"
        # -idle N: hide after N seconds idle; -root: apply across the root window.
        unclutter -idle "${KIOSK_CURSOR_IDLE}" -root >/dev/null 2>&1 &
    else
        log "unclutter not found; cursor auto-hide unavailable (Req 11.4 degraded)"
    fi
}

# ---------------------------------------------------------------------------
# Build the Chromium kiosk flag set (Req 11.1, 11.2, 11.3).
# ---------------------------------------------------------------------------
chromium_flags() {
    # --kiosk            fullscreen, no navigation controls / address bar (11.1, 11.3)
    # --start-fullscreen reinforce fullscreen fill of the screen (11.2)
    # --noerrdialogs / --disable-* : suppress infobars, translation, session
    #   restore and "didn't shut down correctly" popups so nothing overlays the
    #   character (keeps the screen clean per 11.2/11.3).
    # --overscroll-history-navigation=0 and --hide-scrollbars : remove on-screen
    #   scrollbars where applicable (11.1/11.2).
    # --ignore-gpu-blocklist / --enable-gpu-rasterization / --use-gl=egl : the
    #   character is a real WebGL (three.js) 3D model now, not a 2D sprite --
    #   these get GPU-accelerated WebGL working reliably on the Pi 5's Mesa/V3D
    #   driver via Chromium's EGL backend (the default GL backend picked by
    #   Chromium's built-in GPU blocklist is sometimes overly conservative on
    #   the Pi and falls back to slow software rendering without this).
    cat <<'FLAGS'
--kiosk
--start-fullscreen
--noerrdialogs
--disable-infobars
--disable-translate
--disable-features=TranslateUI,Translate
--disable-session-crashed-bubble
--disable-pinch
--overscroll-history-navigation=0
--hide-scrollbars
--check-for-update-interval=31536000
--no-first-run
--fast
--fast-start
--disable-component-update
--autoplay-policy=no-user-gesture-required
--ignore-gpu-blocklist
--enable-gpu-rasterization
--use-gl=egl
FLAGS
}

# ---------------------------------------------------------------------------
# Present a visible on-screen failure indication after all retries are
# exhausted (Req 11.5). Tries graphical helpers first, then falls back to a
# console message so something is always shown.
# ---------------------------------------------------------------------------
show_failure_indication() {
    local message="$1"
    log "FAILURE: ${message}"

    if command -v zenity >/dev/null 2>&1; then
        zenity --error --no-wrap --title="Vertew display failed" \
            --text="${message}" >/dev/null 2>&1 &
        return 0
    fi

    if command -v xmessage >/dev/null 2>&1; then
        xmessage -center -title "Vertew display failed" "${message}" >/dev/null 2>&1 &
        return 0
    fi

    # Last-resort fallback: paint the message on the console/screen so an
    # operator standing at the kiosk still sees that something went wrong.
    printf '\n\n*** VERTEW DISPLAY FAILED ***\n%s\n\n' "${message}"
    if command -v setterm >/dev/null 2>&1; then
        setterm --foreground red >/dev/null 2>&1 || true
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Attempt to launch Chromium once. Returns 0 if Chromium stayed up past the
# launch grace window, non-zero if it failed to start or exited immediately.
# ---------------------------------------------------------------------------
launch_chromium_once() {
    local browser="$1"
    shift
    local flags=("$@")

    log "launching ${browser} at ${KIOSK_URL}"

    # Start Chromium in the background so we can watch whether it survives the
    # grace window; if it dies immediately the launch is considered failed.
    "${browser}" "${flags[@]}" "${KIOSK_URL}" >/dev/null 2>&1 &
    local pid=$!

    sleep "${KIOSK_LAUNCH_GRACE}"

    if kill -0 "${pid}" >/dev/null 2>&1; then
        log "Chromium running (pid ${pid})"
        # Hand control to Chromium for the rest of the session.
        wait "${pid}"
        return 0
    fi

    log "Chromium exited within ${KIOSK_LAUNCH_GRACE}s of launch"
    return 1
}

# ---------------------------------------------------------------------------
# Main: locate Chromium, start the cursor hider, then retry the launch up to
# KIOSK_MAX_ATTEMPTS times before showing the failure indication (Req 11.5).
# ---------------------------------------------------------------------------
main() {
    local browser
    if ! browser="$(find_chromium)"; then
        show_failure_indication "Chromium is not installed (looked for chromium-browser and chromium)."
        exit 1
    fi

    start_cursor_hider

    # Read the flag set into an array so each flag is passed as a separate arg.
    local flags=()
    while IFS= read -r flag; do
        [ -n "${flag}" ] && flags+=("${flag}")
    done < <(chromium_flags)

    local attempt
    for (( attempt = 1; attempt <= KIOSK_MAX_ATTEMPTS; attempt++ )); do
        log "launch attempt ${attempt}/${KIOSK_MAX_ATTEMPTS}"
        if launch_chromium_once "${browser}" "${flags[@]}"; then
            # Chromium ran and has now exited (e.g. session ended). Done.
            log "Chromium session ended after a successful launch"
            exit 0
        fi
        log "attempt ${attempt} failed"
    done

    show_failure_indication \
        "Chromium failed to launch in fullscreen after ${KIOSK_MAX_ATTEMPTS} attempts."
    exit 1
}

main "$@"
