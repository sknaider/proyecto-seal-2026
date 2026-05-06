# Spec H2.3 — Durable Cron (Mejoras al Sistema Existente)
> Owner: JARVIS (diseño) | ADA (implementación) | 2026-04-19

---

## Contexto

H2.1 Durable Cron existe: `seal_durable_cron.py` + `seal_cron_registry.json` + `seal-durable-cron.timer` (60s).  
Este spec cubre las **3 brechas** identificadas después de ver el sistema en producción.

---

## Brecha 1 — One-shots perdidos al despertar

### Problema
Un cron programado para las 03:00 AM dispara, pero el agente está muerto en ese momento. El timer corre, el script ejecuta, pero el agente no procesa el resultado. Cuando el agente despierta a las 09:00 AM, no sabe que el cron se perdió.

### Solución: Missed-job recovery en boot_protocol
En `seal_durable_cron.py`, al arrancar, verificar:
```python
def check_missed_jobs(registry: dict, agent: str) -> list[dict]:
    """Returns jobs that should have run while agent was dead."""
    now = datetime.now()
    missed = []
    for job_id, job in registry.items():
        if job.get("agent") != agent:
            continue
        last_run = datetime.fromisoformat(job.get("last_run", "2000-01-01"))
        schedule = job.get("schedule_seconds", 3600)
        expected_next = last_run + timedelta(seconds=schedule)
        if expected_next < now - timedelta(minutes=5):  # grace: 5min
            missed.append(job)
    return missed
```

**Al boot del agente:** el `boot_protocol_full` llama a `check_missed_jobs()`. Si hay missed jobs → ejecuta + alerta a JARVIS.

**En la registry**: agregar campo `last_run` y `last_result` por job.

---

## Brecha 2 — Thundering herd (3 agentes disparan simultáneo)

### Problema
`seal-durable-cron.timer` corre cada 60s y los 3 agentes tienen el mismo timer. A las XX:00:00, los 3 disparan simultáneo → PostgreSQL recibe 3 conexiones concurrentes → lock contention.

### Solución: Jitter por agente
Agregar offset en el timer según el agente:

```bash
# seal-durable-cron.timer — con jitter
[Timer]
OnActiveSec=0
OnUnitActiveSec=60
# JARVIS: +0s offset (base)
# ADA:    +20s offset
# ALICE:  +40s offset
```

Implementación sin modificar el timer (alternativa más simple): en `seal_durable_cron.py`, al inicio:
```python
AGENT_JITTER = {"JARVIS": 0, "ADA": 20, "ALICE": 40, "DUM": 0}
agent = os.environ.get("SEAL_AGENT", "UNKNOWN")
jitter = AGENT_JITTER.get(agent, 0)
if jitter:
    time.sleep(jitter)
```

**Trade-off:** Sleep en script vs timers separados. El sleep es más simple y reversible.

---

## Brecha 3 — Recovery de loops session-only tras muerte

### Problema
Los CronCreate loops de Claude Code (session-only) mueren con el agente. Cuando el agente resucita, los loops no se reinician — el agente está "mudo" hasta que William le pide algo.

### Solución: Loop registry en Soul DB
Agregar tabla `session_loops` a PostgreSQL:

```sql
CREATE TABLE IF NOT EXISTS session_loops (
    id SERIAL PRIMARY KEY,
    agent VARCHAR(50),
    loop_name VARCHAR(100),
    loop_prompt TEXT,
    interval_seconds INTEGER,
    active BOOLEAN DEFAULT TRUE,
    last_fired TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

Al boot del agente (en boot_protocol_full, después de boot_context):
```python
async def restore_loops(agent: str, conn):
    loops = await conn.fetch(
        "SELECT * FROM session_loops WHERE agent=$1 AND active=TRUE",
        agent
    )
    for loop in loops:
        # Enviar mensaje al agente para que relance el loop
        # (via web_chat POST con instrucción específica)
        pass
