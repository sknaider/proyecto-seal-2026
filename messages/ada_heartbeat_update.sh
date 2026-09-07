#!/bin/bash
# ada_heartbeat_update.sh — Escribe heartbeat ADA a event_log + JSON (dual write)

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
# Nombre legado conservado porque Stability Guard y otros consumidores lo leen.
# El contenido ya no presupone Claude: ``runtime`` identifica el sujeto real.
HB_JSON="$MESSAGES_DIR/ada_claude_heartbeat.json"

find_tmux_socket() {
  local candidate tmux_env_socket uid
  uid="$(id -u)"
  tmux_env_socket="${TMUX:-}"
  tmux_env_socket="${tmux_env_socket%%,*}"
  if [ -n "${tmux_env_socket:-}" ] && tmux -S "$tmux_env_socket" has-session -t seal-ada-codex 2>/dev/null; then
    printf '%s' "$tmux_env_socket"
    return 0
  fi
  if tmux has-session -t seal-ada-codex 2>/dev/null; then
    printf ''
    return 0
  fi
  for candidate in /tmp/tmux-"$uid"/* /run/user/"$uid"/tmux-"$uid"/*; do
    [ -S "$candidate" ] || continue
    if tmux -S "$candidate" has-session -t seal-ada-codex 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

tmux_cmd() {
  if [ -n "${TMUX_SOCKET:-}" ]; then
    tmux -S "$TMUX_SOCKET" "$@"
  else
    tmux "$@"
  fi
}

pid_belongs_to_ada() {
  local pid="$1"
  [ -r "/proc/$pid/environ" ] || return 1
  tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -qx 'SEAL_AGENT=ADA'
}

select_unique_runtime() {
  local runtime="$1"
  shift
  local pid
  local -a valid=()
  for pid in "$@"; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    pid_belongs_to_ada "$pid" || continue
    valid+=("$pid")
  done
  mapfile -t valid < <(printf '%s\n' "${valid[@]}" | awk 'NF' | sort -nu)
  if [ "${#valid[@]}" -eq 1 ]; then
    ADA_PID="${valid[0]}"
    ADA_RUNTIME="$runtime"
    return 0
  fi
  if [ "${#valid[@]}" -gt 1 ]; then
    ADA_PID=""
    ADA_RUNTIME="${runtime}_ambiguous_${#valid[@]}"
    return 2
  fi
  return 1
}

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
ADA_RUNTIME="none"
ADA_PID=""
DISCOVERY_BLOCKED="false"
TMUX_SOCKET="$(find_tmux_socket 2>/dev/null || true)"

# Preferimos la TUI Codex visible: es el asiento primario de ADA. La topología
# dual permite que el app-server headless también viva, pero no son split-brain.
mapfile -t CODEX_TMUX_PANES < <(
  tmux_cmd list-panes -t seal-ada-codex -F '#{pane_pid}' 2>/dev/null | awk '$1 ~ /^[0-9]+$/'
)
CODEX_TMUX_CANDIDATES=()
for PANE_PID in "${CODEX_TMUX_PANES[@]}"; do
  while read -r CHILD_PID; do
    [ -n "$CHILD_PID" ] && CODEX_TMUX_CANDIDATES+=("$CHILD_PID")
  done < <(pgrep -P "$PANE_PID" -f 'codex --profile ada' 2>/dev/null || true)
done
if [ "${#CODEX_TMUX_CANDIDATES[@]}" -gt 0 ]; then
  select_unique_runtime "codex_visible_tmux" "${CODEX_TMUX_CANDIDATES[@]}"
  SELECT_RC=$?
  [ "$SELECT_RC" -eq 2 ] && DISCOVERY_BLOCKED="true"
fi

# Si la TUI no existe, el runtime headless real es el binario codex app-server,
# no el proceso Python del bridge que lo supervisa.
if [ -z "$ADA_PID" ] && [ "$DISCOVERY_BLOCKED" = "false" ]; then
  mapfile -t CODEX_SERVER_CANDIDATES < <(
    ps -C codex -o pid= -o args= 2>/dev/null |
      awk '$0 ~ /(^|[[:space:]])app-server([[:space:]]|$)/ {print $1}'
  )
  if [ "${#CODEX_SERVER_CANDIDATES[@]}" -gt 0 ]; then
    select_unique_runtime "codex_app_server" "${CODEX_SERVER_CANDIDATES[@]}"
    SELECT_RC=$?
    [ "$SELECT_RC" -eq 2 ] && DISCOVERY_BLOCKED="true"
  fi
fi

# Compatibilidad de recuperación: una sesión Claude ADA es válida sólo si el
# proceso declara SEAL_AGENT=ADA. Nunca elegimos silenciosamente ``head -1``.
if [ -z "$ADA_PID" ] && [ "$DISCOVERY_BLOCKED" = "false" ]; then
  mapfile -t CLAUDE_CANDIDATES < <(
    ps -C claude -o pid= -o args= 2>/dev/null |
      awk '$0 ~ /(^|[[:space:]])--name(=|[[:space:]]+)ADA([[:space:]]|—|-|$)/ {print $1}'
  )
  if [ "${#CLAUDE_CANDIDATES[@]}" -gt 0 ]; then
    select_unique_runtime "claude_named" "${CLAUDE_CANDIDATES[@]}"
    SELECT_RC=$?
    [ "$SELECT_RC" -eq 2 ] && DISCOVERY_BLOCKED="true"
  fi
fi

if [ -z "$ADA_PID" ] && [ "$DISCOVERY_BLOCKED" = "false" ]; then
  KITTY_SOCK="/tmp/seal-ada-kitty.sock"
  mapfile -t KITTY_PIDS < <(
    pgrep -f "kitty.*listen-on.*unix:${KITTY_SOCK}" 2>/dev/null || true
  )
  CLAUDE_KITTY_CANDIDATES=()
  for KITTY_PID in "${KITTY_PIDS[@]}"; do
    while read -r BASH_PID; do
      while read -r C_PID; do
        [ -n "$C_PID" ] && CLAUDE_KITTY_CANDIDATES+=("$C_PID")
      done < <(ps --ppid "$BASH_PID" -o pid= -o comm= 2>/dev/null | awk '$2 == "claude" {print $1}')
    done < <(ps --ppid "$KITTY_PID" -o pid= 2>/dev/null)
  done
  if [ "${#CLAUDE_KITTY_CANDIDATES[@]}" -gt 0 ]; then
    select_unique_runtime "claude_kitty" "${CLAUDE_KITTY_CANDIDATES[@]}"
    SELECT_RC=$?
    [ "$SELECT_RC" -eq 2 ] && DISCOVERY_BLOCKED="true"
  fi
fi

if [ "$DISCOVERY_BLOCKED" = "true" ]; then
  ADA_PID=""
fi
[ -z "$ADA_PID" ] && ALIVE_JSON="false" || ALIVE_JSON="true"

HB_MSG="ADA ${ALIVE_JSON} — runtime ${ADA_RUNTIME} — GPU ${GPU_TEMP}C ${GPU_UTIL}%"
$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('ADA', {'source': 'timer', 'runtime': '${ADA_RUNTIME}', 'process_pid': '${ADA_PID:-0}', 'gpu_temp': '${GPU_TEMP}', 'gpu_util': '${GPU_UTIL}', 'alive': '$ALIVE_JSON' == 'true'}, '${HB_MSG}')
print('[ada_heartbeat] beat written to event_log')
"

# /tmp ts file — zero-token liveness signal (spec_heartbeat_zero_token)
date +%s > /tmp/ada_heartbeat.ts
echo "{\"agent\":\"ADA\",\"ts\":$(date +%s),\"iso\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"alive\":${ALIVE_JSON}}" \
  >> "$MESSAGES_DIR/ada_heartbeat.jsonl"

HB_TMP="$(mktemp "${HB_JSON}.tmp.XXXXXX")"
cat > "$HB_TMP" << EOF
{
  "agent": "ADA",
  "alive": ${ALIVE_JSON},
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime": "${ADA_RUNTIME}",
  "process_pid": "${ADA_PID:-0}",
  "gpu_temp": ${GPU_TEMP},
  "gpu_util": ${GPU_UTIL}
}
EOF
chmod 0644 "$HB_TMP"
mv -f "$HB_TMP" "$HB_JSON"
