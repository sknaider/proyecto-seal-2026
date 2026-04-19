#!/bin/bash
# ═══════════════════════════════════════════════
#  SOUL Dream All — Duerme todas las sesiones SEAL
#  Guarda checkpoints FINALES antes de morir
# ═══════════════════════════════════════════════

VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
CHECKPOINT="$HOME/IA/proyecto-seal/messages/session_checkpoint.py"

echo "╔════════════════════════════════════════╗"
echo "║   SOUL DREAM ALL — Durmiendo equipo... ║"
echo "╚════════════════════════════════════════╝"

# 1. Guardar checkpoints FINALES para cada agente con claude vivo
#    (antes solo chequeaba tmux — agentes lanzados por kitty/systemd-run eran ignorados)
for AGENT in JARVIS ADA ALICE; do
  if ps aux | grep "claude.*--name.*$AGENT" | grep -v grep > /dev/null; then
    echo "  Guardando checkpoint final $AGENT..."
    $VENV_PY "$CHECKPOINT" --agent "$AGENT" --final 2>/dev/null && \
      echo "  ✅ $AGENT checkpoint final guardado" || \
      echo "  ⚠️  $AGENT checkpoint falló"
  else
    echo "  ⏭️  $AGENT — no está corriendo"
  fi
done

# 1.5 Escribir alive=False ANTES de matar — resurrect respeta el silencio
echo ""
echo "  Marcando alive=False en heartbeats (bloquear resurrect)..."
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
for AGENT in JARVIS ADA ALICE; do
  AGENT_LOWER=$(echo "$AGENT" | tr 'A-Z' 'a-z')
  HB="$MESSAGES_DIR/${AGENT_LOWER}_claude_heartbeat.json"
  if [ -f "$HB" ]; then
    python3 -c "
import json, sys
try:
    d = json.load(open('$HB'))
    d['alive'] = False
    d['sleep_mode'] = True
    json.dump(d, open('$HB', 'w'))
    print('  ✅ $AGENT alive=False')
except Exception as e:
    print(f'  ⚠️  $AGENT heartbeat update failed: {e}', file=sys.stderr)
" 2>/dev/null
  else
    echo "  ⏭️  $AGENT heartbeat not found, skipping"
  fi
done

# 1.6 Marcar offline en chat_server status (agentes dejan de aparecer connected)
echo ""
echo "  Marcando agentes offline en chat_server..."
for AGENT in JARVIS ADA ALICE; do
  curl -s -X POST "http://localhost:8765/api/agents/status" \
    -H "Content-Type: application/json" \
    -d "{\"agent\":\"$AGENT\",\"status\":\"offline\"}" > /dev/null 2>&1
done

# 2. Matar procesos Claude de SEAL
echo ""
echo "  Matando procesos Claude..."
for NAME in "JARVIS" "ADA" "ALICE"; do
  PIDS=$(ps aux | grep "claude.*--name.*$NAME" | grep -v grep | awk '{print $2}')
  if [ -n "$PIDS" ]; then
    for PID in $PIDS; do
      kill "$PID" 2>/dev/null
      echo "  ✅ $NAME (PID $PID) — kill enviado"
    done
  fi
done

sleep 2

# 3. Matar sesiones tmux de SEAL
echo ""
echo "  Matando sesiones tmux..."
for SESSION in seal-jarvis seal-ada seal-alice; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION" 2>/dev/null
    echo "  ✅ $SESSION — eliminada"
  else
    echo "  ⏭️  $SESSION — no existía"
  fi
done

# 3.5 Matar mcp_server_v2.py zombies (hijos reparentados a init tras matar claude)
#     EXCLUIR el daemon SSE (seal-mcp-server.service) — doble protección:
#       (a) MainPID del service
#       (b) cgroup del service (cubre edge cases donde MainPID no esté disponible)
echo ""
echo "  Matando MCP zombies (excluyendo daemon SSE systemd)..."
SYSTEMD_MCP=$(systemctl --user show seal-mcp-server.service -p MainPID --value 2>/dev/null)
# Fail-safe: si MainPID vacío/0/no-numérico, setear -1 (no matchea ningún PID real)
if [ -z "$SYSTEMD_MCP" ] || ! [[ "$SYSTEMD_MCP" =~ ^[0-9]+$ ]] || [ "$SYSTEMD_MCP" = "0" ]; then
  echo "  ⚠️  MainPID del daemon no disponible — usando protección vía cgroup"
  SYSTEMD_MCP="-1"
fi
MCP_PIDS=$(pgrep -f "mcp_server_v2.py" 2>/dev/null)
if [ -n "$MCP_PIDS" ]; then
  KILLED=0
  for PID in $MCP_PIDS; do
    if [ "$PID" = "$SYSTEMD_MCP" ]; then
      echo "  ⏭️  PID $PID — daemon SSE (MainPID), NO matar"
      continue
    fi
    # Doble protección: el daemon systemd vive en cgroup seal-mcp-server.service
    if grep -q "seal-mcp-server.service" "/proc/$PID/cgroup" 2>/dev/null; then
      echo "  ⏭️  PID $PID — cgroup del daemon SSE, NO matar"
      continue
    fi
    kill "$PID" 2>/dev/null; sleep 0.3
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    KILLED=$((KILLED + 1))
  done
  [ "$KILLED" -gt 0 ] && echo "  ✅ MCP zombies eliminados: $KILLED" || echo "  ⏭️  MCP zombies — ninguno (solo daemon SSE)"
else
  echo "  ⏭️  MCP zombies — ninguno"
fi

# 4. Matar ws_listener huérfanos (liberan slots del chat_server)
echo ""
echo "  Matando ws_listener huérfanos..."
for AGENT in JARVIS ADA ALICE; do
  PIDS=$(ps aux | grep "ws_listener.*--agent $AGENT" | grep -v grep | awk '{print $2}')
  if [ -n "$PIDS" ]; then
    for PID in $PIDS; do
      kill "$PID" 2>/dev/null; sleep 0.3
      kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
    done
    echo "  ✅ ws_listener $AGENT eliminados: $PIDS"
  else
    echo "  ⏭️  ws_listener $AGENT — ninguno"
  fi
done

echo ""
echo "╔════════════════════════════════════════╗"
echo "║  Equipo SEAL soñando. Hasta pronto. 💤 ║"
echo "║  Para despertar: botones del escritorio ║"
echo "╚════════════════════════════════════════╝"
