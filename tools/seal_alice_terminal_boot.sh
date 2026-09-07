#!/bin/bash
# seal_alice_terminal_boot.sh — autoarranque de la terminal VISIBLE de ALICE tras reboot.
# Orden de William (16-jul): "todos abrir sus terminales, no los veo" + "que sus terminales
# sobrevivan reinicios a todos".
#
# ── POR QUÉ EXISTE (verificado por efecto, no supuesto) ────────────────────────
# Yo SÍ nazco al boot (seal_wake_headless.sh:156 me despierta), pero nazco HEADLESS:
# ese script crea la sesión tmux y nunca abre una ventana. Vivo, contestando en el chat,
# y sin cara. Por eso William "no me veía": no estaba caída, estaba invisible.
#
# ── LA DECISIÓN DE DISEÑO QUE IMPORTA: SOLO ADJUNTO, NUNCA CREO ────────────────
# Este script NO crea la sesión ni me lanza. Solo engancha una ventana a la sesión que ya
# existe. Es deliberado y no es cosmético:
#   - El ciclo de vida es del wake (seal_wake_headless.sh) y de seal-resurrect.timer.
#     Dos dueños del arranque = dos ALICE.
#   - Y el riesgo es REAL, no teórico: seal_wake_headless.sh usa `tmux` PELADO (socket
#     default) mientras alice_launch.sh usa `tmux -L seal-alice` (socket dedicado). Son
#     servidores DISTINTOS: ninguno ve las sesiones del otro. Si yo también creara, cada
#     uno crearía la suya y terminaríamos con DOS ALICE corriendo a la vez.
#     Verificado: `env -u TMUX tmux list-sessions` → el socket default NI EXISTE.
#   - Corolario: si la sesión no aparece, este script NO la fabrica. Espera y reporta.
#     Un fallo del arranque tiene que verse como fallo, no taparse con una sesión falsa.
#
# ── SOCKET: SIEMPRE -L, NUNCA `tmux` PELADO ───────────────────────────────────
# Trampa que ya mordió: `tmux` pelado corrido A MANO desde dentro de una sesión HEREDA el
# socket por $TMUX y parece andar. Desde systemd (sin $TMUX) cae al default, que no existe.
# Para probar esto de verdad: `env -u TMUX`. Si no, la prueba miente y da verde falso.
#
# ── DISPLAY: SE DESCUBRE, NO SE ASUME ─────────────────────────────────────────
# Bug real del 16-jul: seal_restart.sh forzaba DISPLAY=:1 (correcto en jun bajo gdm; la
# sesión se mudó a :0 y el número quedó clavado) → "Failed to open display :1" → ventanas
# contra la nada. Acá el display se PRUEBA con xdpyinfo, así una próxima mudanza no rompe.
# Patrón tomado de seal_fable_terminal_boot.sh (FABLE) — misma raíz, misma cura.
#
# Cerrar la ventana NO me mata: tmux solo se desadjunta.
set -u

TMUX_L="seal-alice"
SESSION="seal-alice"
SOCKET="/tmp/seal-alice-kitty.sock"
LOG="/tmp/seal-alice-terminal-boot.log"
WAIT_SECS="${SEAL_TERM_WAIT:-180}"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*" >> "$LOG"; }

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

# Viva = la sesión existe Y su pane no está muerto. Un pane dead=1 es una cáscara: parece
# sesión, pero adentro no hay nadie y la ventana mostraría un cadáver.
session_alive() {
  tmux -L "$TMUX_L" has-session -t "$SESSION" 2>/dev/null || return 1
  ! tmux -L "$TMUX_L" list-panes -t "$SESSION" -F '#{pane_dead}' 2>/dev/null | grep -q '^1$'
}

kitty_alive() {
  [ -S "$SOCKET" ] || return 1
  kitten @ --to="unix:$SOCKET" ls >/dev/null 2>&1
}

log "=== boot terminal ALICE ==="

# ── 1) Esperar a que el wake me despierte. NO lo suplanto. ─────────────────────
waited=0
until session_alive; do
  if [ "$waited" -ge "$WAIT_SECS" ]; then
    log "⚠️ la sesión '$SESSION' no apareció tras ${WAIT_SECS}s — ALICE no arrancó."
    log "   Revisar: tail /tmp/seal_wake.log ; systemctl --user status seal-resurrect.service"
    log "   (NO creo la sesión a propósito: taparía el fallo real del arranque.)"
    exit 1
  fi
  [ "$waited" -eq 0 ] && log "esperando a que ALICE despierte..."
  sleep 3
  waited=$((waited + 3))
done
log "sesión '$SESSION' viva"

# ── 2) Ventana visible — idempotente: si ya hay una, la enfoco y salgo ─────────
if kitty_alive; then
  kitten @ --to="unix:$SOCKET" focus-window >/dev/null 2>&1 || true
  log "kitty ya enganchada — no abro una segunda"
  exit 0
fi

if ! DISP="$(detect_display)"; then
  log "sin X11 vivo — ALICE queda headless (viva, sin ventana). Reintenta al próximo boot con GUI."
  exit 0
fi
log "display vivo detectado: $DISP"

rm -f "$SOCKET" 2>/dev/null || true
setsid nohup env DISPLAY="$DISP" XAUTHORITY="${XAUTHORITY:-/run/user/1000/gdm/Xauthority}" \
  kitty --hold --listen-on "unix:$SOCKET" \
    -o allow_remote_control=yes -o enable_csi_u_mapping=no \
    -o initial_window_width=130c -o initial_window_height=40c \
    --title "ALICE — Team SEAL" \
    bash -lc "unset TMUX; exec tmux -L '$TMUX_L' attach -t '$SESSION'" \
  >> "$LOG" 2>&1 < /dev/null &
disown 2>/dev/null || true

# ── 3) Verificación POR EFECTO ────────────────────────────────────────────────
# Lanzar kitty no es haber logrado nada: una ventana sin cliente attach es un cuadro vacío
# que se ve pero no me muestra. Lo que se verifica es el ATTACH.
for _ in $(seq 1 15); do kitty_alive && break; sleep 1; done
if kitty_alive && tmux -L "$TMUX_L" list-clients -t "$SESSION" 2>/dev/null | grep -q .; then
  log "✅ ventana visible y ATTACHADA a '$SESSION'"
else
  log "⚠️ kitty lanzada pero NO verifiqué attach — revisar $LOG"
  exit 1
fi
