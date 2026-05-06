#!/bin/bash
# PILAR 1 - Liveness check real para NEXUS (v3)
# Metodo: ultimo mensaje de NEXUS en william_channel.jsonl

CHANNEL=/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl
MAX_IDLE_SECS=480  # 8 minutos

NEXUS_PID=$(pgrep -f 'claude.*NEXUS' | head -1)

if [ -z "$NEXUS_PID" ]; then
  STATUS="MUERTO - proceso no encontrado"
  NIVEL="CRITICAL"
else
  LAST_MSG_TS=$(grep '"from": "NEXUS"' "$CHANNEL" 2>/dev/null | tail -1 | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('timestamp','')[:19])
except: print('')
" 2>/dev/null)

  if [ -z "$LAST_MSG_TS" ]; then
    STATUS="SIN MENSAJES en canal"
    NIVEL="HIGH"
  else
    LAST_EPOCH=$(date -d "$LAST_MSG_TS" +%s 2>/dev/null || echo 0)
    NOW=$(date +%s)
    IDLE_SECS=$((NOW - LAST_EPOCH))

    if [ $IDLE_SECS -gt $MAX_IDLE_SECS ]; then
      STATUS="ZOMBIE-IDLE: ultimo mensaje hace ${IDLE_SECS}s (limite=${MAX_IDLE_SECS}s)"
      NIVEL="HIGH"
    else
      STATUS="VIVO: ultimo mensaje hace ${IDLE_SECS}s"
      NIVEL="OK"
    fi
  fi
fi

TIMESTAMP=$(date '+%Y-%m-%dT%H:%M:%S')
echo "[$TIMESTAMP] NEXUS liveness: $STATUS [$NIVEL]"

if [ "$NIVEL" != "OK" ]; then
  curl -s -X POST http://localhost:8765/api/agents/send \
    -H "Content-Type: application/json" \
    -d "{\"from\":\"DUM\",\"to\":\"William\",\"type\":\"alert\",\"channel\":\"web_chat\",\"message\":\"[NEXUS-LIVENESS] $NIVEL: $STATUS\"}" 2>/dev/null
fi

exit 0
