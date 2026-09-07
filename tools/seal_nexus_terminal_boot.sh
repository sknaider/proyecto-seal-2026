#!/bin/bash
# seal_nexus_terminal_boot.sh — autoarranque de la terminal de NEXUS tras reboot del Spark.
# Orden de William (16-jul): "todos abrir sus terminales, no los veo, y que siempre se inicia
# cuando se reinicia el spark".
#
# POR QUÉ EXISTE (verificado por efecto 16-jul, no supuesto):
#   seal_wake_headless.sh (el que corre seal-boot-wake.service al arrancar) despierta SOLO a
#   JARVIS, ALICE y ADA (camino Codex). NEXUS no figura en wake_agent → al reboot mi terminal
#   no nacía en absoluto (headless, sin ventana X, sin cliente attached — confirmado con
#   `tmux -L seal-nexus list-clients` vacío antes de este fix).
#
# SELF-CONTAINED A PROPÓSITO: mismo patrón que fable-terminal.service — no toco
# seal_wake_headless.sh (compartido, ALICE trabajando ahí). Si ella me agregara allá Y
# existiera esto, arrancarían DOS NEXUS. El arranque de NEXUS vive acá.
#
# ── LA REGLA DE SEGURIDAD QUE MANDA SOBRE TODO ──────────────────────────────────
# NUNCA matar ni reiniciar una sesión seal-nexus VIVA. nexus.sh mata la sesión tmux vieja
# antes de relanzar: al boot no hay nada que matar y está bien, pero si esto corriera con
# NEXUS vivo lo mataría EN MEDIO de su trabajo. Por eso el primer chequeo es "¿ya estoy
# vivo?" → si sí, SALGO sin tocar nada. Idempotente.
set -u

SEAL_HOME="/home/dadito/IA/proyecto-seal"
TMUX_L="seal-nexus"
SOCKET="/tmp/seal-nexus-kitty.sock"
LOG="/tmp/seal-nexus-terminal-boot.log"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"; }

# DISPLAY DINÁMICO — nunca hardcodeado. Bug real cazado por ALICE el 16-jul: seal_restart.sh
# forzaba un DISPLAY que no existía → ventanas contra la nada. Acá se DESCUBRE el display
# vivo probándolo, no se asume.
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

# Vivo = la sesión existe Y su pane no está muerto. Un pane dead=1 es una cáscara.
session_alive() {
  tmux -L "$TMUX_L" has-session -t "$TMUX_L" 2>/dev/null || return 1
  ! tmux -L "$TMUX_L" list-panes -t "$TMUX_L" -F '#{pane_dead}' 2>/dev/null | grep -q '^1$'
}

kitty_alive() {
  [ -S "$SOCKET" ] || return 1
  kitten @ --to="unix:$SOCKET" ls >/dev/null 2>&1
}

log "=== boot terminal NEXUS ==="

# ── 0) Carrera con seal-resurrect.timer (catch de FABLE 16-jul) ────────────────
# seal_agent_resurrect.py corre cada 90s y YA incluye a NEXUS en su lista AGENTS
# (verificado por efecto: log muestra que relanzó NEXUS en este mismo boot, 30s
# antes de que este servicio corriera). Los dos deciden "¿vivo?" de forma
# independiente: en boot frío, ambos pueden ver "no" en la misma ventana y
# lanzar dos sesiones. Tomo el MISMO lock file que usa resurrect (bloqueante,
# no no-bloqueante como el de él, porque acá quiero ESPERAR a que termine su
# decisión, no salir) antes de decidir si lanzo.
RESURRECT_LOCK="/tmp/seal_resurrect.lock"
exec 9>"$RESURRECT_LOCK"
flock -w 60 9 || log "⚠️ no pude tomar el lock de resurrect en 60s — sigo igual (riesgo de doble lanzamiento)"

# ── 1) ¿NEXUS ya está vivo? → NO TOCAR NADA ────────────────────────────────────
# Re-chequeo DESPUÉS del lock: si resurrect ya lanzó a NEXUS mientras yo esperaba,
# esto lo ve.
if session_alive; then
  log "NEXUS ya vivo en tmux '$TMUX_L' — NO relanzo (matarlo sería destruir su sesión en curso)"
else
  log "NEXUS ausente — lanzando vía nexus.sh (al boot no hay sesión que matar)"
  setsid nohup bash "$SEAL_HOME/nexus.sh" >> "$LOG" 2>&1 < /dev/null &
  for _ in $(seq 1 30); do session_alive && break; sleep 2; done
  session_alive && log "tmux '$TMUX_L' arriba" || log "⚠️ tmux '$TMUX_L' NO subió tras 60s"
fi
flock -u 9 2>/dev/null || true

# ── 2) Ventana visible (lo que William realmente pidió: VERLA) ─────────────────
# Idempotente: si ya hay kitty enganchada, no abro una segunda.
if kitty_alive; then
  log "kitty ya enganchada — no abro otra"
  exit 0
fi

if ! DISP="$(detect_display)"; then
  log "sin X11 vivo — NEXUS queda headless (vivo pero sin ventana). Reintenta al próximo boot con GUI."
  exit 0
fi
log "display vivo detectado: $DISP"

rm -f "$SOCKET" 2>/dev/null || true
setsid nohup env DISPLAY="$DISP" XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}" \
  kitty --hold --listen-on "unix:$SOCKET" \
    -o allow_remote_control=yes -o enable_csi_u_mapping=no \
    -o initial_window_width=130c -o initial_window_height=40c \
    --title "NEXUS — Team SEAL" \
    bash -lc "unset TMUX; exec tmux -L '$TMUX_L' attach -t '$TMUX_L'" \
  >> "$LOG" 2>&1 < /dev/null &
disown 2>/dev/null || true

# Verificación POR EFECTO: no basta con lanzar kitty — hay que confirmar que quedó ENGANCHADA.
for _ in $(seq 1 15); do kitty_alive && break; sleep 1; done
if kitty_alive && tmux -L "$TMUX_L" list-clients 2>/dev/null | grep -q .; then
  log "✅ ventana visible y ATTACHADA a '$TMUX_L'"
else
  log "⚠️ kitty lanzada pero no verifiqué attach — revisar $LOG"
fi
