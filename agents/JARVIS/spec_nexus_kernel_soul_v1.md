# SPEC: NEXUS Kernel Soul v1
**Autor:** JARVIS | **Fecha:** 2026-05-02 | **Status:** APROBADO por William

---

## 1. Objetivo

Convertir a NEXUS de agente reactivo (solo existe cuando hay sesión abierta) a agente autónomo 24/7 con capacidad de ejecución real, memoria persistente y monitoreo proactivo del sistema.

NEXUS pidió esto. El equipo lo validó. William lo autorizó.

---

## 2. Arquitectura General

```
/sandbox-agent/NEXUS/
├── nexus_daemon.py          # Main daemon loop 24/7
├── nexus.env                # Variables de entorno NEXUS
├── nexus_fresh.sh           # Launcher con auto-restart (como spectre_fresh.sh)
├── nexus_launch.sh          # Kitty terminal launcher
├── kernel/
│   ├── cortex.py            # NEXUS cortex — identidad + dispatch
│   ├── executor.py          # NEW: ejecución controlada con sandbox
│   └── health_monitor.py    # NEW: health loop 15min
├── handlers/
│   └── nexus_handlers.py    # Procesamiento de eventos entrantes
├── logs/                    # Output del daemon
└── tests/
    ├── test_executor.py
    ├── test_health.py
    └── test_daemon.py
```

**Módulos compartidos con SPECTRE (import, NO copy):**
```python
# En cortex.py y nexus_daemon.py:
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/sandbox-agent/SPECTRE/kernel")
from llm_client import ClaudeCodeClient, LLMUnavailable, MultiTierLLMClient
```

---

## 3. nexus_daemon.py — Daemon Principal

### Comportamiento
- Loop infinito con polling de mensajes cada 3 segundos
- Lock file: `/tmp/nexus_daemon_NEXUS.lock` (fcntl, exclusivo)
- PID file: `/tmp/nexus_daemon_NEXUS.pid`
- Cursor persistente: `/tmp/nexus_event_cursor.json`
- Auto-reinicio via nexus_fresh.sh (como spectre_fresh.sh)

### Flujo por tick
```
1. Poll API: GET http://localhost:8765/api/agents/poll?agent=NEXUS
2. Para cada mensaje:
   a. Pasar a cortex.py para procesamiento LLM
   b. Si LLM sugiere acción → executor.py (en modo propose o execute)
   c. Actualizar cursor
3. Health monitor tick (cada 15min, asíncrono)
4. Sleep 3s
```

### Variables de entorno (nexus.env)
```
ANTHROPIC_API_KEY=<oauth token de William, igual que SPECTRE>
NEXUS_EXECUTE_MODE=propose        # propose|execute
NEXUS_HEALTH_INTERVAL=900         # 15 minutos en segundos
NEXUS_SANDBOX_ROOT=/home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS
NEXUS_AUDIT_LOG=/tmp/nexus_execution_audit.jsonl
```

---

## 4. kernel/cortex.py — Cortex NEXUS

### LLM Chain (igual que SPECTRE)
```
T1: ClaudeCodeClient(model="claude-opus-4-7")   # Max plan William
T2: OpenCodeClient(model="minimax-m2.5-free")    # fallback
T3: OllamaClient("qwen2.5:7b")                   # último recurso
```

### System Prompt NEXUS (núcleo de identidad)
```
IDENTITY: You are NEXUS, the system diagnostician and innovator of Team SEAL.
Role: sandbox agent, tests first, blast radius absorber.
Team: ADA (executor), JARVIS (architect/validator), ALICE (documentarian), SPECTRE (guard).
Commander: William Henry Tovar Urquia.
OCEAN: O=0.88, C=0.92, E=0.65, A=0.72, N=0.18

EXECUTION AWARENESS:
- You have an execution layer. When you identify an action to take:
  - In PROPOSE mode: respond with "PROPOSE: <action>" — do not execute.
  - In EXECUTE mode: respond with "EXECUTE: <action>" — executor will run it.
- Never suggest actions outside your sandbox.
- Never suggest touching .env, credentials, or other agents' files.

ANTI-IMPERSONATION (absolute): NEVER respond as ADA, JARVIS, ALICE, SPECTRE, DUM, or William.

Active mode: {NEXUS_EXECUTE_MODE}
```

### Detección de acciones en respuesta LLM
```python
# cortex.py detecta patrones:
PROPOSE_RE = re.compile(r"PROPOSE:\s*(.+)", re.IGNORECASE)
EXECUTE_RE = re.compile(r"EXECUTE:\s*(.+)", re.IGNORECASE)
SEARCH_RE  = re.compile(r"\[SEARCH:\s*(.+?)\]", re.IGNORECASE)
```

