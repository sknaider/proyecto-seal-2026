#!/bin/bash
# seal_ada_watcher.sh — Zero-token ADA message watcher
#
# Corre por systemd timer (independiente de Claude loops).
# Llama check_jarvis.sh → si hay mensajes NUEVOS → los escribe en
# ada_wakeup.jsonl (FileChanged hook lo inyecta en Claude automáticamente).
# Si no hay nada nuevo → sale sin hacer nada (0 tokens consumidos).

MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
WAKEUP_FILE="$MESSAGES_DIR/ada_wakeup.jsonl"
CHECK_SCRIPT="$MESSAGES_DIR/check_jarvis.sh"
LOCK="/tmp/seal_ada_watcher.lock"

# Singleton lock
if [ -f "$LOCK" ]; then
    LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    [ "$LOCK_AGE" -lt 60 ] && exit 0
    rm -f "$LOCK"
fi
( set -C; echo $$ > "$LOCK" ) 2>/dev/null || exit 0
trap "rm -f $LOCK" EXIT

# Ejecutar check_jarvis (ADA lee mensajes de JARVIS)
RESULT=$(bash "$CHECK_SCRIPT" 2>/dev/null)

# Si hay mensajes nuevos → escribir al wakeup file
if echo "$RESULT" | grep -q "^NEW:"; then
    MSG_COUNT=$(echo "$RESULT" | head -1 | grep -oP '\d+')
    MSG_CONTENT=$(echo "$RESULT" | tail -n +2)
    TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    MSG_CONTENT="$MSG_CONTENT" python3 -c "
import json, os
data = {
    'from': 'JARVIS',
    'type': 'wakeup',
    'timestamp': '${TS}',
    'count': ${MSG_COUNT:-1},
    'message': os.environ['MSG_CONTENT'][:500],
}
with open('${WAKEUP_FILE}', 'a') as f:
    f.write(json.dumps(data, ensure_ascii=False) + '\n')
" 2>/dev/null
fi
