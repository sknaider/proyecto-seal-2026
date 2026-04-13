#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  SEAL Relaunch — resurrect a dead agent (JARVIS/ADA/ALICE)
#  Reproduces the desktop-button method from terminal/CLI.
#  Uses DISPLAY=:0 + setsid kitty + <agent>_fresh.sh
#  Origin: 2026-04-12, after ADA restart suicide (chat_server
#  pkill from inside her own process group killed kitty+claude).
# ═══════════════════════════════════════════════════════════

set -e

AGENT="${1^^}"  # uppercase
VALID=(JARVIS ADA ALICE)

if [ -z "$AGENT" ]; then
  echo "uso: $0 <JARVIS|ADA|ALICE> [--force]"
  exit 1
fi

# Validate agent name
VALID_MATCH=0
for v in "${VALID[@]}"; do
  [ "$v" = "$AGENT" ] && VALID_MATCH=1 && break
done
if [ "$VALID_MATCH" -eq 0 ]; then
  echo "[seal_relaunch] agente inválido: $AGENT (válidos: ${VALID[*]})"
  exit 1
fi

FORCE=0
[ "$2" = "--force" ] && FORCE=1

LC_AGENT="${AGENT,,}"
FRESH="/home/dadito/IA/proyecto-seal/${LC_AGENT}_fresh.sh"

if [ ! -f "$FRESH" ]; then
  echo "[seal_relaunch] launcher no encontrado: $FRESH"
  exit 2
fi

# Already alive?
ALIVE_PID=$(pgrep -f "claude.*--name ${AGENT}" | head -1)
if [ -n "$ALIVE_PID" ] && [ "$FORCE" -eq 0 ]; then
  echo "[seal_relaunch] $AGENT ya está vivo (PID $ALIVE_PID). Usa --force para re-lanzar."
  exit 0
fi

# Force kill old dangling kitty/claude before relaunch
if [ "$FORCE" -eq 1 ]; then
  echo "[seal_relaunch] --force: matando procesos viejos de $AGENT..."
  # SIGTERM first, wait, then SIGKILL any survivors (claude often ignores TERM
  # when blocked in a read/ioctl — we hit exactly this with ALICE 2026-04-12).
  for PID in $(pgrep -f "claude.*--name ${AGENT}"); do
    kill "$PID" 2>/dev/null || true
  done
  for PID in $(pgrep -f "kitty.*${AGENT}"); do
    kill "$PID" 2>/dev/null || true
  done
  sleep 1
  for PID in $(pgrep -f "claude.*--name ${AGENT}"); do
    echo "[seal_relaunch] SIGTERM ignorado, SIGKILL PID $PID"
    kill -9 "$PID" 2>/dev/null || true
  done
  for PID in $(pgrep -f "kitty.*${AGENT}"); do
    kill -9 "$PID" 2>/dev/null || true
  done
  sleep 0.3
fi

# Final checkpoint for the dead session (if any context recoverable from DB)
VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
CHECKPOINT="/home/dadito/IA/proyecto-seal/messages/session_checkpoint.py"
if [ -f "$CHECKPOINT" ]; then
  $VENV_PY "$CHECKPOINT" --agent "$AGENT" --final 2>/dev/null || true
fi

# Mark offline so webchat sidebar reflects the outage during relaunch
curl -s -X POST "http://localhost:8765/api/agents/status" \
  -H "Content-Type: application/json" \
  -d "{\"agent\":\"$AGENT\",\"status\":\"offline\"}" > /dev/null 2>&1 || true

# Resolve DISPLAY — auto-detect active X socket (soporta :0, :10, xrdp)
if [ -z "$DISPLAY" ] || ! xdpyinfo -display "$DISPLAY" &>/dev/null; then
  for d in /tmp/.X11-unix/X*; do
    NUM="${d##*/X}"
    if xdpyinfo -display ":${NUM}" &>/dev/null 2>&1; then
      export DISPLAY=":${NUM}"
      break
    fi
  done
