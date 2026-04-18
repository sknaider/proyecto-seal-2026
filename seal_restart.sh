#!/bin/bash
# seal_restart.sh — Auto-restart de agente SEAL via systemd-run + kitty
# Llamado por seal_agent_resurrect.sh cuando heartbeat muerto
# Uso: seal_restart.sh <ada|jarvis|alice>

AGENT_LOWER="${1,,}"
case "$AGENT_LOWER" in
  ada|jarvis|alice) ;;
  *) echo "Uso: seal_restart.sh <ada|jarvis|alice>"; exit 1 ;;
esac

AGENT_UPPER="${AGENT_LOWER^^}"
SEAL_DIR="/home/dadito/IA/proyecto-seal"
FRESH_SCRIPT="$SEAL_DIR/${AGENT_LOWER}_fresh.sh"
LOG="$SEAL_DIR/messages/seal_restart.log"
UNIT="seal-resurrect-${AGENT_LOWER}-$(date +%s)"

export PATH="/home/dadito/.local/bin:$PATH"
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000

ts() { date '+%Y-%m-%dT%H:%M:%S'; }

echo "[$(ts)] AUTO-RESTART $AGENT_UPPER — systemd-run + kitty" >> "$LOG"

# Idempotencia: si ya hay claude vivo con este nombre, saltar
EXISTING=$(ps -C claude -o pid= -o args= 2>/dev/null | awk "/--name $AGENT_UPPER/{print \$1}" | head -1)
if [ -n "$EXISTING" ]; then
  echo "[$(ts)] SKIP $AGENT_UPPER — claude ya vivo (PID $EXISTING)" >> "$LOG"
  exit 0
fi

if [ ! -x "$FRESH_SCRIPT" ]; then
  echo "[$(ts)] ERROR — $FRESH_SCRIPT no existe o no es ejecutable" >> "$LOG"
  exit 1
fi

# Lanzar via systemd-run + kitty (igual que soul_wake_all.sh — necesita TTY real)
systemd-run --user \
  --unit="$UNIT" \
  --description="SEAL RESURRECT $AGENT_UPPER" \
  /usr/bin/env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 \
    kitty --title "$AGENT_UPPER — Team SEAL" \
    bash "$FRESH_SCRIPT" \
  >> "$LOG" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "[$(ts)] OK $AGENT_UPPER lanzado via systemd-run (unit: $UNIT)" >> "$LOG"
else
  echo "[$(ts)] ERROR systemd-run falló (exit $EXIT_CODE) — unit: $UNIT" >> "$LOG"
  exit 1
fi

exit 0
