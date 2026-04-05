#!/bin/bash
# SOUL AWARENESS launcher
# Inicia el daemon en background, registra PID

VENV=/home/dadito/IA/seal-spark/.venv/bin/python3
SCRIPT=/home/dadito/IA/proyecto-seal/memory/soul_awareness.py
PID_FILE=/home/dadito/IA/proyecto-seal/awareness.pid
LOG_FILE=/home/dadito/IA/proyecto-seal/soul_awareness.log

# Verificar que no corra ya
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "SOUL AWARENESS ya está corriendo (PID $PID)"
        exit 0
    fi
fi

cd /home/dadito/IA/proyecto-seal/memory
nohup $VENV $SCRIPT > /dev/null 2>&1 &
PID=$!
echo $PID > "$PID_FILE"
echo "SOUL AWARENESS iniciado — PID: $PID"
echo "Log: $LOG_FILE"
