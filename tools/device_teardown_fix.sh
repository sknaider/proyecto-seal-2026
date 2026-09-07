#!/bin/bash
# Teardown clones — ROUND 2 (fix por efecto: clones huérfanos vivos + backups con patrón equivocado).
set -u
SEAL=/home/dadito/.seal
echo "=== TEARDOWN FIX — inicio ==="

echo "-- matar clones claude huérfanos (SIGKILL, pid + patrón) --"
kill -9 15319 15403 2>/dev/null && echo "  kill -9 15319 15403" || true
pkill -9 -f clone_settings.json 2>/dev/null && echo "  pkill -9 clone_settings.json" || true
pkill -9 -f mcp_minisoul 2>/dev/null && echo "  pkill -9 mcp_minisoul" || true
pkill -9 -f mcp_server_minisoul 2>/dev/null || true
sleep 1

echo "-- borrar backups mini-soul con patrón CORRECTO (mini-soul.db.*) --"
rm -f "$SEAL"/mini-soul.db.* 2>/dev/null && echo "  rm mini-soul.db.*" || true
echo "-- borrar markers .sleeping (estado de clon) --"
rm -f "$SEAL"/*.sleeping 2>/dev/null && echo "  rm *.sleeping" || true

echo ""
echo "=== EFECTO (verificación round 2) ==="
echo "-- procesos minisoul/clone restantes (debe ser 0): --"
pgrep -af "minisoul\|clone_settings" >/dev/null 2>&1 && pgrep -af "minisoul\|clone_settings" || echo "  0 procesos ✓"
echo "-- restos mini-soul/sleeping en ~/.seal (debe ser 0): --"
ls -1 "$SEAL" 2>/dev/null | grep -iE "mini-soul|sleeping" || echo "  0 restos ✓"
echo "-- ~/.seal final (debe quedar SOLO federación + venv + certs/tokens): --"
ls -1 "$SEAL"
echo "=== TEARDOWN FIX — fin ==="
