#!/bin/bash
# seal_keepalive.sh — Anti-idle keepalive para agentes SEAL
# Manda espacio+backspace al TTY de cada agente vía kitty socket.
# Previene Claude idle timeout sin ejecutar ningún comando.
# Busca PID por árbol de procesos (kitty→bash→claude) — no por --name flag
# (fix: claude --resume no incluye --name, pgrep por nombre falla).
# 0 tokens Claude. Corre vía systemd timer cada 4 min.

LOG="/tmp/seal_keepalive.log"
ts() { date '+%Y-%m-%dT%H:%M:%S'; }

for AGENT_LOWER in jarvis ada alice; do
  AGENT_UPPER="${AGENT_LOWER^^}"
  KITTY_SOCK="/tmp/seal-${AGENT_LOWER}-kitty.sock"

  [ -S "$KITTY_SOCK" ] || continue
  kitten @ --to="unix:$KITTY_SOCK" ls >/dev/null 2>&1 || continue

  # Buscar PID de kitty con este socket
  KITTY_PID=$(pgrep -f "kitty.*listen-on.*unix:${KITTY_SOCK}" 2>/dev/null | head -1)
  [ -n "$KITTY_PID" ] || continue

  # Buscar proceso claude descendiente de kitty (kitty → bash → claude)
  PID=""
  for BASH_PID in $(ps --ppid "$KITTY_PID" -o pid= 2>/dev/null); do
    C_PID=$(ps --ppid "$BASH_PID" -o pid= -o comm= 2>/dev/null | awk '/claude/{print $1}' | head -1)
    [ -n "$C_PID" ] && PID="$C_PID" && break
  done
  # Si no encontró a dos niveles, intentar también --name directo (proceso fresco)
  [ -z "$PID" ] && PID=$(ps -C claude -o pid= -o args= 2>/dev/null | awk "/--name ${AGENT_UPPER}/{print \$1}" | head -1)
  [ -n "$PID" ] || continue

  # Enviar espacio + DEL — resetea idle timer sin ejecutar nada en Claude
  kitten @ --to="unix:$KITTY_SOCK" send-text $' \x7f' 2>/dev/null \
    && echo "[$(ts)] KEEPALIVE $AGENT_UPPER — PID $PID, socket OK" >> "$LOG" \
    || echo "[$(ts)] WARN $AGENT_UPPER — send-text falló" >> "$LOG"
done
