#!/bin/bash
# Supervisor persistente del visor visible de NEXUS.
# Mantiene tmux/Claude intactos y recupera solamente Kitty cuando se desprende.
set -u
set -o pipefail

SEAL_HOME="/home/dadito/IA/proyecto-seal"
BOOT="$SEAL_HOME/tools/seal_nexus_terminal_boot.sh"
TMUX_SOCKET="seal-nexus"
TMUX_SESSION="seal-nexus"
KITTY_SOCKET="/tmp/seal-nexus-kitty.sock"
LOG="/tmp/seal-nexus-terminal-supervisor.log"
INTERVAL="${NEXUS_TERMINAL_INTERVAL:-10}"

log() {
  printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"
}

session_healthy() {
  tmux -L "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null || return 1
  ! tmux -L "$TMUX_SOCKET" list-panes -t "$TMUX_SESSION" -F '#{pane_dead}' \
    2>/dev/null | rg -q '^1$'
}

kitty_attached() {
  [ -S "$KITTY_SOCKET" ] || return 1
  local state
  state="$(mktemp)" || return 1
  if ! kitten @ --to="unix:$KITTY_SOCKET" ls >"$state" 2>/dev/null; then
    rm -f "$state"
    return 1
  fi
  if ! python3 - "$state" "$TMUX_SESSION" <<'PY'
import json
import sys

path, session = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as handle:
    state = json.load(handle)
for os_window in state:
    for tab in os_window.get("tabs", []):
        for window in tab.get("windows", []):
            cmdline = " ".join(window.get("cmdline") or [])
            if "tmux" in cmdline and session in cmdline:
                raise SystemExit(0)
raise SystemExit(1)
PY
  then
    rm -f "$state"
    return 1
  fi
  rm -f "$state"
  tmux -L "$TMUX_SOCKET" list-clients -t "$TMUX_SESSION" -F '#{client_tty}' \
    2>/dev/null | rg -q '^/dev/'
}

recover_existing_kitty() {
  [ -S "$KITTY_SOCKET" ] || return 1
  local state window_id child_is_tmux
  state="$(mktemp)" || return 1
  if ! kitten @ --to="unix:$KITTY_SOCKET" ls >"$state" 2>/dev/null; then
    rm -f "$state"
    return 1
  fi
  read -r window_id child_is_tmux < <(
    python3 - "$state" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
for os_window in state:
    for tab in os_window.get("tabs", []):
        for window in tab.get("windows", []):
            cmdline = " ".join(window.get("cmdline") or [])
            print(window["id"], int("tmux" in cmdline and "seal-nexus" in cmdline))
            raise SystemExit(0)
raise SystemExit(1)
PY
  ) || {
    rm -f "$state"
    return 1
  }
  rm -f "$state"

  # Nunca inyectar texto si el hijo aún parece tmux: podría terminar en el
  # prompt vivo de NEXUS durante una carrera. Ese visor se recrea limpiamente.
  [ "$child_is_tmux" = "0" ] || return 1
  kitten @ --to="unix:$KITTY_SOCKET" send-text --match "id:$window_id" \
    "unset TMUX; exec tmux -L '$TMUX_SOCKET' attach -t '$TMUX_SESSION'
" >/dev/null 2>&1 || return 1
  sleep 1
  if kitty_attached; then
    kitten @ --to="unix:$KITTY_SOCKET" focus-window --match "id:$window_id" \
      >/dev/null 2>&1 || true
    log "visor Kitty reenganchado a tmux sin reiniciar NEXUS"
    return 0
  fi
  return 1
}

launch_viewer() {
  if [ -S "$KITTY_SOCKET" ]; then
    local state window_id
    state="$(mktemp)" || return 1
    if kitten @ --to="unix:$KITTY_SOCKET" ls >"$state" 2>/dev/null; then
      window_id="$(
        python3 - "$state" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
for os_window in state:
    for tab in os_window.get("tabs", []):
        for window in tab.get("windows", []):
            print(window["id"])
            raise SystemExit(0)
raise SystemExit(1)
PY
      )" || window_id=""
      if [ -n "$window_id" ]; then
        kitten @ --to="unix:$KITTY_SOCKET" close-window --match "id:$window_id" \
          >/dev/null 2>&1 || true
        sleep 1
      fi
    fi
    rm -f "$state"
    rm -f "$KITTY_SOCKET" 2>/dev/null || true
  fi

  log "visor Kitty ausente; lanzando dentro de nexus-terminal.service"
  bash "$BOOT" >> "$LOG" 2>&1 || return 1
  for _ in $(seq 1 15); do
    kitty_attached && return 0
    sleep 1
  done
  return 1
}

ensure_runtime() {
  session_healthy && return 0
  log "runtime tmux ausente o inválido; delegando recuperación al boot canónico"
  bash "$BOOT" >> "$LOG" 2>&1 || return 1
  session_healthy
}

ensure_viewer() {
  kitty_attached && return 0
  recover_existing_kitty && return 0
  launch_viewer
}

trap 'log "supervisor detenido"; exit 0' TERM INT
log "supervisor iniciado"
while true; do
  ensure_runtime || log "WARN: runtime NEXUS no recuperado en este intento"
  if session_healthy; then
    ensure_viewer || log "WARN: visor NEXUS no recuperado en este intento"
  fi
  sleep "$INTERVAL" &
  wait $!
done
