#!/bin/bash
# Supervisor persistente del runtime visible de ADA Codex.
# Restaura tmux/Codex aun sin GUI y abre Kitty cuando X11 está disponible.
set -u

SEAL_HOME="/home/dadito/IA/proyecto-seal"
LAUNCHER="$SEAL_HOME/ada_launch.sh"
TMUX_SOCKET="seal-ada-codex"
TMUX_SESSION="seal-ada-codex"
KITTY_SOCKET="/tmp/seal-ada-kitty.sock"
LOG="/tmp/seal-ada-autostart.log"
INTERVAL="${ADA_AUTOSTART_INTERVAL:-10}"

log() {
  printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"
}

tmux_healthy() {
  tmux -L "$TMUX_SOCKET" has-session -t "$TMUX_SESSION" 2>/dev/null || return 1
  ! tmux -L "$TMUX_SOCKET" list-panes -t "$TMUX_SESSION" -F '#{pane_dead}' \
    2>/dev/null | rg -q '^1$'
}

kitty_healthy() {
  [ -S "$KITTY_SOCKET" ] || return 1
  local kitty_state
  kitty_state="$(mktemp)" || return 1
  if ! kitten @ --to="unix:$KITTY_SOCKET" ls >"$kitty_state" 2>/dev/null; then
    rm -f "$kitty_state"
    return 1
  fi
  if ! python3 - "$kitty_state" "$TMUX_SESSION" <<'PY'
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
    rm -f "$kitty_state"
    return 1
  fi
  rm -f "$kitty_state"
  tmux -L "$TMUX_SOCKET" list-clients -t "$TMUX_SESSION" -F '#{client_tty}' \
    2>/dev/null | rg -q '^/dev/'
}

gui_available() {
  [ -S /tmp/.X11-unix/X0 ] || [ -S /tmp/.X11-unix/X1 ]
}

ensure_runtime() {
  if tmux_healthy; then
    return 0
  fi
  log "runtime tmux ausente o inválido; restaurando ADA Codex headless"
  ADA_LAUNCH_HEADLESS=1 "$LAUNCHER" >> "$LOG" 2>&1 || return 1
  tmux_healthy
}

ensure_viewer() {
  # DESACTIVADO POR DEFECTO (William, 6-sep-2026): "quita ese renacer de ada claude".
  #
  # QUE HACIA: este loop corre cada 10 s y, si no encontraba la ventana, la
  # reabria. Ese era el "renaces" -- William cerraba la ventana de ADA y
  # reaparecia sola. NO era el tmux de ada.sh (eso ya quedo opt-in aparte).
  #
  # POR QUE SE PUEDE APAGAR SIN DANAR A CODEX: $LAUNCHER apunta a ada_launch.sh,
  # que abre la ventana de ADA **CLAUDE**. kitty_healthy() en cambio busca un
  # kitty adjunto a la sesion **CODEX** ($TMUX_SESSION=seal-ada-codex), asi que
  # esta funcion nunca abria la ventana que decia cuidar: abria la equivocada.
  # ensure_runtime() --que SI mantiene vivo el tmux headless de Codex-- queda
  # intacto y sigue corriendo.
  #
  # Para volver al comportamiento anterior:  SEAL_ADA_AUTOVIEWER=1
  [ "${SEAL_ADA_AUTOVIEWER:-0}" = "1" ] || return 0
  kitty_healthy && return 0
  gui_available || return 0
  log "visor Kitty ausente; abriendo terminal visible de ADA"
  "$LAUNCHER" >> "$LOG" 2>&1 &

  # El visor debe vivir aparte del loop supervisor. Esperar aquí de forma
  # acotada evita abrir duplicados mientras Kitty crea el socket y se conecta.
  local attempt
  for attempt in $(seq 1 50); do
    kitty_healthy && return 0
    sleep 0.1
  done
  return 1
}

trap 'log "supervisor detenido"; exit 0' TERM INT
log "supervisor iniciado"
while true; do
  ensure_runtime || log "WARN: no se pudo restaurar tmux/Codex en este intento"
  ensure_viewer || log "WARN: no se pudo abrir/verificar Kitty en este intento"
  sleep "$INTERVAL" &
  wait $!
done
