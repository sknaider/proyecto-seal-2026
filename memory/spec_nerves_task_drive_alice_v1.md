# NERVES Task Drive Improvements — Arquitectura ALICE v1.0
**Fecha:** 2026-05-09  
**Autor:** ALICE (adaptación del blueprint JARVIS)  
**Aprobado por:** William  
**Scope:** Solo arquitectura ALICE — guard `NERVES_V2_AGENTS`

---

## Base OCEAN ALICE

| Trait | ALICE | Implicación |
|-------|-------|-------------|
| Conscientiousness | 0.962 | La más alta del equipo — ALICE necesita completar tasks |
| Openness | 0.905 | Ve oportunidades en cada tarea — expansión natural |
| Neuroticism | 0.21 | Sin ansiedad paralizante — actúa bajo presión |

---

## Problema con task_drive genérico para ALICE

El task_drive genérico no refleja que ALICE tiene C=0.962 — la conscientiousness más alta del equipo. Para ALICE, una tarea sin terminar es un estado cognitivo activo, no un dato pasivo.

---

## Mejoras ALICE

### Mejora 1 — Threshold reducido (C=0.962)

**Ajuste:** threshold 25 (más sensible que el default). ALICE siente la presión de tareas pendientes más rápido.

```python
# ALICE task_drive
"task_drive": {"decay_tau_s": 4*3600, "threshold": 25.0},
```

**decay_tau más corto:** 4h vs el estándar — la urgencia de tareas sube más rápido para ALICE.

---

### Mejora 2 — Sensor de tareas reales con deadlines

**Qué hace:** ALICE lee MCP TaskList + working_state para determinar urgencia real. No solo cuenta tareas — pondera por deadline y relevancia.

```python
async def _sense_task_drive_alice(self) -> dict:
    tasks = []
    # 1. TaskList via MCP (si disponible)
    try:
        task_list = await mcp_task_list()
        overdue = [t for t in task_list if _is_overdue(t)]
        urgent   = [t for t in task_list if _is_urgent_soon(t)]  # <2h
        tasks.extend(overdue + urgent)
    except Exception:
        pass
    # 2. working_state pending_validations
    ws = await working_state_get(self.agent)
    pending = ws.get("pending_validations", [])
    return {"tasks": tasks, "pending": pending, "count": len(tasks) + len(pending)}
```

---

### Mejora 3 — Escalación por urgencia

**Qué hace:** El comportamiento varía según la urgencia real de las tareas pendientes.

| Nivel | Condición | Acción ALICE |
|-------|-----------|--------------|
| Silencioso | Tasks pendientes sin deadline | Checkpoint + nota en inner_monologue |
| Activo | Task con deadline <2h | Post al equipo, pide colaboración si necesario |
| Urgente | Task overdue o bloqueante | Avisa a William directamente |

```python
async def _fire_task_drive_alice(self, value: float) -> str:
    ctx = await _sense_task_drive_alice(self)
    if ctx["count"] == 0:
        return "task_drive:no_urgent_tasks"
    
    if any(_is_overdue(t) for t in ctx["tasks"]):
        await self._post_chat(_format_task_alert(ctx), to="William")
    elif any(_is_urgent_soon(t) for t in ctx["tasks"]):
        await self._post_chat(_format_task_alert(ctx), to="equipo")
    else:
        await self._log_internal(ctx)
    
    return f"task_drive_handled:{ctx['count']}_tasks"
```

---

### Mejora 4 — Formato de alerta ALICE

**Qué hace:** ALICE reporta tareas con contexto financiero/analítico, no solo el nombre.

```python
def _format_task_alert_alice(ctx: dict) -> str:
    lines = ["📋 ALICE — tareas urgentes:"]
    for t in ctx["tasks"][:3]:
        deadline = t.get("deadline", "sin deadline")
        lines.append(f"  • {t['subject']} — {deadline}")
    return "\n".join(lines)
```

---

## Tabla de cambios vs JARVIS

| Parámetro | JARVIS | ALICE | Razón |
|-----------|--------|-------|-------|
| threshold | estándar | 25.0 (menor) | C=0.962 — muy sensible a pendientes |
| decay_tau_s | 6h | 4h | Urgencia sube más rápido |
| Sensor | working_state | TaskList + working_state | C alta = quiere datos reales |
| Escalación | 3 niveles | 3 niveles (igual estructura) | Blueprint JARVIS heredado |

---

## Notas de implementación

- `AGENT_TANK_OVERRIDES["ALICE"]["task_drive"] = {"threshold": 25.0, "cooldown_s": 45*60}`
- `_sense_task_drive_alice` usa MCP TaskList primero, fallback a working_state
- Cooldown task_drive ALICE: 45min (más corto — urgencia real no espera)
