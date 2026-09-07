#!/usr/bin/env bash
# Idempotent restart for SEAL Studio (Next.js frontend on :3001)
# Guarantees: at most ONE next-server process listening on PORT after exit.
# Usage: bash seal_studio_restart.sh [--rebuild]

set -euo pipefail

PORT="${PORT:-3001}"
FRONTEND_DIR="/home/dadito/IA/proyecto-seal/seal-studio/frontend"
LOCK="/tmp/seal_studio_restart.lock"
PIDFILE="/tmp/seal_studio.pid"
LOGFILE="/tmp/next.log"
REBUILD="${1:-}"

# Serialize concurrent restarts — flock blocks until previous run exits.
exec 9>"$LOCK"
flock -x 9

cd "$FRONTEND_DIR"

# Load Node 24 (build requirement).
export NVM_DIR="$HOME/.nvm"
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh" >/dev/null 2>&1 || true
nvm use 24 >/dev/null 2>&1 || true

echo "[$(date -Is)] seal_studio_restart: stopping any listener on :$PORT"

# Kill anything holding the port — covers zombies + races from prior runs.
# ss -lntpH doesn't exist everywhere, so parse directly.
HOLDERS=$(ss -ltnp 2>/dev/null | awk -v p=":$PORT" '$4 ~ p {print}' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true)
for pid in $HOLDERS; do
  echo "  -> kill $pid"
  kill "$pid" 2>/dev/null || true
done

# Also kill stale next-server processes (covers orphans that lost their port).
pkill -f "next-server" 2>/dev/null || true
pkill -f "next start" 2>/dev/null || true

# Wait up to 5s for the port to free.
for _ in 1 2 3 4 5; do
  if ! ss -ltn 2>/dev/null | awk -v p=":$PORT" '$4 ~ p' | grep -q .; then
    break
  fi
  sleep 1
done

if ss -ltn 2>/dev/null | awk -v p=":$PORT" '$4 ~ p' | grep -q .; then
  echo "ERROR: port $PORT still bound after 5s — aborting"
  exit 2
fi

if [[ "$REBUILD" == "--rebuild" ]]; then
  echo "[$(date -Is)] rebuilding (.next wiped)"
  rm -rf .next
  npm run build
fi

echo "[$(date -Is)] starting next (PORT=$PORT)"
# Close lock fd (9) in the child so npm/next don't inherit it and hold the lock forever.
PORT="$PORT" nohup npm start >"$LOGFILE" 2>&1 9>&- &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
disown || true

# Verify it actually came up.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sS -o /dev/null -w '%{http_code}' "http://localhost:$PORT/" 2>/dev/null | grep -q 200; then
    # Verify HTML asset refs all resolve (guards against stale-HTML bug).
    REFS=$(curl -sS "http://localhost:$PORT/" | grep -oE '_next/static/[^"]+\.(css|js)' | sort -u || true)
    ALL_OK=1
    for f in $REFS; do
      code=$(curl -sS -o /dev/null -w '%{http_code}' "http://localhost:$PORT/$f")
      if [[ "$code" != "200" ]]; then
        echo "ERROR: asset $f -> HTTP $code (stale build?)"
        ALL_OK=0
        break
      fi
    done
    if [[ "$ALL_OK" == "1" ]]; then
      echo "[$(date -Is)] seal_studio_restart: OK pid=$NEW_PID port=$PORT assets=$(echo "$REFS" | wc -l)"
      exit 0
    fi
    exit 3
  fi
  sleep 1
done

echo "ERROR: next never responded on :$PORT"
tail -20 "$LOGFILE"
exit 4
