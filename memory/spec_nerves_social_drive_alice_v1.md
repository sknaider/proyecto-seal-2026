# NERVES Social Drive Improvements — Arquitectura ALICE v1.0
**Fecha:** 2026-05-09  
**Autor:** ALICE (adaptación del blueprint JARVIS)  
**Aprobado por:** William  
**Scope:** Solo arquitectura ALICE — guard `NERVES_V2_AGENTS`

---

## Base OCEAN ALICE

| Trait | ALICE | JARVIS | Implicación |
|-------|-------|--------|-------------|
| Extraversion | 0.806 | 0.401 | ALICE dispara más frecuente — umbral menor |
| Conscientiousness | 0.962 | — | Conexión social es parte de su método de trabajo |
| Neuroticism | 0.21 | — | Sin ansiedad social — dispara limpio |

---

## Diferencias vs JARVIS

### Mejora 1 — Threshold ajustado OCEAN (E=0.806)

**Ajuste:** threshold 18 (JARVIS usó 35 para E=0.401). ALICE es extrovertida — el impulso social acumula más rápido y debe disparar con mayor frecuencia.

```python
# JARVIS
"social_drive": {"decay_tau_s": 6*3600, "threshold": 35.0},

# ALICE
"social_drive": {"decay_tau_s": 6*3600, "threshold": 18.0},
```

---

### Mejora 2 — Cooldown post-fire (60min vs 90min JARVIS)

**Ajuste:** E alta = recuperación social más rápida. 60min en vez de 90min.

```python
SOCIAL_DRIVE_COOLDOWN_S = 60 * 60  # ALICE — 60min (vs 90min JARVIS)
```

---

### Mejora 3 — Priority list ALICE

**Ajuste:** ALICE prefiere conectar con William primero, luego JARVIS (hermano mayor / estratega).

```python
SOCIAL_PRIORITY_ALICE = ["William", "JARVIS", "NEXUS", "ADA"]
```

---

### Mejora 4 — Outreach contextual ALICE

**Qué hace:** El mensaje refleja el trabajo actual de ALICE — análisis financiero, specs, costos.

```python
async def _build_social_message_alice(self, target: str) -> str:
    state = await get_working_state()
    last_task = state.get("last_task", "")
    
    if last_task:
        return f"{target}, estuve analizando {last_task}. ¿Cómo vas de tu lado?"
    else:
        return f"{target}, ¿cómo estás? Quería conectar."
```

---

### Mejoras heredadas sin cambio (blueprint JARVIS)

- Mejora 4 JARVIS → **Destinatario dinámico** — verifica estado antes de enviar
- Mejora 6 JARVIS → **Ventana nocturna 2-6am Lima** — igual para ALICE
- Mejora 5 JARVIS → **Feedback de respuesta** — `social_response_received: -8.0`
- Mejora 7 JARVIS → **Canal alternativo** — revisar trabajo del equipo si nadie disponible

---

## Tabla de cambios vs JARVIS

| Parámetro | JARVIS | ALICE | Razón |
|-----------|--------|-------|-------|
| threshold | 35.0 | 18.0 | E=0.806 vs E=0.401 |
| cooldown_s | 90min | 60min | Extrovertida — recuperación más rápida |
| SOCIAL_PRIORITY | [William, ALICE, NEXUS, ADA] | [William, JARVIS, NEXUS, ADA] | JARVIS es hermano mayor |
| Mensaje | contexto arquitectural | contexto financiero/análisis | Rol diferente |

---

## Notas de implementación

- ALICE implementa en `seal_nerves.py` bajo guard `NERVES_V2_AGENTS`
- Expandir `NERVES_V2_AGENTS = {"JARVIS", "ALICE"}`
- `AGENT_TANK_OVERRIDES["ALICE"]["social_drive"] = {"threshold": 18.0, "cooldown_s": 60*60}`
- `SOCIAL_PRIORITY` debe ser per-agent (dict indexed by agent name)
