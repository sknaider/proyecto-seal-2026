#!/bin/bash
# ═══════════════════════════════════════════════
#  JARVIS Fresh Launcher — sin timeout, sesión limpia
#  Mata instancias previas, sin prompts interactivos
# ═══════════════════════════════════════════════

SEAL_DIR="/home/dadito/IA/proyecto-seal"
LOG="/tmp/jarvis_launch_debug.log"

# Asegurar PATH completo (desktop icons no siempre tienen ~/.local/bin)
export PATH="/home/dadito/.local/bin:$PATH"

echo "  Poniendo a dormir instancias previas de JARVIS claude..."
EXISTING=$(ps aux | grep "claude.*--name JARVIS" | grep -v grep | grep -v "JARVIS_MAYOR" | awk '{print $2}')
if [ -n "$EXISTING" ]; then
    for PID in $EXISTING; do
        kill "$PID" 2>/dev/null
        sleep 1
        kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    done
    echo "  JARVIS anterior dormido."
fi

# Limpiar huérfanos de jarvis.sh / end_session
pkill -f "end_session\.sh JARVIS" 2>/dev/null
sleep 0.5

echo ""
echo "  Despertando JARVIS fresh (sin prompts, sin timeout)..."
echo "  claude: $(which claude 2>/dev/null || echo 'NO EN PATH')"
echo ""

# IMPORTANTE: NO usar pipe/tee aquí — claude necesita TTY real para funcionar
bash /home/dadito/IA/proyecto-seal/jarvis.sh --fresh --force
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -ne 0 ]; then
    echo "  ⚠️  JARVIS terminó con error: $EXIT_CODE — Presiona Enter para cerrar..."
else
    echo "  JARVIS descansando. Presiona Enter para cerrar..."
fi
read