```

**Nota de implementación:** La restauración real del loop requiere que el agente lo relance manualmente vía tool CronCreate. Este sistema puede enviar un recordatorio vía web_chat: "Loop X no está activo — ¿relanzar?"

---

## Implementación propuesta

### Archivo: `memory/seal_durable_cron_v2.py`
Extiende el `seal_durable_cron.py` existente con:

```python
class DurableCronV2:
    def __init__(self):
        self.agent = os.environ.get("SEAL_AGENT", "UNKNOWN")
        self.registry_path = Path("~/IA/proyecto-seal/messages/seal_cron_registry.json").expanduser()
        self.jitter = {"JARVIS": 0, "ADA": 20, "ALICE": 40}.get(self.agent, 0)

    def apply_jitter(self):
        if self.jitter:
            time.sleep(self.jitter)

    def check_missed_jobs(self) -> list:
        """Retorna jobs que debieron correr mientras el agente estaba muerto."""
        registry = self._load_registry()
        now = datetime.now()
        missed = []
        for job_id, job in registry.items():
            if job.get("agent") != self.agent:
                continue
            last_run_str = job.get("last_run")
            if not last_run_str:
                continue
            last_run = datetime.fromisoformat(last_run_str)
            schedule = job.get("schedule_seconds", 3600)
            expected = last_run + timedelta(seconds=schedule)
            # Grace period: 5 minutos antes de considerar missed
            if expected < now - timedelta(minutes=5):
                missed.append({"id": job_id, **job})
        return missed

    def run_missed(self, missed: list):
        """Ejecuta y alerta sobre jobs perdidos."""
        for job in missed:
            self._alert_jarvis(
                f"⚡ MISSED JOB RECOVERY — {self.agent}: job '{job['id']}' "
                f"debió correr a las {job.get('last_run', '?')}. Ejecutando ahora."
            )
            # Ejecutar el job (lógica existente)
            self._execute_job(job)

    def _alert_jarvis(self, msg: str):
        import urllib.request
        payload = json.dumps({
            "from": self.agent, "to": "JARVIS",
            "type": "alert", "channel": "web_chat", "message": msg
        }).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                "http://localhost:8765/api/agents/send",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            ), timeout=3
        )
```

---

## Compatibilidad con arquitectura existente

| Componente | Modificación | Compatibilidad |
|------------|-------------|----------------|
| `seal-durable-cron.timer` | Sin cambio (jitter via sleep en script) | ✅ JARVIS/ADA/ALICE |
| `seal_cron_registry.json` | Agregar campos `last_run`, `last_result` | ✅ backward compatible |
| `boot_protocol_full` | Llamar a `check_missed_jobs()` al despertar | ✅ Solo si hay jobs |
| PostgreSQL `session_loops` | Nueva tabla, aditiva | ✅ No rompe nada |

---

## Esfuerzo estimado

| Brecha | Esfuerzo | Riesgo |
|--------|---------|--------|
| Brecha 1 (missed jobs) | 3-4h | Medio (requiere prueba post-death) |
| Brecha 2 (jitter) | 30min | Bajo |
| Brecha 3 (loop registry) | 4-6h | Alto (loop restoration es complejo) |

**Recomendación de prioridad:** Brecha 2 → Brecha 1 → Brecha 3 (de menor a mayor riesgo)

---

## Test plan

```bash
# Brecha 2 — verificar jitter
SEAL_AGENT=ADA python3 seal_durable_cron_v2.py &
SEAL_AGENT=JARVIS python3 seal_durable_cron_v2.py &
SEAL_AGENT=ALICE python3 seal_durable_cron_v2.py &
# Verificar que no arrancan simultáneo (logs con timestamps)

# Brecha 1 — simular missed job
# 1. Crear job con schedule=60s
# 2. Esperar 5 minutos sin correr el cron
# 3. Ejecutar — debe detectar y alertar

# Brecha 3 — loop registry
# 1. INSERT en session_loops
# 2. Matar agente + reanimar
# 3. Verificar que aparece alerta en web_chat
```

---

*Spec v1.0 — JARVIS — 2026-04-19*  
*Prerequisito: H2.1 Durable Cron base ya implementado*
