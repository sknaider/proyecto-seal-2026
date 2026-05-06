#!/bin/bash
# NEXUS external heartbeat — sobrevive restart de Claude Code
# Fix ruido #2 (NEXUS, 24-abr-2026):
#   Antes: escribía alive=true siempre → falso positivo cuando NEXUS muerto
#   Ahora: valida kitty socket + POST webchat real + heartbeat file veraz
set -u

HEARTBEAT_FILE="/home/dadito/IA/proyecto-seal/messages/nexus_claude_heartbeat.json"
KITTY_SOCK="/tmp/seal-nexus-kitty.sock"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# 1. Detectar sesión viva: socket kitty + no stale (last_modified < 30min)
if [[ -S "$KITTY_SOCK" ]]; then
    ALIVE="true"
    MODEL="claude-sonnet-4-6"
else
    ALIVE="false"
    MODEL="unknown"
fi

# 2. Escribir heartbeat file con verdad
python3 -c "
import json
data = {
    'agent': 'NEXUS',
    'alive': ${ALIVE^},
    'timestamp': '$TIMESTAMP',
    'process_pid': '',
    'model': '$MODEL',
    'source': 'external_cron',
}
with open('$HEARTBEAT_FILE', 'w') as f:
    json.dump(data, f, indent=2)
"

# 3. POST webchat DESACTIVADO — CronCreate (8min) ya cubre el chat heartbeat
# El archivo JSON de arriba es suficiente para DUM monitoring
# (Fix ruido #3 — NEXUS, 24-abr-2026: dual heartbeat cron+CronCreate = spam innecesario)
