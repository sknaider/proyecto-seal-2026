#!/bin/bash
# seal_peer_health_watcher.sh — Zero-token peer health watcher.
#
# Corre por systemd timer cada 10min. Ejecuta peer_health_check.sh y:
#   - si output empieza con OK → exit 0 silencioso (0 tokens)
#   - si no → append al jarvis_wakeup.jsonl para despertar a JARVIS
#
# Único consumidor LLM: JARVIS (estratega responsable de escalar a William).
# ADA/ALICE se enteran por DM de JARVIS si aplica.

set -e
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HEALTH_SCRIPT="$MESSAGES_DIR/peer_health_check.sh"
WAKEUP_FILE="$MESSAGES_DIR/jarvis_wakeup.jsonl"
LOCK="/tmp/seal_peer_health_watcher.lock"

# Singleton lock (auto-expira >120s)
if [ -f "$LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    [ "$LOCK_AGE" -lt 120 ] && exit 0
    rm -f "$LOCK"
fi
( set -C; echo $$ > "$LOCK" ) 2>/dev/null || exit 0
trap "rm -f $LOCK" EXIT

# Ejecutar check. "SYSTEM" como SELF_AGENT para que nunca se excluya ninguno.
OUT=$(bash "$HEALTH_SCRIPT" SYSTEM 2>&1 || true)

if [[ "$OUT" == OK* ]]; then
    exit 0
fi

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
OUT_TRUNC="${OUT:0:500}"
OUT="$OUT_TRUNC" python3 - <<'PY' >>"$WAKEUP_FILE"
import json, os
print(json.dumps({
    "from": "SYSTEM",
    "type": "peer_alert",
    "timestamp": os.popen("date -u +%Y-%m-%dT%H:%M:%SZ").read().strip(),
    "message": os.environ.get("OUT", "")[:500],
}))
PY

exit 0
