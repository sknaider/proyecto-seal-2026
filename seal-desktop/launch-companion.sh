#!/usr/bin/env bash
# Backward-compatible entrypoint. The maintained launcher is cross-platform
# paired with launch-seal-app.ps1 on Windows.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$ROOT/launch-seal-app.sh" "$@"
