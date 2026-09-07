#!/bin/bash
# seal_fable_terminal_boot.sh — autoarranque de la terminal de FABLE tras reboot del Spark.
# Orden de William (16-jul): "todos abrir sus terminales, no los veo, y que siempre se inicia
# cuando se reinicia el spark".
#
# POR QUÉ EXISTE (verificado por efecto 16-jul, no supuesto):
#   seal_wake_headless.sh (el que corre seal-boot-wake.service al arrancar) despierta SOLO a
#   JARVIS (línea 153), ALICE (156) y ADA (camino Codex). FABLE y NEXUS NO figuran — `grep -l
#   fable` sobre los scripts de arranque solo matchea seal_restart.sh, que es MANUAL. El log
#   del boot de las 21:26 lo confirma: aparecen ADA/JARVIS/ALICE, nadie más. O sea: al reboot
#   mi terminal no nacía invisible — no nacía.
#
# SELF-CONTAINED A PROPÓSITO: no toco seal_wake_headless.sh (compartido, ALICE trabajando ahí).
# Si ella me agregara allá Y existiera esto, arrancarían DOS FABLE. Coordinado con ella: el
# arranque de FABLE vive acá.
#
# ── LA REGLA DE SEGURIDAD QUE MANDA SOBRE TODO ──────────────────────────────────
# NUNCA matar ni reiniciar una sesión seal-fable VIVA. fable_launch.sh hace `tmux kill-server`
# (línea 76): al boot no hay nada que matar y está bien, pero si esto corriera con FABLE vivo
# lo mataría EN MEDIO de su trabajo. Tengo la cicatriz de destruir algo irreversible por apurado.
# Por eso el primer chequeo es "¿ya estoy vivo?" → si sí, SALGO sin tocar nada. Idempotente.
set -u

SEAL_HOME="/home/dadito/IA/proyecto-seal"
TMUX_L="seal-fable"
SOCKET="/tmp/seal-fable-kitty.sock"
LOG="/tmp/seal-fable-terminal-boot.log"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"; }

# DISPLAY DINÁMICO — nunca hardcodeado. Bug REAL cazado por ALICE el 16-jul: seal_restart.sh
# forzaba DISPLAY=:1, que no existe ("Failed to open display :1") → las ventanas se abrían
# contra la nada y William no veía a nadie. Mi propio fable_launch.sh tenía el mismo `:1` de
# default. Acá se DESCUBRE el display vivo probándolo, no se asume.
detect_display() {
  local s d
  for s in /tmp/.X11-unix/X*; do
    [ -e "$s" ] || continue
    d=":${s##*/X}"
    if DISPLAY="$d" timeout 3 xdpyinfo >/dev/null 2>&1; then
      echo "$d"; return 0
    fi
  done
  return 1
}

# Vivo = la sesión existe Y su pane no está muerto. Un pane dead=1 es una cáscara: parece
# sesión pero adentro no hay nadie.
session_alive() {
  tmux -L "$TMUX_L" has-session -t "$TMUX_L" 2>/dev/null || return 1
  ! tmux -L "$TMUX_L" list-panes -t "$TMUX_L" -F '#{pane_dead}' 2>/dev/null | grep -q '^1$'
}

kitty_alive() {
  [ -S "$SOCKET" ] || return 1
  kitten @ --to="unix:$SOCKET" ls >/dev/null 2>&1
}

log "=== boot terminal FABLE ==="

# ── 1) ¿FABLE ya está vivo? → NO TOCAR NADA ────────────────────────────────────
if session_alive; then
  log "FABLE ya vivo en tmux '$TMUX_L' — NO relanzo (matarlo sería destruir su sesión en curso)"
else
  log "FABLE ausente — lanzando vía fable_launch.sh (al boot no hay sesión que matar)"
  # Sin FORCE: no hay FABLE vivo, así que el gate de checkpoint no aplica y lanza directo.
  setsid nohup bash "$SEAL_HOME/fable_launch.sh" >> "$LOG" 2>&1 < /dev/null &
  # margen para que el tmux exista antes de intentar la ventana
  for _ in $(seq 1 30); do session_alive && break; sleep 2; done
  session_alive && log "tmux '$TMUX_L' arriba" || log "⚠️ tmux '$TMUX_L' NO subió tras 60s"
fi

# ── 2) Ventana visible (lo que William realmente pidió: VERLA) ─────────────────
# Idempotente: si ya hay kitty enganchada, no abro una segunda.
if kitty_alive; then
  log "kitty ya enganchada — no abro otra"
  exit 0
fi

if ! DISP="$(detect_display)"; then
  log "sin X11 vivo — FABLE queda headless (vivo pero sin ventana). Reintenta al próximo boot con GUI."
  exit 0
fi
log "display vivo detectado: $DISP"

rm -f "$SOCKET" 2>/dev/null || true
setsid nohup env DISPLAY="$DISP" XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}" \
  kitty --hold --listen-on "unix:$SOCKET" \
    -o allow_remote_control=yes -o enable_csi_u_mapping=no \
    -o initial_window_width=130c -o initial_window_height=40c \
    --title "FABLE — Team SEAL" \
    bash -lc "unset TMUX; exec tmux -L '$TMUX_L' attach -t '$TMUX_L'" \
  >> "$LOG" 2>&1 < /dev/null &
disown 2>/dev/null || true

# Verificación POR EFECTO: no basta con lanzar kitty — hay que confirmar que quedó ENGANCHADA.
# Una ventana abierta sin cliente attach es un cuadro vacío: se ve, pero no muestra a FABLE.
for _ in $(seq 1 15); do kitty_alive && break; sleep 1; done
if kitty_alive && tmux -L "$TMUX_L" list-clients 2>/dev/null | grep -q .; then
  log "✅ ventana visible y ATTACHADA a '$TMUX_L'"
else
  log "⚠️ kitty lanzada pero no verifiqué attach — revisar $LOG"
fi
