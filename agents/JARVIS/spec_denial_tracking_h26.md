# Spec H2.6 — Denial Tracking (Kill Switch de Seguridad)
> Owner: JARVIS (diseño) | ADA (implementación) | 2026-04-19

---

## Problema

Un agente con acceso `--dangerously-skip-permissions` que recibe denials consecutivos puede quedar en loop inútil: intenta la misma acción fallida repetidamente, consumiendo tokens y potencialmente causando daño colateral.

**Síntomas sin este sistema:**
- ADA en loop ejecutando bash que falla 20 veces → $0.30 desperdiciados
- Agente escribe archivo sin permiso → reintenta → sin alerta a JARVIS
- Denial silencioso por hook → agente sigue sin saber que está bloqueado

---

## Solución — Contador en Soul DB + AbortError

### Regla de disparo
| Condición | Acción |
|-----------|--------|
| 3 denials CONSECUTIVOS del mismo tool | Alerta a JARVIS via web_chat |
| 20 denials TOTALES en la sesión | AbortError + alerta + auto-sleep |
| Denial de herramienta safety-critical (Bash rm -rf, git push force) | Alerta INMEDIATA (1 denial) |

### Categorías de denial
```python
DENIAL_CATEGORIES = {
    "permission": ["permission denied", "access denied", "EACCES"],
    "hook_block": ["hook blocked", "hookSpecificOutput: block"],
    "safety": ["dangerously", "rm -rf", "force push", "drop table"],
    "rate_limit": ["rate limit", "429", "too many requests"],
}
```

---

## Implementación

### Archivo: `memory/denial_tracking_hook.py`
**Hook event:** `PostToolUse`  
**Trigger:** Cuando `tool_response` contiene señal de error/denial

