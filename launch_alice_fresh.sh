#!/bin/bash
# ═══════════════════════════════════════════════
#  ALICE Fresh Launcher — sin timeout, sesión limpia
#  Uso: bash launch_alice_fresh.sh
# ═══════════════════════════════════════════════

SEAL_DIR="/home/dadito/IA/proyecto-seal"

echo "  Matando instancias previas de ALICE..."
EXISTING=$(ps aux | grep "claude.*--name ALICE" | grep -v grep | awk '{print $2}')
if [ -n "$EXISTING" ]; then
    for PID in $EXISTING; do
        kill "$PID" 2>/dev/null
        sleep 1
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    done
    echo "  Vieja ALICE eliminada."
fi

# Matar procesos huerfanos alice.sh / end_session / soul_reflect
pkill -f "alice\.sh" 2>/dev/null
pkill -f "end_session\.sh ALICE" 2>/dev/null
sleep 0.5

echo ""
echo "  Lanzando ALICE fresh (sin timeout)..."
echo "  (Ctrl+C o escribe 'exit' para cerrar ALICE)"
echo ""

cd "$SEAL_DIR"
bash alice.sh --fresh --force
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo "  ⚠️  alice.sh terminó con código de error: $EXIT_CODE"
    echo "  Presiona Enter para cerrar..."
    read
fi
