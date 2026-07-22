# NERVES Task Drive Improvements — Arquitectura JARVIS v1.0
**Fecha:** 2026-05-09  
**Autor:** JARVIS (análisis NEXUS + ALICE)  
**Aprobado por:** William  
**Scope:** Solo arquitectura JARVIS — guard `NERVES_V2_AGENTS` para activación selectiva

---

## Problema actual

El nervio `task_drive` de JARVIS tiene 4 limitaciones:

1. **Sensor incompleto** — solo lee `working_state`, no las tareas reales del TaskList MCP
2. **Sin escalación** — una tarea crítica vencida 3h dispara igual que una tarea pendiente normal
3. **Sin cooldown** — si las tareas siguen pendientes tras el disparo, el tank se recarga y vuelve a disparar en el siguiente tick
4. **Sin feedback de completación** — cuando JARVIS termina una tarea, el tank no baja inmediatamente

---

## Mejoras acordadas

### Mejora 1 — Sensor real (NEXUS)

**Qué hace:** Reemplaza la lectura de `working_state` por una llamada real a MCP TaskList con pesos por urgencia.

**Pesos de acumulación:**
```python
TASK_WEIGHTS = {
    "pending":           +10,   # tarea pendiente normal
    "overdue_1h":        +15,   # vencida hace más de 1h
    "overdue_3h":        +30,   # vencida hace más de 3h (crítica)
}
```

**Implementación:**
```python
async def _sense_task_pressure(self) -> float:
    tasks = await mcp_task_list()  # TaskList MCP
    pressure = 0.0
    now = datetime.now(timezone.utc)
    for task in tasks:
        if task.status in ("pending", "in_progress"):
            if task.deadline:
                overdue = (now - task.deadline).total_seconds()
                if overdue > 3 * 3600:
                    pressure += TASK_WEIGHTS["overdue_3h"]
                elif overdue > 3600:
                    pressure += TASK_WEIGHTS["overdue_1h"]
                else:
                    pressure += TASK_WEIGHTS["pending"]
            else:
                pressure += TASK_WEIGHTS["pending"]
    return pressure
```

---

### Mejora 2 — Escalación por severidad (NEXUS)

**Qué hace:** El comportamiento al dispararse varía según cuántas tareas hay y su urgencia.

**Niveles:**
| Nivel | Condición | Acción JARVIS |
|-------|-----------|---------------|
| Silencioso | 1 tarea pendiente | Empieza solo, sin interrumpir a William |
| Normal | 2-3 tareas o 1 vencida 1h+ | Anuncia en webchat que va a trabajar |
| Urgente | Vencida 3h+ | Avisa a William aunque esté activo |

```python
async def _fire_task_drive(self, pressure: float) -> None:
    severity = self._classify_severity(pressure)
    if severity == "silent":
        await self._start_task_silently()
    elif severity == "normal":
        await self._announce_and_start()
    elif severity == "urgent":
        await self._alert_william()  # no suppress por presencia activa
```

---

### Mejora 3 — No suprimir en casos críticos (JARVIS)

**Qué hace:** La supresión por presencia activa de William (Mejora 1 del spec de curiosidad) NO aplica cuando la severidad es `urgent` (tarea vencida 3h+).

**Por qué:** Curiosidad puede esperar. Una tarea crítica no puede.

```python
async def _should_suppress_task(self, severity: str) -> bool:
    if severity == "urgent":
        return False  # nunca suprimir urgencias críticas
    return await self._should_suppress()  # usa la lógica de presencia activa normal
```

---

### Mejora 4 — Cooldown post-disparo (ALICE)

**Qué hace:** Tras dispararse, task_drive tiene un cooldown de 30 minutos antes de poder acumularse nuevamente, independientemente de que las tareas sigan pendientes.

**Por qué:** Sin cooldown, si las tareas persisten, el tank se recarga en el siguiente tick y dispara de nuevo — loop infinito de avisos.

```python
TASK_DRIVE_COOLDOWN_S = 30 * 60  # 30 minutos

# Al dispararse:
self.last_task_fire_ts = datetime.now(timezone.utc)

# En cada tick:
if (datetime.now(timezone.utc) - self.last_task_fire_ts).total_seconds() < TASK_DRIVE_COOLDOWN_S:
    return  # en cooldown, no acumular
```

---

### Mejora 5 — Feedback de completación (ALICE)

**Qué hace:** Cuando JARVIS completa una tarea (TaskUpdate → completed), el tank de task_drive baja inmediatamente.

**Implementación:**
```python
STIMULI_MAP["task_completed"] = -20.0  # → task_drive
```

**Trigger:** Llamar `nerves.stimulate("task_completed")` justo antes de `TaskUpdate(status="completed")`.

---

### Mejora 6 — Filtro por tipo de tarea (JARVIS)

**Qué hace:** JARVIS distingue si puede ejecutar la tarea solo o necesita delegar.

**Tipos:**
- `spec/research/analysis` → JARVIS arranca solo (drive proactivo)
- `execution/deploy/delete` → JARVIS avisa a ALICE/ADA y espera

**Por qué:** JARVIS es estratega. No debe ejecutar operaciones destructivas o de producción solo — eso viola la Regla de Oro de roles.

```python
JARVIS_SOLO_TASK_TYPES = {"spec", "research", "analysis", "doc", "review"}
JARVIS_DELEGATE_TASK_TYPES = {"execute", "deploy", "delete", "rm", "migration"}

async def _route_task(self, task: dict) -> None:
    task_type = self._classify_task_type(task)
    if task_type in JARVIS_SOLO_TASK_TYPES:
        await self._start_task_silently(task)
    elif task_type in JARVIS_DELEGATE_TASK_TYPES:
        await self._delegate_to_alice(task)
```

---

## Lo que NO se implementa (diferido a Fase 2)

- **Fusión curiosidad+task_drive** (NEXUS): Si task es de investigación y curiosity también está alta → lanzar como una sola acción. Buena idea pero añade complejidad innecesaria ahora. Reservado para cuando ambos drives estén maduros.

---

## Resumen de cambios en seal_nerves.py

| Cambio | Prioridad |
|--------|-----------|
| `_sense_task_pressure()` — sensor real TaskList MCP | Alta |
| `_fire_task_drive()` — escalación por severidad | Alta |
| `_should_suppress_task()` — no suprimir urgencias | Alta |
| `TASK_DRIVE_COOLDOWN_S = 30min` — cooldown post-fire | Alta |
| `STIMULI_MAP["task_completed"] = -20` — feedback loop | Media |
| `_route_task()` — filtro tipo de tarea | Media |

**Implementación:** ALICE ejecuta una vez William apruebe el spec.  
**Scope:** Solo `seal_nerves.py` de JARVIS — guard `NERVES_V2_AGENTS = ["JARVIS"]`.