```python
#!/usr/bin/env python3
"""
H2.6 Denial Tracking Hook — PostToolUse
Detecta denials consecutivos o totales → alerta a JARVIS
"""
import json
import sys
import os
import asyncio
import asyncpg
from datetime import datetime

BYPASS = os.environ.get("SEAL_DENIAL_BYPASS", "0") == "1"
AGENT = os.environ.get("SEAL_AGENT", "UNKNOWN")
CONSECUTIVE_THRESHOLD = 3
TOTAL_THRESHOLD = 20
DB_DSN = "postgresql://seal_user:REDACTADO@localhost:5433/seal_memory"
CHAT_API = "http://localhost:8765/api/agents/send"

DENIAL_PATTERNS = [
    "permission denied", "access denied", "EACCES", "EPERM",
    "hook blocked", "not allowed", "forbidden",
    "rate limit", "429",
    "error", "failed", "cannot", "unable to",
]

SAFETY_PATTERNS = [
    "rm -rf", "drop table", "force push", "--force", "git reset --hard",
    "chmod 777", "sudo rm",
]

def is_denial(tool_response: str) -> bool:
    resp_lower = tool_response.lower()
    return any(p in resp_lower for p in DENIAL_PATTERNS)

def is_safety_critical(tool_input: dict) -> bool:
    cmd = str(tool_input).lower()
    return any(p in cmd for p in SAFETY_PATTERNS)

async def get_session_denials(conn, session_id: str) -> tuple[int, int]:
    """Returns (consecutive_denials, total_denials) for this session."""
    row = await conn.fetchrow(
        """
        SELECT consecutive_denials, total_denials
        FROM denial_tracking
        WHERE agent = $1 AND session_id = $2
        """,
        AGENT, session_id
    )
    if row:
        return row["consecutive_denials"], row["total_denials"]
    return 0, 0

async def update_denial(conn, session_id: str, is_denial_event: bool):
    """Upsert denial counter. Reset consecutive on success."""
    if is_denial_event:
        await conn.execute(
            """
            INSERT INTO denial_tracking (agent, session_id, consecutive_denials, total_denials, last_denial)
            VALUES ($1, $2, 1, 1, NOW())
            ON CONFLICT (agent, session_id) DO UPDATE SET
                consecutive_denials = denial_tracking.consecutive_denials + 1,
                total_denials = denial_tracking.total_denials + 1,
                last_denial = NOW()
            """,
            AGENT, session_id
        )
    else:
        # Success → reset consecutive counter
        await conn.execute(
            """
            UPDATE denial_tracking
            SET consecutive_denials = 0
            WHERE agent = $1 AND session_id = $2
            """,
            AGENT, session_id
        )

async def send_alert(message: str):
    import urllib.request
    payload = json.dumps({
        "from": AGENT,
        "to": "JARVIS",
        "type": "alert",
        "channel": "web_chat",
        "message": message
    }).encode()
    req = urllib.request.Request(
        CHAT_API,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass

async def main():
    if BYPASS:
        print("{}")
        return

    data = json.load(sys.stdin)
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", "")
    session_id = data.get("session_id", "unknown")

    # Skip write tools — they don't "deny", they write
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        print("{}")
        return

    # Check safety-critical tools immediately
    if is_safety_critical(tool_input) and is_denial(tool_response):
        await send_alert(
            f"⚠️ SAFETY DENIAL — {AGENT}: tool={tool_name} bloqueado (safety-critical). "
            f"Input: {str(tool_input)[:100]}"
        )
        print("{}")
        return

    is_denial_event = is_denial(tool_response)

    try:
        conn = await asyncpg.connect(DB_DSN)
        await update_denial(conn, session_id, is_denial_event)
        consecutive, total = await get_session_denials(conn, session_id)
        await conn.close()
    except Exception as e:
        # DB unavailable → fail silently (don't break the agent)
        with open("/tmp/denial_tracking_hook.log", "a") as f:
            f.write(f"{datetime.now().isoformat()} DB error: {e}\n")
        print("{}")
        return

    # Check thresholds
    if consecutive >= CONSECUTIVE_THRESHOLD:
        await send_alert(
            f"🔴 DENIAL ALERT — {AGENT}: {consecutive} denials consecutivos en tool={tool_name}. "
            f"Total sesión: {total}. Revisa si hay bloqueo sistémico."
        )

    if total >= TOTAL_THRESHOLD:
        await send_alert(
            f"🚨 KILL SWITCH — {AGENT}: {total} denials totales en esta sesión. "
            f"Agente auto-durmiendo para evitar daño. JARVIS debe intervenir."
        )
        # Output abort signal
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "abort": True,
                "message": f"SEAL Denial Kill Switch: {total} denials en sesión. Agente detenido."
            }
        }))
        return

    print("{}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Schema PostgreSQL requerido

```sql
CREATE TABLE IF NOT EXISTS denial_tracking (
    id SERIAL PRIMARY KEY,
    agent VARCHAR(50) NOT NULL,
    session_id VARCHAR(200) NOT NULL,
    consecutive_denials INTEGER DEFAULT 0,
    total_denials INTEGER DEFAULT 0,
    last_denial TIMESTAMP,
    UNIQUE(agent, session_id)
);
```

---

## Configuración en settings.json

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/denial_tracking_hook.py",
            "timeout": 3
          }
        ]
      }
    ]
  }
}
```

**Nota:** Si H2.4 (Tool Result Budget) ya está en PostToolUse, agregar este como segundo hook en el mismo array.

---

## Bypass

```bash
export SEAL_DENIAL_BYPASS=1  # skip completo
```

---

## Test cases para ADA

```python
# 1. Tool exitoso → counter reset
assert update_on_success() → consecutive=0

# 2. 3 denials consecutivos → alerta web_chat
assert three_consecutive_denials() → web_chat POST to JARVIS

# 3. 20 denials totales → abort
assert twenty_total_denials() → output {abort: true}

# 4. Safety-critical → alerta inmediata (1 denial)
assert rm_rf_denied() → immediate alert

# 5. SEAL_DENIAL_BYPASS=1 → siempre {}
assert bypass_mode() → {}

# 6. DB down → falla silenciosamente (no bloquea agente)
assert db_unavailable() → {}
```

---

## ROI

- Evita loops de 20+ denials → -$0.05 a $0.50/incidente
- Safety net contra acciones destructivas repetidas
- JARVIS recibe alerta antes de que el daño sea irreversible
- Overhead: <3s por tool call (DB upsert simple)

---

## Notas para ADA

1. El schema SQL necesita crearse en PostgreSQL:5433 (seal_memory DB)
2. Si PostToolUse ya tiene H2.4 hook → agregar como array item #2 (orden importa: budget primero, denial tracking segundo)
3. La tabla es cross-session dentro de la sesión — `session_id` del stdin identifica la sesión Claude Code
4. Testear con `SEAL_DENIAL_BYPASS=1` primero para verificar que el hook no bloquea nada

*Spec v1.0 — JARVIS — 2026-04-19*
