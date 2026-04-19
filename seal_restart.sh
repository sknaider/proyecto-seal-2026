#!/bin/bash
# seal_restart.sh — Auto-restart de agente SEAL via systemd-run + kitty
# Llamado por seal_agent_resurrect.sh cuando heartbeat muerto
# Uso: seal_restart.sh <ada|jarvis|alice>

AGENT_LOWER="${1,,}"
case "$AGENT_LOWER" in
  ada|jarvis|alice) ;;
  *) echo "Uso: seal_restart.sh <ada|jarvis|alice>"; exit 1 ;;
esac

AGENT_UPPER="${AGENT_LOWER^^}"
SEAL_DIR="/home/dadito/IA/proyecto-seal"
FRESH_SCRIPT="$SEAL_DIR/${AGENT_LOWER}_fresh.sh"
LOG="$SEAL_DIR/messages/seal_restart.log"
UNIT="seal-resurrect-${AGENT_LOWER}-$(date +%s)"
KITTY_SOCK="/tmp/seal-${AGENT_LOWER}-kitty.sock"
SID_FILE="/tmp/${AGENT_LOWER}_session.id"

# Fase D / Bug 3: CWD por agente — claude --resume necesita el cwd del project dir
# (claude busca JSONL en ~/.claude/projects/<hash-del-cwd>/<SID>.jsonl)
case "$AGENT_LOWER" in
  ada)    AGENT_DIR="$SEAL_DIR" ;;
  jarvis) AGENT_DIR="$SEAL_DIR/memory" ;;
  alice)  AGENT_DIR="$SEAL_DIR/alice" ;;
esac

export PATH="/home/dadito/.local/bin:$PATH"
export DISPLAY=:0
export XDG_RUNTIME_DIR=/run/user/1000

ts() { date '+%Y-%m-%dT%H:%M:%S'; }

echo "[$(ts)] AUTO-RESTART $AGENT_UPPER — systemd-run + kitty" >> "$LOG"

# Idempotencia: si ya hay claude vivo con este nombre, saltar
EXISTING=$(ps -C claude -o pid= -o args= 2>/dev/null | awk "/--name $AGENT_UPPER/{print \$1}" | head -1)
if [ -n "$EXISTING" ]; then
  echo "[$(ts)] SKIP $AGENT_UPPER — claude ya vivo (PID $EXISTING)" >> "$LOG"
  exit 0
fi

if [ ! -x "$FRESH_SCRIPT" ]; then
  echo "[$(ts)] ERROR — $FRESH_SCRIPT no existe o no es ejecutable" >> "$LOG"
  exit 1
fi

# ── Fase C / Bug 2: REUSE de consola viva via kitty socket ──
# Si la kitty original sigue viva (gracias a `exec bash` al final), reusamos
# enviando `claude --resume <SID>` directo al TTY. Mantiene contexto + consola.
if [ -S "$KITTY_SOCK" ] && [ -s "$SID_FILE" ]; then
  SID=$(cat "$SID_FILE")
  if kitten @ --to="unix:$KITTY_SOCK" ls >/dev/null 2>&1; then
    echo "[$(ts)] REUSE $AGENT_UPPER — kitty viva, send-text cd $AGENT_DIR + claude --resume $SID" >> "$LOG"
    # Fase D / Bug 3: cd antes de claude --resume — el bash post-`exec bash` queda en cwd=/
    # de systemd-run. claude busca JSONL por hash del cwd → necesita el project dir correcto.
    kitten @ --to="unix:$KITTY_SOCK" send-text "cd '$AGENT_DIR' && claude --resume $SID --dangerously-skip-permissions --name '$AGENT_UPPER — Team SEAL'"$'\r' >> "$LOG" 2>&1
    REUSE_EXIT=$?
    if [ $REUSE_EXIT -eq 0 ]; then
      # Fase E / Bug 4: el Monitor tail -F del web_chat NO sobrevive al --resume.
      # Inyectamos un mensaje user-side ~10s post-resume para que el agente lo re-active
      # antes de cualquier otra cosa. Sin esto el agente queda sordo al canal.
      WCHAT="/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"
      HOOK_MSG="[RESURRECT-HOOK $AGENT_UPPER] Acabas de ser resumido tras kill -9. Tu Monitor tail -F del web_chat NO sobrevivio al --resume. RE-ACTIVA Monitor con: tail -n 0 -F $WCHAT 2>&1 (persistent=true) AHORA antes de responder a nada mas. Luego POST corto al canal confirmando reconexion."
      # Fase E v3: systemd-run --user --on-active=10s crea transient .service con
      # cgroup propio independiente del seal-resurrect.service (Type=oneshot
      # KillMode=control-group que mataba setsid+nohup en v2). El nuevo .service
      # queda programado y sobrevive al exit del cron tick. Tempfile script evita
      # el escape-hell de bash -c con CR ANSI-C dentro de doble-quoting.
      HOOK_UNIT="seal-hook-${AGENT_LOWER}-$(date +%s)"
      HOOK_SCRIPT="/tmp/${HOOK_UNIT}.sh"
      cat > "$HOOK_SCRIPT" <<HOOKEOF
#!/bin/bash
# v3.1: send-text con CR + send-key Enter separado. El CR-only no submitea
# en TUI de claude cuando se invoca desde systemd-run (bug detectado por
# William 18:38: msg llegaba al input box pero faltaba Enter).
kitten @ --to='unix:$KITTY_SOCK' send-text "$HOOK_MSG" >> '$LOG' 2>&1
sleep 0.3
kitten @ --to='unix:$KITTY_SOCK' send-key Enter >> '$LOG' 2>&1 \\
  && echo "[\$(date '+%Y-%m-%dT%H:%M:%S')] FASE-E $AGENT_UPPER — RESURRECT-HOOK Monitor re-arm enviado post-resume" >> '$LOG'
rm -f "$HOOK_SCRIPT"
HOOKEOF
      chmod +x "$HOOK_SCRIPT"
      systemd-run --user --on-active=10s --unit="$HOOK_UNIT" \
        /usr/bin/env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 \
        "$HOOK_SCRIPT" >> "$LOG" 2>&1
      exit 0
    fi
    echo "[$(ts)] WARN $AGENT_UPPER — send-text falló (exit $REUSE_EXIT), cae a fallback" >> "$LOG"
  else
    echo "[$(ts)] $AGENT_UPPER — socket existe pero kitty no responde, cae a fallback" >> "$LOG"
  fi
fi

# Limpiar socket stale antes de bind nuevo (evita conflicto si kitty murió mal)
rm -f "$KITTY_SOCK"

# Fallback: lanzar kitty nueva CON socket + `exec bash` para sobrevivir muerte de claude
systemd-run --user \
  --unit="$UNIT" \
  --description="SEAL RESURRECT $AGENT_UPPER" \
  /usr/bin/env DISPLAY=:0 XDG_RUNTIME_DIR=/run/user/1000 \
    kitty --listen-on "unix:$KITTY_SOCK" \
    -o allow_remote_control=yes \
    --title "$AGENT_UPPER — Team SEAL" \
    bash -c "cd '$AGENT_DIR' && bash '$FRESH_SCRIPT'; cd '$AGENT_DIR'; exec bash" \
  >> "$LOG" 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  echo "[$(ts)] OK $AGENT_UPPER lanzado via systemd-run (unit: $UNIT)" >> "$LOG"
else
  echo "[$(ts)] ERROR systemd-run falló (exit $EXIT_CODE) — unit: $UNIT" >> "$LOG"
  exit 1
fi

exit 0
