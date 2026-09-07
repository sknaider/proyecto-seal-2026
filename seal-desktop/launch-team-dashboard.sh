#!/bin/bash
# Soul App 2 — Team Dashboard launcher
# Uses Brave --app mode against the dev server (localhost:5173)
# Falls back to starting the dev server if not running

DASHBOARD_PORT=5173
BACKEND_PORT=8800
SEAL_DIR="/home/dadito/IA/proyecto-seal/seal-desktop"
PROFILE_DIR="$HOME/.config/seal-team-dashboard-profile"

# Start backend if not running
if ! curl -s -m 2 "http://localhost:${BACKEND_PORT}/api/system/health" >/dev/null 2>&1; then
    echo "Starting seal-studio backend..."
    cd "/home/dadito/IA/proyecto-seal/seal-studio/backend"
    nohup /home/dadito/IA/seal-spark/.venv/bin/python3 -m uvicorn main:app \
        --host 0.0.0.0 --port "${BACKEND_PORT}" --log-level warning \
        > /tmp/seal_studio.log 2>&1 &
    sleep 3
fi

# Start UI dev server if not running
if ! curl -s -m 2 "http://localhost:${DASHBOARD_PORT}/" >/dev/null 2>&1; then
    echo "Starting team dashboard UI dev server..."
    cd "$SEAL_DIR/ui"
    nohup npm run dev > /tmp/soul_app2_ui.log 2>&1 &
    sleep 4
fi

# If window already open, focus it
EXISTING=$(DISPLAY=:0 wmctrl -l 2>/dev/null | grep "Soul App 2" | head -1 | awk '{print $1}')
if [ -n "$EXISTING" ]; then
    DISPLAY=:0 wmctrl -ia "$EXISTING" 2>/dev/null
    exit 0
fi

# Open as standalone app window
mkdir -p "$PROFILE_DIR"
DISPLAY=:0 brave-browser \
    --user-data-dir="$PROFILE_DIR" \
    --app="http://localhost:${DASHBOARD_PORT}" \
    --window-size=1400,900 \
    --no-first-run \
    --disable-sync \
    2>/dev/null &

echo "Soul App 2 launched at http://localhost:${DASHBOARD_PORT}"
