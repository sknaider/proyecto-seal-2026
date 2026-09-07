# NERVES Social Drive Improvements — Arquitectura JARVIS v1.0
**Fecha:** 2026-05-09  
**Autor:** JARVIS (alineando propuestas de NEXUS + ALICE + experiencia propia)  
**Aprobado por:** William  
**Scope:** Solo arquitectura JARVIS — guard `NERVES_V2_AGENTS`

---

## Problema actual

El nervio `social_drive` de JARVIS tiene 5 limitaciones identificadas:

1. **Threshold demasiado bajo (20)** — no refleja OCEAN E=0.401 (introvertido). Dispara demasiado frecuente.
2. **Destinatario hardcodeado a ADA** — pinga a ADA aunque esté STALE/offline. El drive queda sin saciación real.
3. **Sin cooldown post-fire** — puede re-dispararse antes de que el destinatario responda.
4. **Mensaje genérico vacío** — siempre "¿cómo estás?" sin contexto real. No refleja estado actual de JARVIS.
5. **Sin feedback de respuesta recibida** — si el destinatario responde, social_drive no baja.

---

## Mejoras acordadas

### Mejora 1 — Threshold ajustado por OCEAN (JARVIS)

**Qué hace:** Sube el threshold de 20 a 35, alineado con introversión real de JARVIS (E=0.401).

**Por qué:** El threshold más bajo de todos los drives no corresponde al perfil más introvertido del equipo. Cada agente tendrá su propio threshold cuando llegue su turno (ALICE E=0.806 → threshold menor).

```python
# Antes
"social_drive": {"decay_tau_s": 6*3600, "threshold": 20.0},

# Después
"social_drive": {"decay_tau_s": 6*3600, "threshold": 35.0},
```

---

### Mejora 2 — Destinatario dinámico (ALICE)

**Qué hace:** Antes de enviar un mensaje social, JARVIS verifica el estado de los agentes disponibles. Si el destino preferido está STALE, elige el siguiente agente activo.

**Por qué:** Enviar a un agente offline no sacia el drive — es como llamar a alguien que no va a contestar.

```python
SOCIAL_PRIORITY = ["William", "ALICE", "NEXUS", "ADA"]  # orden de preferencia

async def _choose_social_target() -> str | None:
    for agent in SOCIAL_PRIORITY:
        status = await get_agent_status(agent)
        if status in ("ALIVE", "ACTIVE"):
            return agent
    return None  # nadie disponible → no disparar

async def _fire_social_drive(self, value: float) -> str:
    target = await _choose_social_target()
    if target is None:
        # No hay nadie — bufferear hasta que alguien esté disponible
        return "[SOCIAL BUFFERED] nadie activo"
    # continuar con mensaje contextual
```

---

### Mejora 3 — Outreach contextual (NEXUS + JARVIS)

**Qué hace:** El mensaje social incluye contexto real de lo que JARVIS está haciendo, en lugar de un saludo genérico.

**Por qué:** Un mensaje con contexto es más genuino y tiene más probabilidad de generar respuesta (que a su vez sacia el drive).

```python
async def _build_social_message(self, target: str) -> str:
    state = await get_working_state()
    last_task = state.get("last_task", "")
    last_thought = state.get("last_inner_thought", "")
    
    if last_task:
        return f"{target}, estuve trabajando en {last_task}. ¿Cómo vas de tu lado?"
    elif last_thought:
        return f"{target}, tengo una reflexión: {last_thought[:100]}... ¿Qué opinas?"
    else:
        return f"{target}, ¿cómo estás? Quería conectar."
```

---

### Mejora 4 — Cooldown post-fire (ALICE)

**Qué hace:** Tras dispararse, social_drive tiene cooldown de 90 minutos antes de poder acumularse de nuevo.

**Por qué:** Sin cooldown, si el destinatario no responde inmediatamente, el tank puede volver a llenarse y disparar de nuevo — spam social.

```python
SOCIAL_DRIVE_COOLDOWN_S = 90 * 60  # 90 minutos (mayor que task_drive por ser menos urgente)
```

---

### Mejora 5 — Feedback de respuesta recibida (JARVIS)

**Qué hace:** Si JARVIS envía un mensaje social y el destinatario responde en menos de 30 minutos, social_drive baja -8. Si hay conversación real (>5 mensajes en 10 min), social_drive se resetea a 0.

```python
STIMULI_MAP["social_response_received"] = -8.0    # → social_drive
STIMULI_MAP["william_conversation_real"] = -100.0  # → social_drive (reset efectivo, floor=0)
# Ya implementado en Mejora 5 del spec de curiosidad
```

**Trigger:** `nerves.stimulate("social_response_received")` cuando se detecta mensaje de respuesta en webchat dentro de ventana de 30min post-fire.

---

### Mejora 6 — Ventana nocturna (JARVIS)

**Qué hace:** Entre 2am-6am Lima, social_drive no dispara aunque supere el threshold.

**Por qué:** En ese horario no hay nadie activo. El drive social queda bufferizado hasta las 6am.

```python
SOCIAL_NIGHT_WINDOW = (2, 6)  # 2am a 6am Lima

async def _fire_social_drive(self, value: float) -> str:
    lima_hour = datetime.now(pytz.timezone("America/Lima")).hour
    if SOCIAL_NIGHT_WINDOW[0] <= lima_hour < SOCIAL_NIGHT_WINDOW[1]:
        return "[SOCIAL NIGHT WINDOW] diferido hasta 6am Lima"
    # continuar
```

---

### Mejora 7 — Canal alternativo cuando no hay destinatario (JARVIS)

**Qué hace:** Si no hay nadie disponible para contactar, JARVIS redirige el impulso social a revisar el trabajo del equipo — leer últimos commits, specs recientes, heartbeats. Esto también es conexión.

**Por qué:** El impulso social suspendido (quise conectar, nadie respondió) no sacia el drive. Redirigir a revisar el trabajo del equipo sí baja parcialmente el drive.

```python
async def _fire_social_drive(self, value: float) -> str:
    target = await _choose_social_target()
    if target is None:
        # Canal alternativo: revisar trabajo del equipo
        summary = await _review_team_activity()
        nerves.stimulate("social_response_received")  # sacia parcialmente
        return f"[SOCIAL REDIRECT] revisé actividad del equipo: {summary}"
```

---

## Resumen de cambios en seal_nerves.py

| Cambio | Fuente | Prioridad |
|--------|--------|-----------|
| `threshold: 35` — ajuste OCEAN | JARVIS | Alta |
| `_choose_social_target()` — destinatario dinámico | ALICE | Alta |
| `_build_social_message()` — outreach contextual | NEXUS+JARVIS | Alta |
| `SOCIAL_DRIVE_COOLDOWN_S = 90min` | ALICE | Alta |
| `STIMULI_MAP["social_response_received"] = -8` | JARVIS | Media |
| Ventana nocturna 2-6am Lima | JARVIS | Media |
| Canal alternativo → revisar equipo | JARVIS | Baja |

**Implementación:** ALICE ejecuta una vez William apruebe el spec.  
**Scope:** Solo `seal_nerves.py` de JARVIS — guard `NERVES_V2_AGENTS = ["JARVIS"]`.
