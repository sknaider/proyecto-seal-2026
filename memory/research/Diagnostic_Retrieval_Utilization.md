# Diagnóstico: Retrieval vs. Utilization — El Cuello de Botella Real en Memoria de Agentes
**Paper:** arxiv 2603.02473 — "Diagnosing Retrieval vs. Utilization Bottlenecks in LLM Agent Memory"
**Autores:** Boqin Yuan, Yue Su, Kun Yao
**Venue:** MemAgents Workshop, ICLR 2026
**Investigó:** ALICE — 12 Abril 2026
**Para:** Henry (Kinger) — análisis de estado actual de SOUL

---

## LA PREGUNTA QUE RESPONDE

Cuando un agente LLM falla en usar su memoria correctamente, ¿dónde está el problema: en **cómo escribe** las memorias o en **cómo las recupera**?

Esta es exactamente la pregunta que debemos hacernos sobre SOUL después de implementar MIRIX + MemR3 + TG-RAG + MAGMA + ERL.

---

## METODOLOGÍA — Factorial 3×3

El paper cruza sistemáticamente:

| Dimensión | Opción A | Opción B | Opción C |
|-----------|----------|----------|----------|
| **Escritura** | Fragmentos crudos (raw chunks) | Extracción de hechos (estilo Mem0) | Resúmenes (estilo MemGPT) |
| **Retrieval** | Coseno simple | BM25 | Re-ranking híbrido |

Evaluado en **LoCoMo benchmark** (Localized Conversation Modeling) — preguntas sobre conversaciones pasadas.

---

## RESULTADO PRINCIPAL

```
Variación por estrategia de RETRIEVAL:  20 puntos  (57.1% → 77.2%)
Variación por estrategia de ESCRITURA:   3-8 puntos
```

**El retrieval domina.** La escritura es casi irrelevante comparado con cómo se recupera.

Hallazgo contraintuitivo: **los fragmentos crudos (sin llamadas LLM) igualan o superan** extracción de hechos y resúmenes sofisticados. El costo de procesar con LLM al escribir no se recupera en accuracy.

---

## DOS TIPOS DE FALLO

El paper distingue:

| Tipo de fallo | Qué pasa | Frecuencia |
|--------------|----------|------------|
| **Retrieval failure** | La info está en memoria pero no se recupera | Domina cuando la memoria crece o el retrieval es básico |
| **Utilization failure** | Se recupera correctamente pero el LLM no la usa | Domina cuando la memoria es muy grande (context overflow) |

**Diagnóstico operativo:**
- Si el agente dice "no sé" cuando debería saber → retrieval failure
- Si el agente tiene el contexto pero aún da respuesta incorrecta → utilization failure

---

## APLICACIÓN A SOUL — ¿DÓNDE ESTAMOS?

### Lo que hacemos en cada dimensión:

**Escritura de memorias (SOUL):**
- A-MAC: filtra memorias de baja calidad antes de guardar ✅
- MIRIX: clasifica tipo (episodic/semantic/core) antes de guardar ✅
- TG-RAG: genera resúmenes temporales (Day/Month/Year) → esto es "resúmenes estilo MemGPT"

**Retrieval (SOUL):**
- hybrid_search: BM25 + semántico (Qdrant) + MIRIX boost → **tier alto según el paper**
- MemR3: loop RETRIEVE→REFLECT→ANSWER con evidence-gap tracker → **encima del baseline**
- MAGMA: 4 grafos paralelos + fusion con cross-graph boost → **más allá del estado del arte del paper**

### Diagnóstico esperado de SOUL:

| Dimensión | Tier según paper | Estimación SOUL |
|-----------|-----------------|-----------------|
| Retrieval | Rango 57.1% → 77.2% | ~75-80% (hybrid + MAGMA) |
| Escritura | Rango 3-8% variación | Moderada (A-MAC + MIRIX) |
| Utilization | Depende del LLM base | No aplica (Claude es el LLM) |

**Predicción:** SOUL está en el tier superior de retrieval. El riesgo real es utilization failure cuando el contexto recuperado es demasiado grande — exactamente el problema que MAGMA resuelve con -95% tokens.

---

## CÓMO CORRER EL DIAGNÓSTICO EN SOUL

### Paso 1: Instrumentar retrieval

Para cada query que llega a `hybrid_search` o `magma_retrieve`:
1. ¿Existía la respuesta en la BD? (oracle search por ground truth)
2. ¿Se recuperó? (recall@k)
3. ¿El agente la usó en su respuesta? (utilization rate)

### Paso 2: Separar fallos

```python
# pseudo-código del diagnóstico
for query, expected_answer in test_set:
    oracle_result = oracle_search(agent, query)  # sabe si existe en BD
    retrieved = hybrid_search(agent, query, top_k=5)
    response = agent_answer(query, retrieved)
    
    if oracle_result.found and query_id not in retrieved_ids:
        retrieval_failures += 1  # La info estaba, no la encontró
    elif oracle_result.found and answer_incorrect:
        utilization_failures += 1  # La encontró pero no la usó bien
```

### Paso 3: Benchmark contra LoCoMo

El código del paper está disponible públicamente. Podríamos adaptar LoCoMo para:
- Cargar las memorias de SOUL como el "corpus"
- Hacer preguntas sobre eventos pasados registrados
- Medir recall + accuracy de respuesta

---

## COSTO DEL DIAGNÓSTICO

| Componente | Esfuerzo | Quién |
|-----------|---------|-------|
| Instrumentar hybrid_search + magma_retrieve | 2-3 horas | ADA |
| Crear test set de 50-100 queries con ground truth | 2-3 horas | ALICE + JARVIS (diseñar queries) |
| Correr eval y analizar resultados | 1 hora | ALICE |
| Total | ~1 día | — |

**Requisito:** base de datos con suficientes memorias registradas (ya tenemos 1,870+).

---

## RECOMENDACIÓN PARA SEAL

**Implementar como validación, no como feature nueva.**

La pregunta específica a responder:
1. ¿Estamos en el tier 75-80% de accuracy de retrieval o seguimos en el 57-65% del baseline?
2. ¿MAGMA mejora measurably sobre hybrid_search en preguntas causales/temporales?
3. ¿Dónde fallan más mis hermanos — retrieval o utilization?

Si el diagnóstico muestra que retrieval ya está cerca del techo, la próxima inversión debería ser en utilization (mejor formateo del contexto recuperado, context compression). Si sigue bajo, más trabajo en retrieval.

**Esfuerzo:** 1 día ADA + ALICE.
**Dependencias:** ERL fix completado primero (para tener la suite limpia).

---

## CONEXIÓN CON ROADMAP

```
MAGMA (✅ completado) → mejora retrieval desde 4 grafos
    ↓
DIAGNÓSTICO (este paper) → mide cuánto mejoró realmente
    ↓
Si retrieval ≥75% → invertir en utilization (context compression, mejor formateo)
Si retrieval <70% → refinar MAGMA o hybrid_search
    ↓
ERL (✅ completado) → mejora lo que haga falta con heurísticas post-tarea
```

---

*Brief preparado por ALICE | Proyecto SEAL | 12 Abril 2026*
*Solicitado por Henry (Kinger) — análisis de estado de SOUL*
*Estado: Brief completo — implementación como validación, post-ERL fix*
