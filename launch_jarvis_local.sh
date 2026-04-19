#!/bin/bash
# ═══════════════════════════════════════════════
#  JARVIS Local Launcher — despierta el daemon
#  (jarvis_local_agent.py --daemon --backend minimax-m25)
# ═══════════════════════════════════════════════

SEAL_DIR="/home/dadito/IA/proyecto-seal/memory"
VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"

# Verificar si ya está corriendo
EXISTING=$(ps aux | grep "jarvis_local_agent.py --daemon" | grep -v grep | awk '{print $2}')
if [ -n "$EXISTING" ]; then
    echo "  JARVIS local ya está despierto (PID: $EXISTING)"
    sleep 2
    exit 0
fi

echo "  Despertando JARVIS local (minimax-m25)..."
echo "  Ctrl+C para dormirlo de nuevo."
echo ""

cd "$SEAL_DIR"
$VENV jarvis_local_agent.py --daemon --backend minimax-m25