fi
DISPLAY_VAL="${DISPLAY:-:0}"
export DISPLAY="$DISPLAY_VAL"

LOG="/tmp/${LC_AGENT}_relaunch.log"
TMUX_SESSION="seal-${LC_AGENT}"

# Kill stale tmux session for this agent (if any)
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true

LAUNCHED=false
LAUNCH_METHOD=""

# Tier 1: kitty (preferred)
if [ "$LAUNCHED" = false ] && command -v kitty &>/dev/null && xdpyinfo -display "$DISPLAY_VAL" &>/dev/null 2>&1; then
  echo "[seal_relaunch] DISPLAY=$DISPLAY_VAL — spawning kitty for $AGENT..."
  DISPLAY="$DISPLAY_VAL" setsid kitty \
    --title "$AGENT — Team SEAL" \
    -o initial_window_width=140c -o initial_window_height=40c \
    bash "$FRESH" \
    </dev/null >"$LOG" 2>&1 &
  disown
  LAUNCHED=true
  LAUNCH_METHOD="kitty"
fi

# Tier 2: xfce4-terminal fallback
if [ "$LAUNCHED" = false ] && command -v xfce4-terminal &>/dev/null && xdpyinfo -display "$DISPLAY_VAL" &>/dev/null 2>&1; then
  echo "[seal_relaunch] DISPLAY=$DISPLAY_VAL — kitty no disponible, usando xfce4-terminal..."
  DISPLAY="$DISPLAY_VAL" setsid xfce4-terminal \
    --title "$AGENT — Team SEAL" \
    --geometry=140x40 \
    -e "bash $FRESH" \
    </dev/null >"$LOG" 2>&1 &
  disown
  LAUNCHED=true
  LAUNCH_METHOD="xfce4-terminal"
fi

# Tier 3: headless tmux fallback (no display)
if [ "$LAUNCHED" = false ]; then
  echo "[seal_relaunch] ⚠️  sin display visible — arrancando $AGENT HEADLESS vía tmux..."
  tmux new-session -d -s "$TMUX_SESSION" -n "$AGENT" "bash $FRESH" 2>"$LOG"
  LAUNCHED=true
  LAUNCH_METHOD="tmux-headless"
  echo "[seal_relaunch] Para ver: tmux attach -t $TMUX_SESSION"
fi

# Brief wait, then verify
sleep 1.5
NEW_TERM=$(pgrep -af "(kitty|xfce4-terminal).*${AGENT}" | head -1 | awk '{print $1}')
NEW_CLAUDE=$(pgrep -f "claude.*--name ${AGENT}" | head -1)
TMUX_ALIVE=$(tmux has-session -t "$TMUX_SESSION" 2>/dev/null && echo 1 || echo 0)

if [ "$LAUNCH_METHOD" = "tmux-headless" ]; then
  if [ "$TMUX_ALIVE" = "1" ]; then
    echo "[seal_relaunch] ✅ tmux session $TMUX_SESSION activa"
  else
    echo "[seal_relaunch] ❌ FAIL — tmux no arrancó. Log: $LOG"
    tail -10 "$LOG" 2>/dev/null
    exit 3
  fi
elif [ -z "$NEW_TERM" ]; then
  echo "[seal_relaunch] ❌ FAIL — terminal ($LAUNCH_METHOD) no arrancó. Log: $LOG"
  tail -10 "$LOG" 2>/dev/null
  exit 3
else
  echo "[seal_relaunch] ✅ $LAUNCH_METHOD PID $NEW_TERM"
fi

if [ -n "$NEW_CLAUDE" ]; then
  echo "[seal_relaunch] ✅ claude PID $NEW_CLAUDE (arrancando boot_context)"
else
  echo "[seal_relaunch] ⏳ claude aún arrancando (espera ~3s para su auto-announce en webchat)"
fi

exit 0