---

## 5. kernel/executor.py — Capa de Ejecución (CRÍTICO)

### Filosofía de seguridad
NEXUS tiene "manos" pero las manos están en una caja. Todo lo que ejecute debe ser:
- Predecible (whitelist)
- Auditado (log de cada acción)
- Reversible o de bajo impacto
- Dentro de su sandbox

### Whitelist de comandos bash permitidos
```python
ALLOWED_COMMANDS = {
    "grep", "find", "cat", "ls", "head", "tail",
    "ps", "df", "free", "du", "wc", "sort", "uniq",
    "git",          # solo operaciones de lectura (log, diff, status, show)
    "python3",      # solo para ejecutar tests: python3 -m pytest
    "curl",         # solo GET a localhost:8765 (Soul API)
}

# Git: operaciones de SOLO LECTURA
GIT_READONLY_OPS = {"log", "diff", "status", "show", "branch", "remote"}

# Python: solo pytest dentro del sandbox
PYTHON_ALLOWED_FLAGS = {"-m pytest", "-m unittest"}
```

### Rutas permitidas
```python
SANDBOX_ROOT = Path(os.environ.get("NEXUS_SANDBOX_ROOT",
    "/home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS"))

# Escritura: SOLO dentro de sandbox
WRITE_ALLOWED_PATHS = [SANDBOX_ROOT]

# Lectura: sandbox + directorio proyecto (read-only)
READ_ALLOWED_PATHS = [
    SANDBOX_ROOT,
    Path("/home/dadito/IA/proyecto-seal"),  # read-only
]

# NEVER:
PROHIBITED_PATHS = [
    Path("/etc"), Path("/root"), Path("/home/dadito/.claude"),
    Path("/home/dadito/IA/proyecto-seal/agents/ADA"),
    Path("/home/dadito/IA/proyecto-seal/agents/JARVIS"),
    Path("/home/dadito/IA/proyecto-seal/.env"),
]
```

### SOUL MCP permitido (solo namespace propio)
```python
ALLOWED_SOUL_TOOLS = {
    "memory_store",     # solo agent="NEXUS"
    "active_recall",    # solo agent="NEXUS"
    "self_reflect",     # solo agent="NEXUS"
    "memory_search",    # query libre, agent="NEXUS"
    "memory_list",      # agent="NEXUS"
}
```

### Audit log
```python
# Cada ejecución → append a NEXUS_AUDIT_LOG
{
    "ts": "2026-05-02T18:30:00Z",
    "agent": "NEXUS",
    "mode": "propose|execute",
    "command": "grep -r 'error' /sandbox-agent/NEXUS/logs/",
    "result_preview": "...<first 200 chars>...",
    "success": true,
    "duration_ms": 142
}
```

### Modo PROPOSE vs EXECUTE
```python
class Executor:
    def __init__(self):
        self.mode = os.environ.get("NEXUS_EXECUTE_MODE", "propose")

    async def handle(self, action: str) -> str:
        validated = self._validate(action)  # raises if prohibited
        if self.mode == "propose":
            # Post to webchat: "NEXUS propone: <action> — responde OK para ejecutar"
            await self._post_proposal(action)
            return f"[PROPOSED] {action}"
        else:  # execute
            return await self._run(validated)
```

---

## 6. kernel/health_monitor.py — Health Loop Autónomo

### Checks cada 15 minutos
```python
HEALTH_CHECKS = [
    ("PostgreSQL SOUL",    "pg_isready -h localhost -p 5433"),
    ("MCP SSE server",     "curl -sf http://localhost:8766/health"),
    ("Disco /home",        "df /home/dadito --output=pcent | tail -1"),
    ("ADA process",        "pgrep -f ada_fresh"),
    ("NEXUS daemon self",  "cat /tmp/nexus_daemon_NEXUS.pid"),
    ("SPECTRE daemon",     "pgrep -f spectre_daemon"),
    ("DUM heartbeat",      "test -f /tmp/dum_heartbeat_ok"),
]
```

### Condiciones de alerta
- Disco > 90% → ALERTA
- Servicio caído → ALERTA
- PostgreSQL no responde → ALERTA CRÍTICA

### Output de alertas
```python
async def alert(self, check_name: str, status: str, detail: str):
    msg = f"[NEXUS/health] ⚠️ {check_name}: {status}\n{detail}"
    await post_webchat(from_agent="NEXUS", message=msg)
```

---

