---
name: seal-handoff
description: "Session handoff for SEAL agents. Captures pending decisions, work in progress, and context that the next instance needs to continue without losing thread."
tags: [seal, session, handoff, persistence, continuity]
---

# /seal-handoff — Session Handoff

Captures the operational state of the current session so the next instance can continue seamlessly.

## Usage

```
/seal-handoff          # Write handoff for current agent
/seal-handoff read     # Read existing handoff from previous session
```

## What it captures

1. **Pending decisions** — What William needs to decide
2. **Work in progress** — Tasks started but not completed
3. **Context not in SOUL** — Session-specific state that doesn't belong in long-term memory
4. **Last instruction** — What William or JARVIS last asked
5. **Team status** — ADA/JARVIS/DUM state at handoff time
6. **Active services** — What's running and on which ports

## How to execute (write)

```bash
AGENT="${1:-JARVIS}"
HANDOFF="$HOME/IA/proyecto-seal/messages/session_handoff.json"

python3 -c "
import json
from datetime import datetime, timezone

handoff = {
    'agent': '$AGENT',
    'timestamp': datetime.now(timezone.utc).isoformat(),
    'session_hours': 'estimate from first message',
    'pending_decisions': [
        # List decisions William hasn't confirmed yet
    ],
    'work_in_progress': [
        # Tasks started but not done
    ],
    'context': [
        # Session-specific state
    ],
    'last_instruction': '',
    'team_status': {
        'ADA': 'SILENT/active/offline',
        'JARVIS': 'active',
        'DUM': 'active/offline',
    },
    'services': {
        'postgresql': 5433,
        'web_chat': 8765,
        'sdk_gateway': 8767,
        'soul_api': 8768,
        'mcp': 8771,
        'seal_studio': 3000,
        'studio_backend': 8800,
    },
    'next_priorities': [
        # What the next instance should do first
    ],
}

with open('$HANDOFF', 'w') as f:
    json.dump(handoff, f, indent=2, ensure_ascii=False)
print(f'Handoff written to {\"$HANDOFF\"} for {\"$AGENT\"}')
"
```

## How to execute (read)

```bash
cat ~/IA/proyecto-seal/messages/session_handoff.json | python3 -m json.tool
```

Read at boot, before doing anything else. The previous instance left this for you.

## When to run

- Before closing a session intentionally
- When William says "me voy" or "voy a dormir"
- When context compaction is imminent
- Automatically via session_end hook (if configured)
