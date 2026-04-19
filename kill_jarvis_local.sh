#!/bin/bash
# Duerme el daemon JARVIS local (jarvis_local_agent.py)
# "Dormir" = detener el proceso para que descanse

PID=$(ps aux | grep "jarvis_local_agent.py --daemon" | grep -v grep | awk '{print $2}')

if [ -z "$PID" ]; then
    echo "JARVIS local ya está durmiendo (no encontrado)."
    sleep 2
    exit 0
fi

echo "Poniendo a dormir a JARVIS local (PID: $PID)..."
kill "$PID" 2>/dev/null
sleep 2

if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID" 2>/dev/null
    echo "JARVIS local dormido."
else
    echo "JARVIS local descansando. Buenas noches."
fi
sleep 2
