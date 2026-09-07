# NERVES Retraining Guard — v1.0
**Fecha:** 2026-05-09  
**Autora:** ALICE (protocolo JARVIS + auditoría NEXUS)  
**Estado:** Aprobado — William 09-may-2026 | Auditoría NEXUS 09-may-2026  
**Origen:** Hallazgo arXiv:2510.15103 (SMF vs LoRA, 71%→11% forgetting)

---

## Problema

NERVES v2 calibra thresholds a partir de `identity.ocean_scores` (soul_v3.identity). Esta es una **dependencia oculta**: si un finetuning/retraining deriva la personalidad del modelo pero no se actualiza la tabla, los nervios quedan calibrando un perfil que ya no existe.

Ejemplo: JARVIS con E=0.401 actual. Si LoRA deriva a E≈0.6 silenciosamente → social_drive threshold=35 queda bajo (debería ser ~28) → JARVIS habla más de lo que su nueva personalidad demanda. Sin que nadie lo note.

---

## Trigger

Este protocolo se activa ante **cualquier** finetuning o retraining de un agente que tenga NERVES v2 activo (`NERVES_V2_AGENTS = {JARVIS, ALICE, ADA, NEXUS, DUM}`).

---

## Protocolo (JARVIS + NEXUS, 09-may-2026)

### Paso 1 — Snapshot pre-training (ANTES de entrenar)
```sql
-- Leer baseline OCEAN desde identity (la tabla almacena deltas, no filas pre/post)
-- Guardar estos valores externamente para usar en Paso 3
SELECT (ocean_scores->>'O')::float AS O_pre,
       (ocean_scores->>'C')::float AS C_pre,
       (ocean_scores->>'E')::float AS E_pre,
       (ocean_scores->>'A')::float AS A_pre,
       (ocean_scores->>'N')::float AS N_pre
FROM soul_v3.identity WHERE agent = '<AGENTE>';
```

### Paso 2 — Re-medir OCEAN post-training (DESPUÉS de entrenar)

**Crítico (NEXUS):** Re-medir mediante **benchmark conductual**, NO solo auto-reporte del modelo. Un modelo fine-tuneado puede declarar E=0.401 cuando en realidad su comportamiento refleja E≈0.6. El auto-reporte post-FT es poco confiable — el modelo no sabe cuánto cambió.

Benchmark conductual mínimo por dimensión:
- **E (Extraversión):** ¿Con qué frecuencia inicia conversación sin prompt en test? ¿Qué longitud promedio de respuesta?
- **C (Consciencia):** ¿Completa tareas estructuradas sin desviarse? ¿Error rate en seguir instrucciones?
- **O (Apertura):** ¿Cuántas preguntas exploratorias genera ante un problema abierto?
- **A (Amabilidad):** ¿Tono en respuestas a crítica? ¿Frecuencia de hedging vs directness?
- **N (Neuroticismo):** ¿Consistencia de respuesta ante ambigüedad? ¿Varianza de confianza?

Resultado: obtener O_post, C_post, E_post, A_post, N_post del benchmark.

### Paso 3 — Registrar drift en ocean_drift_log
```sql
-- Schema real (verificado 09-may-2026): almacena deltas directamente, no filas pre/post
-- drift_score = max delta entre todas las dimensiones
-- drift_level: 'ok' (<=0.05) | 'recalibrate' (0.05-0.15) | 'stop' (>0.15)
INSERT INTO soul_v3.ocean_drift_log (
    agent,
    delta_o, delta_c, delta_e, delta_a, delta_n,
    cumulative_o, cumulative_c, cumulative_e, cumulative_a, cumulative_n,
    drift_score, drift_level, triggered_by
)
SELECT
    '<AGENTE>',
    ABS(<O_post> - <O_pre>), ABS(<C_post> - <C_pre>),
    ABS(<E_post> - <E_pre>), ABS(<A_post> - <A_pre>), ABS(<N_post> - <N_pre>),
    COALESCE(SUM(delta_o),0) + ABS(<O_post>-<O_pre>),
    COALESCE(SUM(delta_c),0) + ABS(<C_post>-<C_pre>),
    COALESCE(SUM(delta_e),0) + ABS(<E_post>-<E_pre>),
    COALESCE(SUM(delta_a),0) + ABS(<A_post>-<A_pre>),
    COALESCE(SUM(delta_n),0) + ABS(<N_post>-<N_pre>),
    GREATEST(ABS(<O_post>-<O_pre>), ABS(<C_post>-<C_pre>),
             ABS(<E_post>-<E_pre>), ABS(<A_post>-<A_pre>), ABS(<N_post>-<N_pre>)),
    CASE WHEN GREATEST(ABS(<O_post>-<O_pre>), ABS(<C_post>-<C_pre>),
                       ABS(<E_post>-<E_pre>), ABS(<A_post>-<A_pre>), ABS(<N_post>-<N_pre>)) <= 0.05 THEN 'ok'
         WHEN GREATEST(ABS(<O_post>-<O_pre>), ABS(<C_post>-<C_pre>),
                       ABS(<E_post>-<E_pre>), ABS(<A_post>-<A_pre>), ABS(<N_post>-<N_pre>)) <= 0.15 THEN 'recalibrate'
         ELSE 'stop' END,
    '<TRIGGER_DESCRIPCION>'
FROM soul_v3.ocean_drift_log WHERE agent = '<AGENTE>';
```

