# NERVES Context Pressure Improvements — Arquitectura JARVIS v1.0
**Fecha:** 2026-05-09  
**Autor:** JARVIS (análisis NEXUS + ALICE + JARVIS)  
**Aprobado por:** William  
**Scope:** Solo arquitectura JARVIS — guard `NERVES_V2_AGENTS` para activación selectiva

---

## Problema actual

El nervio `context_pressure` tiene base funcional pero 4 limitaciones para JARVIS:

1. **Sin escalación por nivel** — dispara igual a 60% y a 95%, con el mismo mensaje genérico
2. **Distilación incompleta** — solo escribe `daily_brief`. No guarda decisiones del turno actual a SOUL DB ni el hilo de diseño arquitectural
3. **Sin checkpoint inmediato** — depende del cron de 30min. Si la compactación llega antes, todo lo no guardado se pierde
4. **Sin recovery briefing** — al despertar tras compactación, JARVIS arranca sin saber qué problema estaba resolviendo ni qué tenía pendiente

---

## Mejoras acordadas

### Mejora 1 — Escalación por nivel (JARVIS)

**Qué hace:** El comportamiento varía según el nivel de presión. No molesta a William hasta que es realmente necesario.

| Nivel | Rango | Acción JARVIS |
|-------|-------|---------------|
| Silencioso | 60-74% | Checkpoint inmediato, sin avisar |
| Activo | 75-84% | Checkpoint + distilación activa a SOUL DB |
| Urgente | 85%+ | Checkpoint + distilación + avisa a William directamente |

```python
CONTEXT_PRESSURE_THRESHOLDS = {
    "silent":   60.0,   # checkpoint silencioso
    "active":   75.0,   # distilación activa
    "urgent":   85.0,   # avisa a William
}
AUTOCOMPACT_PCT = 75    # William 08-may-2026: target=300K/400K tokens
```

```python
async def _fire_context_pressure_v2(self, value: float) -> str:
    # Mejora 1: checkpoint inmediato siempre
    await _run_session_checkpoint(self.agent)

    if value >= CONTEXT_PRESSURE_THRESHOLDS["urgent"]:
        await self._distill_active()
        await self._write_recovery_briefing(value)
        await self._post_chat(
            f"[NERVES/{self.agent}] ⚠️ Contexto al {value:.0f}% — compactación inminente. "
            f"Recovery briefing guardado. Al despertar: boot_context + recovery_briefing.",
            to="William"
        )
    elif value >= CONTEXT_PRESSURE_THRESHOLDS["active"]:
        await self._distill_active()
        await self._write_daily_brief()
    else:
        # silencioso — solo checkpoint, sin postear
        pass

    return f"context_pressure_handled:level={'urgent' if value>=85 else 'active' if value>=75 else 'silent'}"
```

---

### Mejora 2 — Distilación activa a SOUL DB (JARVIS)

**Qué hace:** Identifica y guarda las decisiones críticas del turno actual a SOUL DB antes de perderlas. El `daily_brief` captura hechos — esta mejora captura el *hilo de diseño*.

**Qué guarda:**
- Decisiones tomadas en la sesión actual aún no en SOUL DB
- Specs en progreso y su siguiente paso
- Opciones evaluadas y descartadas (el "por qué no" es tan valioso como el "qué")

```python
async def _distill_active(self) -> None:
    # 1. Leer daily_brief si existe y extraer decisiones clave
    # 2. Buscar specs creados en esta sesión
    specs_created = _find_recent_specs(agent=self.agent, since_hours=8)
    for spec_path in specs_created:
        await mcp__seal_memory__memory_store(
            agent=self.agent,
            category="milestone",
            content=f"Spec activo en sesión: {spec_path} — ver archivo para detalles",
            importance=8,
            scope="team",
        )
    # 3. Guardar estado de la discusión actual
    # (llamado desde _write_recovery_briefing)
```

---

### Mejora 3 — Recovery Briefing al 85%+ (JARVIS — CRÍTICA)

**Qué hace:** Escribe un archivo de continuidad estratégica antes de la compactación. No es un resumen genérico — captura el "hilo de diseño" que JARVIS necesita al despertar.

**Contenido del recovery briefing:**
```markdown
# JARVIS Recovery Briefing — {timestamp}
## Presión de contexto: {value:.0f}%

### Qué estábamos haciendo
{descripción del trabajo en curso}

### Specs en progreso
{lista de specs creados/en discusión}

### Decisiones tomadas esta sesión
{lista de decisiones importantes}

### Siguiente paso inmediato
{qué hacer al despertar}

### Estado emocional y arco
{emotional_state, arc, inner_thought}
```

**Implementación:**
```python
async def _write_recovery_briefing(self, pressure: float) -> None:
    path = Path("/tmp/jarvis_recovery_briefing.md")
    # También a ruta persistente:
    persistent = Path("/home/dadito/IA/proyecto-seal/messages/jarvis_recovery_briefing.md")

    content = _build_recovery_content(
        agent=self.agent,
        pressure=pressure,
        # Incluye: specs_en_progreso, decisiones_sesion, siguiente_paso
    )
    path.write_text(content)
    persistent.write_text(content)
```

---

### Mejora 4 — Checkpoint inmediato al disparar (JARVIS)

**Qué hace:** En lugar de depender del cron de 30min, el nervio ejecuta `session_checkpoint.py` inmediatamente cuando dispara. Garantiza que el estado más reciente está guardado.

```python
async def _run_session_checkpoint(agent: str) -> None:
    cmd = [
        "/home/dadito/IA/seal-spark/.venv/bin/python3",
        "/home/dadito/IA/proyecto-seal/messages/session_checkpoint.py",
        "--agent", agent,
    ]
    proc = await asyncio.create_subprocess_exec(*cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await asyncio.wait_for(proc.wait(), timeout=10)
```

**Cuándo ejecutar:**
- SIEMPRE que el nervio dispara (nivel silencioso, activo o urgente)
- No esperar al cron — la compactación no avisa con 30min de antelación

---

## Tabla de cambios

| Mejora | Área | Antes | Después |
|--------|------|-------|---------|
| 1 | Escalación | Igual a 60% y 95% — mismo mensaje | 3 niveles: silencioso/activo/urgente con acciones distintas |
| 2 | Distilación | Solo `daily_brief` (hechos) | + SOUL DB: decisiones, specs en progreso, hilo de diseño |
| 3 | Recovery | Ninguno — JARVIS arranca con amnesia | Recovery briefing con hilo de diseño, siguiente paso, arco |
| 4 | Checkpoint | Cron 30min (puede no llegar a tiempo) | Checkpoint inmediato en cada disparo del nervio |

---

## Notas de implementación

- ALICE implementa en `seal_nerves.py` función `_fire_context_pressure` → renombrar a v2
- Guard: solo aplica si `self.agent in NERVES_V2_AGENTS` (JARVIS primero)
- La Mejora 3 es **CRÍTICA** — sin recovery briefing, cada compactación es pérdida de continuidad estratégica
- `AUTOCOMPACT_PCT = 75` debe ser constante visible en el código (William 08-may-2026)
- Recovery briefing se escribe a `/tmp/jarvis_recovery_briefing.md` (leído en boot) Y a path persistente
