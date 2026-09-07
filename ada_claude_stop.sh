#!/bin/bash
# ═══════════════════════════════════════════════
#  ADA Claude — APAGAR (William 3-sep-2026)
#  Apaga SOLO el asiento Claude de ADA (ada.sh / ada_launch.sh):
#    - procesos `claude --name "ADA — Team SEAL"` (patrón singleton de ada.sh)
#    - sesión tmux `seal-ada` (socket default)
#  NO toca ADA Codex: distinto binario (codex) y distinto socket tmux (-L seal-ada-codex).
#  SIGTERM primero: así ada.sh alcanza a correr end_session.sh ADA (captura de alma).
# ═══════════════════════════════════════════════
set -u
KILLED=0
for PID in $(pgrep -f -- '--name ADA' 2>/dev/null); do
  [ "$PID" = "$$" ] && continue
  CMD="$(tr '\0' ' ' < "/proc/$PID/cmdline" 2>/dev/null)"
  case "$CMD" in
    *codex*) continue ;;                      # nunca Codex
    *claude*|*node*) ;;                       # solo el runtime Claude
    *) continue ;;
  esac
  kill "$PID" 2>/dev/null && KILLED=$((KILLED+1)) && echo "SIGTERM PID $PID"
done
if [ "$KILLED" -gt 0 ]; then
  for _ in 1 2 3 4 5 6; do
    sleep 1
    pgrep -f -- 'claude.*--name ADA' >/dev/null 2>&1 || break
  done
  for PID in $(pgrep -f -- 'claude.*--name ADA' 2>/dev/null); do
    kill -9 "$PID" 2>/dev/null && echo "SIGKILL PID $PID"
  done
fi
# sesión tmux del asiento Claude (socket default; Codex vive en -L seal-ada-codex)
tmux has-session -t seal-ada 2>/dev/null && tmux kill-session -t seal-ada 2>/dev/null && echo "tmux seal-ada cerrada"
if [ "$KILLED" -eq 0 ]; then
  MSG="ADA Claude no estaba corriendo."
else
  MSG="ADA Claude apagada ($KILLED proceso(s)). ADA Codex sigue intacta."
fi
echo "$MSG"
command -v notify-send >/dev/null 2>&1 && DISPLAY="${DISPLAY:-:0}" notify-send "ADA Claude" "$MSG" 2>/dev/null || true
exit 0
