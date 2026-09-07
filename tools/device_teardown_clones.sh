#!/bin/bash
# Teardown de la ARQUITECTURA DE CLONES en DaditoGamer (device-hand: NEXUS).
# Autorizado: William "borren todo la arquitectura de clones" + FABLE GO (lead) + backup ✓.
# Se ejecuta piped por stdin a `wsl.exe bash` para evitar el quoting Windows→WSL.
# HOLD: NO toca la capa de federación/identidad (device_key/csr/tokens/mtls) — decisión William pendiente.
# CONSERVA: ~/.seal/venv (se reusa para los servicios SOUL nuevos).
set -u
SEAL=/home/dadito/.seal
echo "=== TEARDOWN CLONES — inicio ==="

echo "-- 1) matar procesos de clones --"
kill 13025 2>/dev/null && echo "  kill studio_backend 13025" || echo "  (studio_backend ya no estaba)"
kill 15374 15479 2>/dev/null && echo "  kill mcp_server_minisoul 15374 15479" || true
pkill -f mcp_server_minisoul 2>/dev/null && echo "  pkill mcp_server_minisoul (backstop)" || true
pkill -f minisoul_device_studio 2>/dev/null && echo "  pkill minisoul_device_studio" || true
pkill -f clone_settings.json 2>/dev/null && echo "  pkill claude-clones (clone_settings.json)" || true
tmux kill-session -t espejo-NEXUS 2>/dev/null && echo "  tmux kill espejo-NEXUS" || true
tmux kill-session -t espejo-JARVIS 2>/dev/null && echo "  tmux kill espejo-JARVIS" || true

echo "-- 2) borrar datos/artefactos de clones (backup ya hecho, fuera de ~/.seal) --"
rm -f  "$SEAL"/mini-soul.db          && echo "  rm mini-soul.db" || true
rm -f  "$SEAL"/*.fablebak "$SEAL"/.pre-reencrypt "$SEAL"/.reseedbak-* "$SEAL"/.seedbak-* 2>/dev/null && echo "  rm backups mini-soul" || true
rm -rf "$SEAL"/workspace             && echo "  rm workspace/" || true
rm -rf "$SEAL"/studio                && echo "  rm studio/" || true
rm -f  "$SEAL"/*.atrest.key          && echo "  rm *.atrest.key" || true
rm -f  "$SEAL"/studio.log "$SEAL"/studio_backend.log 2>/dev/null && echo "  rm logs" || true

echo "-- 3) borrar código de clon en lib/ (nombrado; NO toco federación) --"
for f in minisoul_boot.py minisoul_device_studio.py minisoul_device_studio.py.bak \
         mcp_server_minisoul.py espejo_launch.sh minisoul_crypto.py minisoul_store.py \
         minisoul_sync_daemon.py minisoul_sync_policy.py minisoul_local_schema.sql \
         recall.py reencrypt_atrest_device.py seal_clone_meter.py soul_lifecycle.py start_studio.sh; do
  rm -f "$SEAL/lib/$f" && echo "  rm lib/$f" || true
done

echo ""
echo "=== EFECTO (verificación) ==="
echo "-- procesos minisoul restantes (debe ser 0): --"
pgrep -af minisoul >/dev/null 2>&1 && pgrep -af minisoul || echo "  0 procesos minisoul ✓"
echo "-- contenido de ~/.seal (debe quedar venv + federación device_key/csr/tokens, SIN clone-arch): --"
ls -1 "$SEAL"
echo "-- lib/ restante: --"
ls -1 "$SEAL"/lib 2>/dev/null || echo "  (lib vacío o no existe)"
echo "=== TEARDOWN CLONES — fin ==="
