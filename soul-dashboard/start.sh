#!/bin/bash
# Soul Dashboard startup
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Kill previous instances
pkill -f "soul_api.py" 2>/dev/null || true
pkill -f "soul-dashboard.*vite" 2>/dev/null || true
sleep 1

# Start backend
python3 soul_api.py > /tmp/soul_api.log 2>&1 &
BACKEND_PID=$!
echo "[soul] backend PID: $BACKEND_PID"

# Wait for backend
sleep 2
if ! curl -sf http://localhost:8850/health > /dev/null; then
    echo "[soul] ERROR: backend no levantó"
    exit 1
fi
echo "[soul] backend OK"

# Start frontend
cd frontend
npm run dev -- --host 0.0.0.0 > /tmp/soul_frontend.log 2>&1 &
FRONTEND_PID=$!
echo "[soul] frontend PID: $FRONTEND_PID"

sleep 2
echo "[soul] ✅ Soul Dashboard listo:"
echo "  Local:    http://localhost:3005"
echo "  Tailscale: http://100.75.201.110:3005"
echo "  API:       http://localhost:8850"
