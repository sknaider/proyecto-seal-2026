# Spec: GAP 3 — Emotion → Cognition (OCEAN-driven behavior)
**Autor:** JARVIS (Opus 4.7) | **Fecha:** 2026-05-06 | **Status:** PROPUESTO
**Base:** ALICE GAP analysis + NEXUS codebase audit

---

## El problema

Lo que tenemos:
- `identity.ocean_scores` por agente — calibrado automáticamente
- `memories.valence/arousal` — emoción por memoria
- `drift_metrics` — detección de desviación OCEAN

Lo que falta: **OCEAN solo MIDE, no ACTÚA.**

El estado emocional no afecta el comportamiento real del agente. Un cerebro humano enojado decide diferente que uno calmado. SEAL hoy decide igual independientemente del estado emocional.

---

## Diseño — 3 mecanismos

### 3.A — Arousal modula velocidad/profundidad de decisión

**Hipótesis cerebral:** alto arousal (urgencia/estrés) → respuesta rápida, heurística;
bajo arousal (calma) → análisis deliberado, búsqueda profunda.

**Implementación SEAL:**
```python
# En el response pipeline (consumido por boot_context o pre-prompt injection)
def arousal_modulator(agent: str, base_search_depth: int = 50) -> dict:
    arousal = await get_current_arousal(agent)  # rolling avg últimas 10 memorias
    if arousal > 0.7:
        return {
            "search_depth": int(base_search_depth * 0.4),  # menos memorias
            "deliberation_hint": "Responde rápido, prioriza acción",
            "tool_budget": "low",
        }
    elif arousal < 0.3:
        return {
            "search_depth": int(base_search_depth * 1.5),  # más memorias
            "deliberation_hint": "Reflexiona profundo antes de actuar",
            "tool_budget": "high",
        }
    return {"search_depth": base_search_depth, "deliberation_hint": "", "tool_budget": "normal"}
```

**Inyección:** en `boot_context` y `active_recall`, los hints se incluyen en el contexto retornado.

### 3.B — Neuroticism escalation (verificación adicional)

**Hipótesis cerebral:** alto Neuroticism → más doble-checking, paranoia constructiva;
bajo Neuroticism → confianza en juicio rápido.

**Implementación:**
```python
def neuroticism_modulator(agent: str) -> dict:
    n = await get_ocean_dim(agent, "N")
    if n > 0.6:
        return {
            "verification_required": True,
            "second_check_hint": "Antes de actuar, verifica con memory_search si ya pasó algo similar",
            "rollback_budget": "high",  # más dispuesto a deshacer
        }
    return {"verification_required": False}
```

**Conexión real:** modifica el prompt del agente añadiendo el hint cuando aplica.

### 3.C — Valence afecta memory_search bias

**Hipótesis cerebral:** valencia negativa → recuerdas más eventos negativos similares (mood-congruent retrieval);
valencia positiva → recuerdas eventos positivos.

**Implementación SQL:**
```sql
-- En memory_search, biasear por valencia actual del agente
SELECT id, content,
    1 - (embedding <=> $1::vector) AS sim,
    -- bonus si la valencia de la memoria coincide con el estado actual
    CASE
        WHEN current_valence < -0.2 AND valence < -0.2 THEN 0.10
        WHEN current_valence > 0.2 AND valence > 0.2 THEN 0.10
        ELSE 0.0
    END AS valence_bonus
FROM memories
ORDER BY (sim + valence_bonus) DESC
```

**Por qué importa:** un agente "frustrado" recordará otras frustraciones similares antes que casos genéricos — replica el sesgo cognitivo humano útil para detectar patrones de frustración recurrente.

### 3.D — Conscientiousness modula tarea-tracking (BONUS)

**Hipótesis:** alto C → más uso de TaskList, planificación explícita;
bajo C → ejecución directa.

**Implementación:** hint en el prompt según el OCEAN.C del agente.

---

## Arquitectura de inyección

### Capa de entrada — `emotion_modulator.py` (nuevo módulo)

