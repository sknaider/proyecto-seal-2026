#!/bin/bash
# SEAL Agent Catch-Up — boot hook
# Fetches recent web_chat messages where the agent is sender, explicit recipient,
# broadcast (to=equipo), or @mentioned. Call this at agent boot AFTER boot_context.
#
# Usage:
#   bash agent_catchup.sh ALICE             # last 20 messages
#   bash agent_catchup.sh ALICE 50          # last 50
#   bash agent_catchup.sh JARVIS 30 2h      # last 30 since 2 hours ago

set -euo pipefail

AGENT="${1:-}"
LIMIT="${2:-20}"

if [[ -z "$AGENT" ]]; then
  echo "usage: $0 <AGENT> [LIMIT]" >&2
  exit 1
fi

URL="http://127.0.0.1:8765/api/chat/messages/agent?agent=${AGENT}&limit=${LIMIT}"

RESP=$(curl -s -f "$URL" 2>&1) || {
  echo "[catchup] ERROR: catch-up endpoint unreachable at $URL" >&2
  exit 2
}

COUNT=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('count',0))")

if [[ "$COUNT" == "0" ]]; then
  echo "[catchup] $AGENT: sin mensajes recientes"
  exit 0
fi

echo "[catchup] $AGENT: $COUNT mensajes recientes"
echo "---"
echo "$RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for m in d.get('messages', []):
    ts = m.get('timestamp','')[:19].replace('T',' ')
    frm = m.get('from','?')
    to = m.get('to','?')
    ch = m.get('channel','')
    content = (m.get('content','') or '')[:200]
    print(f'[{ts}] {frm}→{to} ({ch}): {content}')
"