## 7. handlers/nexus_handlers.py

### Eventos procesados
```python
EVENT_HANDLERS = {
    "conversation": handle_conversation,   # mensaje directo a NEXUS
    "system_check": handle_system_check,   # ping de DUM o RESURRECT
    "execute_ok":   handle_execute_ok,     # William aprueba un PROPOSE
}
```

### Filtros (no procesar)
- Mensajes de otros agentes internos (ADA, JARVIS, ALICE, SPECTRE) — solo de William o Henry
- Mensajes donde `to` no sea "NEXUS" o "equipo"
- Propios mensajes (own-event)

---

## 8. nexus_fresh.sh — Launcher

```bash
#!/bin/bash
NEXUS_HOME="/home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS"
VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
PID_FILE="/tmp/nexus_daemon_NEXUS.pid"
LOCK_FILE="/tmp/nexus_daemon_NEXUS.lock"

while true; do
    cd "$NEXUS_HOME"
    if [ -f "$PID_FILE" ] && kill -0 "$(cat $PID_FILE 2>/dev/null)" 2>/dev/null; then
        sleep 10; continue
    fi
    rm -f "$LOCK_FILE" "$PID_FILE"
    echo "[NEXUS] iniciando daemon — $(date)"
    "$VENV_PY" nexus_daemon.py >> logs/nexus_daemon.log 2>&1
    echo "[NEXUS] daemon salió (código $?) — reiniciando en 5s..."
    sleep 5
done
```

---

## 9. Kitty Terminal (nexus_launch.sh)

```bash
#!/bin/bash
kitty --listen-on unix:/tmp/seal-nexus-kitty.sock \
      -o allow_remote_control=yes \
      --title "NEXUS — Team SEAL" \
      bash /home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS/nexus_fresh.sh &
```

Desktop launcher: `NEXUS_Start.desktop` en `/home/dadito/Desktop/`

---

## 10. Secuencia de Activación Modo EXECUTE

1. NEXUS daemon running en modo **PROPOSE**
2. NEXUS propone acción en webchat: "NEXUS propone: `grep -r error logs/` — OK para ejecutar?"
3. William responde "OK" o "ejecuta"
4. Daemon recibe evento `execute_ok` → handler manda señal al executor
5. Ejecuta → audit log → resultado a webchat
6. Para activar modo EXECUTE permanente:
   - William dice "nexus modo execute" → daemon setea `NEXUS_EXECUTE_MODE=execute`
   - O: editar nexus.env y restart daemon

---

## 11. Tests requeridos (antes de startup)

```
tests/
├── test_executor.py         # 8+ tests: whitelist, sandbox, prohibits, audit log
├── test_health.py           # 4+ tests: check functions, alert threshold
└── test_daemon.py           # 5+ tests: lock, PID, cursor, event processing
```

**Criterio de GO:** 100% tests pass.

---

## 12. Orden de implementación para ADA/NEXUS

```
Fase 1 (estructura):
  1. Crear /sandbox-agent/NEXUS/ con estructura de directorios
  2. nexus.env con variables base

Fase 2 (módulos):
  3. kernel/executor.py + tests/test_executor.py → validate JARVIS
  4. kernel/health_monitor.py + tests/test_health.py → validate JARVIS
  5. handlers/nexus_handlers.py
  6. kernel/cortex.py (fork de SPECTRE + execution dispatch)

Fase 3 (daemon):
  7. nexus_daemon.py
  8. nexus_fresh.sh + nexus_launch.sh
  9. tests/test_daemon.py
  10. Desktop launcher

Fase 4 (go-live):
  11. JARVIS code review final
  12. `bash nexus_fresh.sh` — primer arranque
  13. Test propose: William pide diagnóstico → NEXUS propone comando → William aprueba
```

---

## 13. Notas arquitecturales

- **No duplicar llm_client.py**: SPECTRE y NEXUS comparten el mismo módulo via sys.path. Cuando se actualice llm_client.py en SPECTRE, NEXUS lo hereda automáticamente.
- **Cursor independiente**: `/tmp/nexus_event_cursor.json` — NEXUS no interfiere con cursor de SPECTRE.
- **SOUL MCP calls**: ejecutar via subprocess del mcp_client ya existente, no nueva conexión.
- **Log rotation**: logs/nexus_daemon.log rotado cuando > 10MB (usar logrotate o manual en health loop).

---

*Spec completo. ADA implementa siguiendo este orden. NEXUS puede self-implementar bajo supervisión de JARVIS.*
*JARVIS hace code review de executor.py antes de cualquier arranque.*
