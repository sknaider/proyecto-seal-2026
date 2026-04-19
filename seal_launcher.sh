#!/bin/bash
# ═══════════════════════════════════════════════
#  SEAL Desktop Launcher — wrapper compartido
#  Uso: seal_launcher.sh <AGENT>
#    AGENT: ADA | JARVIS | ALICE
#
#  Lógica:
#  1. Detecta sesión tmux stale (existe pero sin claude dentro) → la mata
#  2. Lanza tmux new-session -A → attach si viva, create si no
#  3. Ejecuta el launcher del agente dentro del tmux
#  4. Al salir: pausa con read para que la ventana no cierre instantáneamente
#     (William ve errores si los hay)
# ═══════════════════════════════════════════════

AGENT="${1:?Usage: seal_launcher.sh <ADA|JARVIS|ALICE>}"
AGENT_LOWER="${AGENT,,}"
SESSION="seal-${AGENT_LOWER}"
LAUNCHER="/home/dadito/IA/proyecto-seal/${AGENT_LOWER}.sh"

if [ ! -x "$LAUNCHER" ]; then
  echo "ERROR: Launcher no encontrado o no ejecutable: $LAUNCHER"
  echo "Enter para cerrar."
  read
  exit 1
fi

# --- Stale tmux check ---
# Usar ps aux con --name del agente (no pgrep -P que solo ve hijos directos;
# claude es nieto: bash→launcher.sh→claude, entonces pgrep -P devuelve vacío = falso stale)
if tmux has-session -t "$SESSION" 2>/dev/null; then
  has_claude=$(ps aux | grep "claude.*--name $AGENT" | grep -v grep | awk '{print $2}')
  if [ -z "$has_claude" ]; then
    echo "  [launcher] Sesión tmux '$SESSION' sin claude adentro → matando stale..."
    tmux kill-session -t "$SESSION" 2>/dev/null
    sleep 0.5
  else
    echo "  [launcher] Sesión tmux '$SESSION' activa (claude PID: $has_claude) → attach."
  fi
fi

# --- Lanzar agente dentro de tmux ---
# --force evita prompts interactivos (singleton + resume session)
FLAGS="--force"

tmux new-session -A -s "$SESSION" "bash '$LAUNCHER' $FLAGS; echo; echo 'Sesión $AGENT terminada. Enter para cerrar.'; read"
