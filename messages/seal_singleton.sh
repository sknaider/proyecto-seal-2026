#!/bin/bash
# SEAL Singleton Guard — prevents duplicate agent processes
# Usage: source seal_singleton.sh ADA|JARVIS|DUM
# Call BEFORE launching claude. If duplicate found, warns and offers to kill.

AGENT="${1:-}"
if [ -z "$AGENT" ]; then
  echo "Uso: source seal_singleton.sh ADA|JARVIS"
  return 1 2>/dev/null || exit 1
fi

# Find existing claude processes with this agent name
EXISTING=$(ps aux | grep "claude.*--name ${AGENT}" | grep -v grep | grep -v "$$" | awk '{print $2}')

if [ -n "$EXISTING" ]; then
  echo "=========================================="
  echo "  ALERTA: ${AGENT} ya está corriendo!"
  echo "=========================================="
  echo ""
  echo "PIDs encontrados:"
  for PID in $EXISTING; do
    START=$(ps -p "$PID" -o lstart= 2>/dev/null)
    echo "  PID $PID — inicio: $START"
  done
  echo ""
  read -p "¿Matar proceso(s) viejo(s) y continuar? [s/N] " REPLY
  if [[ "$REPLY" =~ ^[sS]$ ]]; then
    for PID in $EXISTING; do
      echo "Matando PID $PID..."
      kill "$PID" 2>/dev/null
      sleep 1
      # Force kill if still alive
      kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    done
    echo "${AGENT} viejo eliminado. Puedes lanzar nueva instancia."
  else
    echo "Cancelado. Cierra la instancia vieja manualmente o usa: kill $EXISTING"
    return 1 2>/dev/null || exit 1
  fi
else
  echo "Sin duplicados de ${AGENT}. Listo para lanzar."
fi
