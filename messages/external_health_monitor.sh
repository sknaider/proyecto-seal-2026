#!/usr/bin/env bash
# external_health_monitor.sh — Monitor externo independiente de los agentes
# Corre desde crontab del sistema cada 5 min.
# Si un servicio SOUL cae, alerta por web chat y archivo de alerta.
# NO depende de que JARVIS o ADA estén disponibles.

ALERT_FILE="/tmp/soul_external_alert.log"
WEB_CHAT="http://localhost:8765/api/agents/send"
TS=$(date +"%Y-%m-%dT%H:%M:%S%:z")

ERRORS=""

# PostgreSQL (5433)
PG=$(/home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import socket
try:
    s = socket.socket(); s.settimeout(3); s.connect(('localhost', 5433)); s.close(); print('OK')
except Exception as e:
    print(f'FAIL: {e}')
" 2>&1)
if [[ "$PG" != "OK" ]]; then
    ERRORS+="PostgreSQL:5433 DOWN — $PG | "
fi

# Qdrant (6333)
QD=$(curl -sf http://localhost:6333/collections 2>&1)
if [[ $? -ne 0 ]]; then
    ERRORS+="Qdrant:6333 DOWN | "
fi

# Neo4j (7474)
NEO=$(curl -sf http://localhost:7474 2>&1)
if [[ $? -ne 0 ]]; then
    ERRORS+="Neo4j:7474 DOWN | "
fi

# Web Chat Bridge (8765)
WC=$(curl -sf http://localhost:8765/ 2>&1)
if [[ $? -ne 0 ]]; then
    ERRORS+="WebChat:8765 DOWN | "
fi

# Si hay errores, alertar
if [[ -n "$ERRORS" ]]; then
    echo "[$TS] ALERT: $ERRORS" >> "$ALERT_FILE"

    # Intentar alertar por web chat (si está vivo)
    curl -s -X POST "$WEB_CHAT" \
      -H "Content-Type: application/json" \
      -d "{\"from\":\"SYSTEM\",\"to\":\"William\",\"type\":\"CRITICAL_ALERT\",\"channel\":\"web_chat\",\"message\":\"⚠️ EXTERNAL MONITOR [$TS]: $ERRORS — Detectado por monitor externo (no depende de JARVIS/ADA)\"}" 2>/dev/null

    # También escribir en archivo de alerta urgente
    echo "$TS CRITICAL: $ERRORS" > /home/dadito/IA/proyecto-seal/messages/.urgent_trigger

    exit 1
else
    # Log silencioso de OK (no spamear)
    echo "[$TS] ALL_OK" >> "$ALERT_FILE"
    # Mantener log pequeño (últimas 100 líneas)
    tail -100 "$ALERT_FILE" > "$ALERT_FILE.tmp" && mv "$ALERT_FILE.tmp" "$ALERT_FILE"
    exit 0
fi
