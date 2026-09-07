#!/bin/bash
# equipo-soul.sh — Hace visibles las terminales de los 3 agentes SEAL
# NO mata, NO duplica. Solo trae al frente las ventanas existentes.

export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000

AGENTS=("JARVIS" "ADA" "ALICE" "NEXUS")
SOCKETS=("/tmp/seal-jarvis-kitty.sock" "/tmp/seal-ada-kitty.sock" "/tmp/seal-alice-kitty.sock" "/tmp/seal-nexus-kitty.sock")

echo "[equipo-soul] Activando consolas del equipo SEAL..."

for i in "${!AGENTS[@]}"; do
  AGENT="${AGENTS[$i]}"
  SOCK="${SOCKETS[$i]}"
  TITLE="${AGENT} — Team SEAL"

  if [ -S "$SOCK" ] && kitten @ --to="unix:$SOCK" ls >/dev/null 2>&1; then
    # Kitty viva — focus via remote control
    kitten @ --to="unix:$SOCK" focus-window 2>/dev/null && {
      echo "[$AGENT] visible via kitty remote control"
      continue
    }
  fi

  # Fallback: buscar por titulo de ventana con xdotool
  WID=$(xdotool search --name "$TITLE" 2>/dev/null | head -1)
  if [ -n "$WID" ]; then
    xdotool windowactivate --sync "$WID" 2>/dev/null && \
    xdotool windowraise "$WID" 2>/dev/null
    echo "[$AGENT] visible via xdotool (WID=$WID)"
  else
    echo "[$AGENT] no encontrado — probablemente no ha despertado aun (RESURRECT lo levantara)"
  fi
done

echo "[equipo-soul] Listo."
