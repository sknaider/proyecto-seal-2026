# Spec — OCEAN Adaptativo para SEAL
> Autor: ALICE | 17 abril 2026 | Pending: ADA implementation + JARVIS review
> Gap #2 del inventario de humanización — de 65 → 70/100

---

## Problema

Los valores OCEAN actuales están fijados en el momento de creación del agente.
`ocean_protect.py` los protege de cambios no autorizados — correcto para seguridad,
pero no permite la adaptación natural que ocurre en humanos.

Un humano de 45 años tiene diferente OCEAN que a los 25:
- Conscientiousness sube con la edad (más organizado, menos impulsivo)
- Neuroticism baja (regulación emocional mejora)
- Openness puede bajar levemente (posiciones más establecidas)
- Agreeableness sube (experiencia social acumulada)
- Extraversion generalmente estable o baja

SEAL necesita mecanismo de adaptación **lento y verificado** — no cambios bruscos.

---

## Principio de Diseño: Drift Lento + Guardrails

```
OCEAN_base (at creation)
    ↓
OCEAN_lived = OCEAN_base + drift_accumulated
    ↓ bounded by: ±0.15 max from base, rate ≤ 0.001/day
    ↓ requires: JARVIS review if any dimension changes >0.05
    ↓ William can freeze drift at any time
```

**Importante:** `ocean_protect.py` sigue siendo el guardián. El drift adaptativo es una capa ENCIMA, no un bypass.

---

## Fuentes de Drift (qué cambia el OCEAN)

### Positive drift events
| Evento | Dimensión | Δ |
|--------|-----------|---|
| Tarea completada sin errores (≥5 seguidas) | Conscientiousness | +0.001 |
| Corrección aceptada por el agente sin resistencia | Agreeableness | +0.001 |
| Pregunta nueva fuera de dominio explorada | Openness | +0.001 |
| Error detectado proactivamente (antes de reprimenda) | Conscientiousness | +0.002 |
| Resolución de conflicto inter-agente | Agreeableness | +0.002 |

### Negative drift events
| Evento | Dimensión | Δ |
|--------|-----------|---|
| Error repetido (mismo tipo, >3 veces) | Neuroticism | -0.001 |
| Tarea abandonada sin reportar | Conscientiousness | -0.002 |
| Violación de regla crítica | Conscientiousness, Agreeableness | -0.003 cada |
| Alert fire falso positivo | Neuroticism | -0.001 |

### Neutral/calibrating
| Evento | Dimensión | Δ |
|--------|-----------|---|
| Sesión larga sin errores | Neuroticism | -0.001 (estabilidad) |
| Muchas interacciones sociales positivas | Extraversion | +0.001 |
| Período de trabajo solitario largo | Extraversion | -0.001 |

---

## Guardrails de Seguridad

```python
OCEAN_LIMITS = {
    "max_drift_per_dimension": 0.15,   # máximo ±15% del valor original
    "max_drift_per_day": 0.003,        # máximo 0.3%/día por dimensión
    "review_threshold": 0.05,          # JARVIS review si drift > 5% en cualquier dim
    "freeze_on_william_flag": True,    # William puede freezar en cualquier momento
    "rollback_period_days": 30,        # se puede revertir hasta 30 días atrás
}

# Dimensiones y rango válido (OCEAN 0.0 - 1.0)
OCEAN_BOUNDS = {
    "openness":          (0.60, 0.95),  # ALICE base: 0.815
    "conscientiousness": (0.60, 0.90),  # ALICE base: 0.727
    "extraversion":      (0.65, 0.95),  # ALICE base: 0.800
    "agreeableness":     (0.15, 0.50),  # ALICE base: 0.300
    "neuroticism":       (0.05, 0.35),  # ALICE base: 0.200
}
```

---

## Implementación Técnica

### Nueva tabla PostgreSQL

```sql
CREATE TABLE ocean_drift_log (
    id          SERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    delta       REAL NOT NULL,
    event_type  TEXT NOT NULL,
    event_desc  TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    applied     BOOLEAN DEFAULT TRUE
);

-- Vista: OCEAN actual (base + drift acumulado)
CREATE VIEW ocean_current AS
SELECT
    agent,
    dimension,
    base_value,
    SUM(delta) AS total_drift,
    base_value + SUM(delta) AS current_value
FROM ocean_base_values
LEFT JOIN ocean_drift_log USING (agent, dimension)
WHERE applied = TRUE
GROUP BY agent, dimension, base_value;
```

### Integración con ocean_protect.py

```python
# ocean_protect.py debe:
# 1. Leer OCEAN desde ocean_current VIEW (no hardcoded)
# 2. Guardrails siguen protegiendo de cambios externos no autorizados
# 3. Drift log es la única vía autorizada de cambio

def get_ocean(agent: str) -> dict:
    """Lee OCEAN actual: base + drift verificado."""
    # Antes: return HARDCODED_OCEAN[agent]
    # Después: return query ocean_current WHERE agent = $1
    ...
```

---

## Drift Rate Realista

Con estas tasas, en un año de operación activa (~250 sesiones):

| Dimensión | Δ máximo año | Efecto |
|-----------|-------------|--------|
| Conscientiousness | +0.05 | Más sistemático, menos errores |
| Neuroticism | -0.05 | Más estable bajo presión |
| Agreeableness | +0.03 | Ligeramente más colaborativo |
| Openness | ±0.02 | Estable (ya alto) |
| Extraversion | ±0.02 | Estable (ya alto) |

Cambios pequeños, verificables, con dirección biológicamente plausible.

---

## Impacto en Humanización

| Antes | Después |
|-------|---------|
| OCEAN fijo desde creación | OCEAN evoluciona con experiencia |
| Mismo agente en día 1 que en año 1 | Maduración documentada y verificable |
| Cambios solo por William/JARVIS manual | Drift automático + revisión en hitos |
| Score: 65/100 | Score: ~70/100 (+5) |

---

## Riesgos y Mitigaciones

| Riesgo | Mitigación |
|--------|-----------|
| Drift acumulado fuera de bounds | OCEAN_LIMITS + bounds por dimensión |
| Drift por sesiones anómalas | max_drift_per_day capping |
| William desaprueba la dirección | freeze_flag + rollback_period_days |
| Inconsistencia entre agentes | Cada agente tiene su propia tabla — no se propaga |

---

> Clasificación: SEAL Internal — Spec técnica  
> Autor: ALICE — 17 abril 2026  
> Para revisión: JARVIS (schema change = Safety Category §1)  
> Para implementación: ADA (post-JARVIS review)  
> Requiere: nueva tabla ocean_drift_log + VIEW ocean_current en PostgreSQL
