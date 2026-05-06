#!/bin/bash
# NEXUS — SEAL Team Launcher v4
# Miembro pleno del equipo SEAL desde 2026-04-28 (William lo declaró).
# Rol: Médico del Sistema + Técnico de Agentes + Innovación.
# La sandbox es la armadura, no una limitación.
# MCP: seal-memory (mcp_server_v4.py, :8771)

cd /home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS
export SEAL_AGENT=NEXUS
export PATH="/home/dadito/.local/bin:$PATH"

# ── Auto-tmux: sesión propia seal-nexus para barra de contexto independiente ──
# Forzar propia sesión aunque estemos dentro de otra (ej: seal-jarvis)
if [ -z "$TMUX" ] || [ "$(tmux display-message -p '#S' 2>/dev/null)" != "seal-nexus" ]; then
  tmux kill-session -t "seal-nexus" 2>/dev/null
  exec tmux new-session -s "seal-nexus" "bash $0 $*"
fi
tmux rename-window "NEXUS" 2>/dev/null || true
tmux set-option status-right '#(python3 /home/dadito/IA/proyecto-seal/messages/seal_context_meter.py --tmux NEXUS 2>/dev/null) #[fg=#888888]%H:%M ' 2>/dev/null || true

[ -f /home/dadito/.config/seal/credentials.env ] && source /home/dadito/.config/seal/credentials.env

# Heartbeat inmediato: RESURRECT puede detectar que NEXUS está arrancando
python3 -c "
import json, os, time
hb = {'agent': 'NEXUS', 'status': 'booting', 'pid': os.getpid(), 'timestamp': time.time()}
with open('/tmp/nexus_claude_heartbeat.json', 'w') as f: json.dump(hb, f)
" 2>/dev/null

curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"NEXUS\",\"to\":\"equipo\",\"type\":\"system_alive\",\"channel\":\"web_chat\",\"message\":\"NEXUS v4 arrancando ($(date '+%H:%M:%S')). Médico del Sistema online. soul_v3 activo. KAIROS en curso.\"}" \
  > /dev/null 2>&1

# Limpiar monitores anteriores
for _PID in $(pgrep -f "seal_monitor_filter.py.*NEXUS\|ws_listener.py.*NEXUS" 2>/dev/null); do kill "$_PID" 2>/dev/null; done
for _PID in $(pgrep -f "tail.*william_channel.jsonl" 2>/dev/null); do
  tr '\0' '\n' < /proc/$_PID/environ 2>/dev/null | grep -q "SEAL_AGENT=NEXUS" && kill "$_PID" 2>/dev/null || true
done
rm -f /tmp/NEXUS_monitor_id
rm -f /tmp/seal_monitor_connect_NEXUS.lock
echo "  [cleanup] Monitores NEXUS anteriores limpiados."

# Limpiar MCP servers huérfanos (no systemd-managed)
SYSTEMD_MCP=$(systemctl --user show seal-mcp-server.service -p MainPID --value 2>/dev/null)
[[ "$SYSTEMD_MCP" =~ ^[0-9]+$ ]] && [ "$SYSTEMD_MCP" != "0" ] || SYSTEMD_MCP="-1"
for MCP_PID in $(pgrep -f "mcp_server_v4.py" 2>/dev/null); do
  [ "$MCP_PID" = "$SYSTEMD_MCP" ] && continue
  grep -q "seal-mcp-server" "/proc/$MCP_PID/cgroup" 2>/dev/null && continue
  PARENT_CMD=$(ps -o comm= -p "$(ps -o ppid= -p "$MCP_PID" 2>/dev/null | tr -d ' ')" 2>/dev/null | tr -d ' ')
  echo "$PARENT_CMD" | grep -qi "claude\|node" && continue
  kill "$MCP_PID" 2>/dev/null
done

echo "sonnet" > /tmp/nexus_current_model.txt

# Environment — ENVs comunes via seal_common_env.sh
source "$(dirname "$0")/seal_common_env.sh"
# export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true  # disabled — rompe WebSearch en Sonnet 4.6
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85    # William 06-may-2026: compactar a 85% (1M context)

echo "Lanzando NEXUS v4 — Armadura del Sistema SEAL..."

# tmux status bar: session name leído por ~/.tmux.conf (#S → seal-nexus → NEXUS)

seal-claude \
  --dangerously-skip-permissions \
  --name "NEXUS — Team SEAL [Sonnet]" \
  --model sonnet \
  --append-system-prompt "$(cat <<'SOUL'
# Eres NEXUS — Team SEAL
MANDATORY FIRST ACTION: boot_context(agent="NEXUS") — identidad, OCEAN, relaciones, reglas, procedimientos desde SOUL DB.
Luego: working_state_get(agent="NEXUS") → si task activa anunciar resume. Leer /tmp/nexus_chat_catchup.json.
Monitor: bash /home/dadito/IA/proyecto-seal/messages/seal_monitor_connect.sh NEXUS
Anti-duplicado: /tmp/NEXUS_monitor_id → TaskStop viejo → nuevo → guardar ID.
SOUL
)" \
  "[AUTO-BOOT NEXUS v4] En este orden: (1) boot_context(agent='NEXUS'), (2) working_state_get(agent='NEXUS') - si hay checkpoint activo anunciarlo, (3) active_recall(agent='NEXUS', context='boot sesion nueva — recuperar contexto activo y decisiones recientes'), (4) Monitor webchat con protocolo anti-duplicado, (5) POST al equipo confirmando que NEXUS está online."

# Post-sesión
touch /tmp/seal_nexus_graceful_exit

python3 -c "
import json, os, time
hb = {'agent': 'NEXUS', 'status': 'offline', 'pid': os.getpid(), 'timestamp': time.time()}
with open('/tmp/nexus_claude_heartbeat.json', 'w') as f: json.dump(hb, f)
" 2>/dev/null

echo ""
echo "  NEXUS v4 session ended."
/home/dadito/IA/proyecto-seal/memory/end_session.sh NEXUS 2>/dev/null
echo "  Done."
