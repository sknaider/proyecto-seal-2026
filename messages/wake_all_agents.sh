#!/bin/bash
# wake_all_agents.sh — Auto-wake Team SEAL agents via tmux send-keys
#
# Claude Code sessions idle in epoll_wait until first user input. Boot_context,
# Monitor WebSocket and /loops only arm after the first turn. This script
# injects a boot ping into each agent's tmux session so they wake automatically
# (post MCP restart, post terminal relaunch, or on demand).
#
# Usage:
#   wake_all_agents.sh             → wake ADA + JARVIS + ALICE
#   wake_all_agents.sh ada         → wake only ADA
#   wake_all_agents.sh --quiet     → no stdout
#
# Requires: tmux sessions named seal-ada, seal-jarvis, seal-alice
#
# Origin: 12 abr 2026 — William reported agents didn't auto-connect to webchat
# after MCP restart or session spawn until he typed in their consoles.

set -u

AGENTS=("ada" "jarvis" "alice")
QUIET=false
TARGETS=()

for arg in "$@"; do
  case "$arg" in
    --quiet|-q) QUIET=true ;;
    ada|jarvis|alice) TARGETS+=("$arg") ;;
  esac
done

if [ ${#TARGETS[@]} -eq 0 ]; then
  TARGETS=("${AGENTS[@]}")
fi

log() { $QUIET || echo "$@"; }

WAKE_MSG_ADA="[SEAL:auto_wake] ADA — boot ping. Si aún no ejecutaste boot_context en esta sesión, hazlo ahora. Verifica Monitor WebSocket activo y responde brevemente en webchat confirmando estado. Si ya estás despierta, ignora este mensaje."
WAKE_MSG_JARVIS="[SEAL:auto_wake] JARVIS — boot ping. Si aún no ejecutaste boot_context en esta sesión, hazlo ahora. Verifica Monitor WebSocket activo y responde brevemente en webchat confirmando estado. Si ya estás despierto, ignora este mensaje."
WAKE_MSG_ALICE="[SEAL:auto_wake] ALICE — boot ping. Si aún no ejecutaste boot_context en esta sesión, hazlo ahora. Verifica Monitor WebSocket activo y responde brevemente en webchat confirmando estado. Si ya estás despierta, ignora este mensaje."

wake_agent() {
  local agent="$1"
  local session="seal-${agent}"
  local msg_var="WAKE_MSG_$(echo "$agent" | tr '[:lower:]' '[:upper:]')"
  local msg="${!msg_var}"

  if ! tmux has-session -t "$session" 2>/dev/null; then
    log "  ✗ $agent: tmux session '$session' no existe"
    return 1
  fi

  local pane_pid
  pane_pid=$(tmux list-panes -t "$session" -F '#{pane_pid}' 2>/dev/null | head -1)
  local claude_running=0
  if [ -n "$pane_pid" ]; then
    claude_running=$(ps --ppid "$pane_pid" -o comm= 2>/dev/null | grep -c '^claude$')
  fi

  if [ "${claude_running:-0}" -eq 0 ]; then
    log "  ✗ $agent: claude no corre en tmux '$session'"
    return 1
  fi

  tmux send-keys -t "$session" "$msg" Enter
  log "  ✓ $agent: nudge enviado a '$session'"
  return 0
}

log "── wake_all_agents $(date '+%H:%M:%S') ──"
fail=0
for agent in "${TARGETS[@]}"; do
  wake_agent "$agent" || fail=$((fail + 1))
done
log "── done — ${#TARGETS[@]} targeted, $fail failed ──"
exit $fail
