#!/usr/bin/env bash
# Wrapper de arranque del endpoint de sync (carril NEXUS). Bind a la IP del tailnet (no pública).
# Usado por seal-sync-endpoint.service para que el sync device→central sobreviva reboots.
set -euo pipefail
BIND="$(tailscale ip -4 2>/dev/null | head -1)"
[ -n "$BIND" ] || { echo "[seal-sync-endpoint] sin IP tailnet — no arranco (fail-closed, no expongo público)"; exit 1; }
export SEAL_SYNC_BIND="$BIND"
export SEAL_SYNC_PORT="${SEAL_SYNC_PORT:-8778}"
exec /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/tools/seal_sync_endpoint.py
