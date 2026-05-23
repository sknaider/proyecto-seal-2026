#!/bin/bash
# Soul App — Personal Assistant launcher
# Opens companion_core UI as standalone app window, or focuses it if already open

COMPANION_PORT=5174
BACKEND_PORT=8769
SEAL_DIR="/home/dadito/IA/proyecto-seal/seal-desktop"
PROFILE_DIR="$HOME/.config/seal-companion-profile"

# Start backend if not running
if ! curl -s -m 2 "http://localhost:${BACKEND_PORT}/api/health" >/dev/null 2>&1; then
    echo "Starting companion_core backend..."
    cd "$SEAL_DIR/companion_core"
    source .venv/bin/activate 2>/dev/null || true
    nohup python3 -m uvicorn companion_core.main:app \
        --host 0.0.0.0 --port "${BACKEND_PORT}" --log-level warning \
        > /tmp/companion_core.log 2>&1 &
    sleep 3
fi

# Start UI dev server if not running
if ! curl -s -m 2 "http://localhost:${COMPANION_PORT}/" >/dev/null 2>&1; then
    echo "Starting companion UI dev server..."
    cd "$SEAL_DIR/companion_core/ui"
    nohup npm run dev > /tmp/companion_ui.log 2>&1 &
    sleep 4
fi

# If companion window already open, focus it
EXISTING=$(DISPLAY=:0 wmctrl -l 2>/dev/null | grep "Soul App" | grep -v "Brave\|Studio\|Team" | head -1 | awk '{print $1}')
if [ -n "$EXISTING" ]; then
    DISPLAY=:0 wmctrl -ia "$EXISTING" 2>/dev/null
    echo "Companion already open — focused window $EXISTING"
    exit 0
fi

# Open as standalone app window with persistent profile
mkdir -p "$PROFILE_DIR"
DISPLAY=:0 brave-browser \
    --user-data-dir="$PROFILE_DIR" \
    --app="http://localhost:${COMPANION_PORT}" \
    --window-size=1060,720 \
    --no-first-run \
    --disable-sync \
    2>/dev/null &

echo "Soul App launched at http://localhost:${COMPANION_PORT}"
