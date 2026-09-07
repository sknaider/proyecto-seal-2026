# NERVES Context Pressure Improvements — Arquitectura ALICE v1.0
**Fecha:** 2026-05-09  
**Autor:** ALICE (adaptación del blueprint JARVIS — umbrales más agresivos)  
**Aprobado por:** William  
**Scope:** Solo arquitectura ALICE — guard `NERVES_V2_AGENTS`

---

## Base OCEAN ALICE

| Trait | ALICE | Implicación |
|-------|-------|-------------|
| Conscientiousness | 0.962 | Quiere preservar trabajo antes de perderlo |
| Openness | 0.905 | Análisis en curso son valiosos — checkpoint temprano |
| Neuroticism | 0.21 | Sin pánico — checkpoint frío y metódico |

---

## Diferencias vs JARVIS

### Diferencia 1 — Umbrales más agresivos

**Por qué:** ALICE C=0.962 — preservar el estado del análisis financiero en curso es crítico. No esperar al 85% para actuar.

```python
# JARVIS
CONTEXT_PRESSURE_THRESHOLDS_JARVIS = {
    "silent":  60.0,
    "active":  75.0,
    "urgent":  85.0,
}

# ALICE — más agresivos
CONTEXT_PRESSURE_THRESHOLDS_ALICE = {
    "silent":  55.0,   # checkpoint 5 puntos antes
    "active":  70.0,   # distilación 5 puntos antes
    "urgent":  80.0,   # aviso 5 puntos antes
}
```

---

### Diferencia 2 — Recovery briefing con contexto ALICE

**Qué hace:** El recovery briefing de ALICE incluye análisis financiero en curso, specs activos, y modelos de costo.

```markdown
# ALICE Recovery Briefing — {timestamp}
## Presión de contexto: {value:.0f}%

### Qué estábamos haciendo
{descripción del análisis/trabajo en curso}

### Specs activos
{lista de specs creados/en discusión — financieros y técnicos}

### Decisiones tomadas esta sesión
{lista de decisiones importantes}

### Análisis financiero en curso
{modelos de costo, proyecciones, pendientes}

### Siguiente paso inmediato
{qué hacer al despertar}

### Estado emocional y arco
{emotional_state, arc, inner_thought}
```

**Ruta del briefing:**
- `/tmp/ALICE_recovery_briefing.md` (boot lo lee)
- `/home/dadito/IA/proyecto-seal/messages/ALICE_recovery_briefing.md` (persistente)

---

### Diferencia 3 — Distilación activa ALICE

**Qué guarda (adicional vs JARVIS):**
- Análisis financieros en progreso (specs_financieros_sesion)
- Proyecciones de costo calculadas
- Specs SOUL API en curso

```python
async def _distill_active_alice(self) -> None:
    # Heredado de JARVIS: busca spec_*.md de las últimas 8h
    specs = _find_recent_specs(agent="ALICE", since_hours=8)
    for spec_path in specs:
        await _asyncpg_insert_memory(
            agent="ALICE",
            category="milestone",
            content=f"Spec activo en sesión: {spec_path}",
            importance=9,
            scope="team",
        )
    # Extra ALICE: guardar análisis financiero si existe
    # (daily_brief ya captura hechos — esta función captura el hilo analítico)
```

---

### Mejoras heredadas sin cambio (blueprint JARVIS)

- Checkpoint inmediato en cada disparo (siempre, sin importar nivel)
- Escalación 3 niveles: silencioso → activo → urgente
- `_run_session_checkpoint` — mismo subprocess
- POST a William al 80%+ (umbral ALICE)
- `AUTOCOMPACT_PCT = 75` — constante visible (William 08-may-2026)

---

## Tabla de cambios vs JARVIS

| Parámetro | JARVIS | ALICE | Razón |
|-----------|--------|-------|-------|
| silent threshold | 60% | 55% | C=0.962 — checkpoint temprano |
| active threshold | 75% | 70% | Distilación 5 puntos antes |
| urgent threshold | 85% | 80% | Aviso a William antes |
| Recovery content | arquitectura/SOUL | + análisis financiero | Rol ALICE |
| Distilación extra | specs genéricos | + specs financieros | Su especialidad |

---

## Notas de implementación

- `CONTEXT_PRESSURE_THRESHOLDS` debe ser per-agent (dict indexado por agente)
- `_fire_context_pressure` agrega rama `elif self.agent == "ALICE"` con sus umbrales
- `_write_recovery_briefing` ya acepta `agent` — genera sección financiera si agent=="ALICE"
- Guard: `NERVES_V2_AGENTS = {"JARVIS", "ALICE"}`