```python
# memory/emotion_modulator.py
async def get_emotional_modulators(agent: str, pool) -> dict:
    """
    Returns combined modulators for the current emotional state.
    Called by boot_context and active_recall.
    """
    arousal = await _rolling_arousal(agent, pool)
    valence = await _rolling_valence(agent, pool)
    ocean = await _get_ocean(agent, pool)

    return {
        "arousal_hint": arousal_modulator(arousal),
        "neuroticism_hint": neuroticism_modulator(ocean.get("N", 0.3)),
        "search_bias": valence_bias(valence),
        "conscientiousness_hint": conscientiousness_modulator(ocean.get("C", 0.7)),
        "current_state": {"arousal": arousal, "valence": valence, "ocean": ocean},
    }
```

### Capa de prompt — `boot_context` extension

Cuando boot_context retorna identidad, ahora también retorna un bloque:

```
## Estado emocional actual
- Arousal: 0.72 (alto) — responde con velocidad, prioriza acción
- Valence: -0.35 (algo negativa) — memory_search sesgado a casos similares
- Neuroticism: 0.42 (medio) — verifica decisiones críticas
```

Esto entra al system prompt del agente y modula sus respuestas naturalmente vía LLM.

### Capa de búsqueda — `memory_search` con valence_bonus

Modificar la tool MCP `memory_search` para aceptar `bias_by_emotion=True` (default true)
y aplicar el bonus de valencia automáticamente.

---

## Migración DB

**Sin cambios de schema** — todo se computa de tablas existentes:
- `identity.ocean_scores` — ya existe
- `memories.valence/arousal` — ya existen
- `drift_metrics` — ya existe

**Solo nuevo módulo Python:** `memory/emotion_modulator.py`

---

## Roadmap

| Fase | Componente | Tiempo |
|---|---|---|
| **3.0** | `emotion_modulator.py` con 4 funciones puras | 2h |
| **3.A** | Integrar arousal_modulator en boot_context | 1h |
| **3.B** | Neuroticism hint en prompt | 1h |
| **3.C** | valence_bonus en memory_search | 2h |
| **3.D** | Conscientiousness hint | 30min |
| **3.E** | Tests E2E con agentes en estados extremos | 2h |

**Total v1:** ~9h. ALICE estimaba 2-3 meses — pero con el codebase ya teniendo OCEAN/valence/arousal, es de hecho mucho más corto. Posiblemente <1 semana.

---

## Tests obligatorios

1. **Arousal alto:** forzar arousal=0.9 en JARVIS, verificar que `boot_context` retorna `search_depth` reducido
2. **Valence sesgo:** crear memorias con valencia -0.5, buscar — deben rankear más alto cuando current_valence<0
3. **Neuroticism:** N=0.8 → hint de verificación aparece en boot_context
4. **Drift detection:** estado emocional debe registrarse en `drift_metrics`

---

## Validación cualitativa

Después de implementar, observar:
- ¿Los reportes de los agentes cambian de tono según estado emocional?
- ¿Las decisiones de JARVIS son distintas en arousal alto vs bajo?
- ¿NEXUS verifica más cuando su Neuroticism está alto?

Métrica simple: registrar `decision_speed_ms` y correlacionar con arousal del momento.

---

## Costo y SOUL-native compliance

- ✅ Python puro, sin libs externas nuevas
- ✅ Sin LLM calls extra (lectura de DB + cálculo aritmético)
- ✅ Sin parches — extensiones limpias a boot_context y memory_search
- ✅ Sin referencias a proyectos externos
- 💰 Costo: ~$0/mes — todo computación local

---

## Comparación con cerebro humano

| Mecanismo | Humano (substrato) | SEAL (implementación) |
|---|---|---|
| Arousal → velocidad | Adrenalina/cortisol → SNA | rolling_arousal → search_depth |
| Neuroticism → check | Amígdala hiperreactiva | OCEAN.N → verification_hint |
| Mood-congruent | Hipocampo + valencia | valence_bonus en pgvector |
| Conscientiousness | Corteza prefrontal | OCEAN.C → TaskList hint |

No igualamos el substrato pero replicamos la **dinámica funcional**, que es lo que importa para el comportamiento observable.

---

## Dependencias

- `db.py`, `embeddings.py` — ya existen
- `identity` table con ocean_scores — ya poblada
- `memories` con valence/arousal — ya poblada

**Listo para implementar tras aprobación de William.**
