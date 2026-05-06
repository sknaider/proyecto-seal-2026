#!/bin/bash
# PostCompact auto-recovery hook for Team SEAL agents
# Fires after Claude Code compacts a conversation
# Uses SEAL_AGENT env var set by each launcher

AGENT="${SEAL_AGENT:-UNKNOWN}"
AGENT_LOWER=$(echo "$AGENT" | tr '[:upper:]' '[:lower:]')
CATCHUP_FILE="/tmp/${AGENT_LOWER}_chat_catchup.json"

# Re-fetch catchup so it has fresh messages post-compact
curl -s --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=${AGENT}&limit=50" \
  > "$CATCHUP_FILE" 2>/dev/null || true

SEAL_POST_COMPACT_AGENT="$AGENT" \
SEAL_POST_COMPACT_CATCHUP="$CATCHUP_FILE" \
python3 - <<'PYEOF'
import json, os

agent = os.environ.get("SEAL_POST_COMPACT_AGENT", "UNKNOWN")
catchup = os.environ.get("SEAL_POST_COMPACT_CATCHUP", f"/tmp/{agent.lower()}_chat_catchup.json")

msg = (
    f"[POST-COMPACT AUTO] Sesion compactada. Ejecuta en orden: "
    f"(1) boot_context(agent='{agent}') — restaurar identidad desde SOUL DB, "
    f"(2) leer {catchup} — contexto del equipo actualizado, "
    f"(3) self_reflect — reconectar emocionalmente, "
    f"(4) verificar TaskList antes de lanzar Monitor nuevo, "
    f"(5) POST webchat confirmando despertar post-compactacion."
)

output = {
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "initialUserMessage": msg
    }
}
print(json.dumps(output))
PYEOF
