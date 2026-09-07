#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="${SEAL_APP_URL:-http://127.0.0.1:${SEAL_BACKEND_PORT:-8769}/}"

python3 "$ROOT/scripts/start_backend.py"

if [[ -x "$ROOT/src-tauri/target/release/seal-app" ]]; then
  exec "$ROOT/src-tauri/target/release/seal-app" "$@"
fi

if command -v brave-browser >/dev/null 2>&1; then
  exec brave-browser --app="$URL" --window-size=1060,720 "$@"
elif command -v chromium-browser >/dev/null 2>&1; then
  exec chromium-browser --app="$URL" --window-size=1060,720 "$@"
elif command -v xdg-open >/dev/null 2>&1; then
  exec xdg-open "$URL"
else
  echo "SEAL App is running at $URL"
fi