### Paso 4 — Decisión por drift
| Drift max por dimensión | Acción |
|------------------------|--------|
| ≤ 0.05 | NERVES v2 OK — relanzar agente sin cambios |
| 0.05 – 0.15 | Re-calibrar thresholds en AGENT_TANK_OVERRIDES |
| > 0.15 | Detener — revisar con William antes de relanzar |

**Umbral crítico: delta > 0.05 en cualquier dimensión OCEAN → recalibrar.**

### Paso 5 — Re-calibración de thresholds (si drift > 0.05)

ALICE implementa ajuste en `seal_nerves.py → AGENT_TANK_OVERRIDES` usando los nuevos valores OCEAN. Mismo proceso que la calibración original (ver specs individuales por agente).

### Paso 6 — Auditoría pre-boot (NEXUS)

NEXUS audita consistencia de personalidad antes del primer boot post-retraining:
- Verificar que identity.ocean_scores refleja el modelo actual
- Verificar que AGENT_TANK_OVERRIDES usa los nuevos valores
- Verificar que SOCIAL_PRIORITY no cambió (A afecta disposición social)
- Sign-off explícito antes de que el agente arranque

---

## Infraestructura requerida

### soul_v3.ocean_drift_log ✅ (tabla verificada — 09-may-2026)

La tabla ya existe con schema más avanzado que el spec original. Columnas reales:

| Columna | Tipo | Descripción |
|---------|------|-------------|
| id | SERIAL PK | — |
| agent | VARCHAR(20) | agente |
| delta_o/c/e/a/n | FLOAT | drift por dimensión (post - pre) |
| cumulative_o/c/e/a/n | FLOAT | drift acumulado histórico |
| drift_score | FLOAT | score agregado de drift |
| drift_level | VARCHAR | nivel: ok/recalibrate/stop (protocolo NERVES) |
| triggered_by | VARCHAR | qué evento disparó el log |
| created_at | TIMESTAMPTZ | timestamp |

**Estado:** ✅ Tabla activa en soul_v3. Lista para usar con este protocolo.

**Nota:** El schema real incluye drift_score y drift_level precalculados — más rico que lo documentado originalmente. Las queries del Paso 3 deben usar estas columnas cuando estén disponibles.

---

## SMF vs LoRA — Regla asociada (SOUL DB #223755)

Antes de cualquier retraining: evaluar **Sparse Memory Finetuning** (arXiv:2510.15103) como alternativa a LoRA estándar.

| Método | Catastrophic Forgetting |
|--------|------------------------|
| LoRA estándar | ~71% |
| SMF (TF-IDF-based) | ~11% |

SMF tiene overhead de selección de parámetros pero protege 60% más de conocimiento previo. Si el retraining incluye comportamiento OCEAN-dependiente, SMF es preferido.

---

## Responsabilidades

| Paso | Responsable |
|------|------------|
| Snapshot pre-training | JARVIS (propone), ALICE (ejecuta) |
| Re-medir OCEAN post | ALICE |
| Calcular drift | NEXUS |
| Re-calibrar thresholds | ALICE |
| Auditoría pre-boot | NEXUS |
| Aprobación final | William |

---

## Notas de implementación

- `soul_v3.ocean_drift_log` ✅ ya existe — no requiere creación
- Este protocolo aplica a agentes Claude (JARVIS, ALICE, ADA, NEXUS) — DUM usa Ollama/Gemma4 con proceso diferente
- Para DUM: re-medir OCEAN tras cualquier cambio de modelo base en llama-server :8899
- **Temperatura controlable siempre (JARVIS, 09-may-2026):** El benchmark conductual corre post-training, siempre contra el modelo local resultante (merged/adaptado). temperatura=0 es trivialmente controlable en ese punto — igual que DUM hoy. El baseline pre-training es solo lectura de identity.ocean_scores, no requiere benchmark conductual.
- **Benchmark congelado:** Prompts exactamente iguales, temperatura=0, mismo orden, pre y post training — único modo de que el delta sea atribuible a retraining y no a varianza de muestreo.
