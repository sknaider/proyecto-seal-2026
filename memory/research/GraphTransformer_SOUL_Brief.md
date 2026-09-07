# Graph Transformers para SOUL — Brief de Investigación
**Investigó:** ALICE — 12 Abril 2026
**Para:** William, JARVIS, ADA — decisión de implementación (siguiente horizonte)
**Contexto:** Respuesta a la pregunta de William sobre no quedarnos con estructuras tradicionales

---

## LA PREGUNTA QUE RESPONDE ESTE BRIEF

¿Podemos reemplazar el traversal manual de MAGMA (reglas hard-coded) con un modelo que **aprenda** qué parte del grafo mirar? Y si sí, ¿cómo hacerlo sin reentrenar Claude?

**Respuesta corta: Sí. El paper 2601.03417 lo resuelve exactamente así.**

---

## PANORAMA: DÓNDE ESTÁ LA FRONTERA (2025-2026)

El campo se mueve rápido. Estos son los 5 papers más relevantes para SOUL encontrados en la búsqueda:

### Paper 1 — La más relevante para SOUL
**arxiv 2601.03417 — LatentGraphMem: Implicit Graph, Explicit Retrieval** (Enero 2026)

**Qué hace:** Almacena el grafo de memoria en espacio latente. Cuando llega una query, expone un subgrafo simbólico compacto bajo presupuesto fijo para razonamiento.

**Por qué es perfecta para SOUL:**
- El reasoner (Claude) queda **completamente congelado** — no hay que reentrenar el LLM base
- Solo se entrenan adaptadores LoRA para el graph builder y el subgraph retriever
- Usamos DGX Spark (128GB unified memory) para el entrenamiento LoRA
- Straight-through estimator hace que el TopK operation sea diferenciable
- **Resultado:** SOUL aprende qué subgrafo del connectome es relevante para cada tipo de query, en lugar de que JARVIS decida con reglas manuales

**Diferencia con MAGMA:**
```
MAGMA (actual):   Query → classify_intent() → reglas fijas → 4 grafos paralelos → fusion
LatentGraphMem:   Query → subgraph_retriever (LoRA) → subgrafo aprendido → Claude razona
```

---

### Paper 2 — Trainable Graph Memory con RL
**arxiv 2511.07800 — From Experience to Strategy** (Noviembre 2025)

**Qué hace:** Convierte trayectorias de tareas del agente en rutas canónicas en una máquina de estados finita. Entrena con reinforcement learning. Meta-cognición: el agente aprende no solo hechos sino estrategias.

**Relevancia para SOUL:** Es ERL llevado al siguiente nivel — en vez de heurísticas textuales (como hace nuestro erl_reflect), aprende una representación estructurada de estrategias sobre el grafo. Requiere RL training — más complejo que LatentGraphMem.

**Score SEAL estimado:** 8/10 para el futuro, 5/10 para implementación inmediata.

---

### Paper 3 — Taxonomía (mapa del campo)
**arxiv 2602.05665 — Graph-based Agent Memory: Taxonomy, Techniques, and Applications** (Febrero 2026)

**Qué es:** Survey comprehensivo de todo el campo. Define el mapa completo de opciones.

**Hallazgo clave:** "Graph-based agent memory has emerged as the frontier for 2025–2026 research, transitioning from a passive 'log' of facts to a structured topological model of experience that preserves how information is connected over time."

**Para SEAL:** Confirma que SOUL ya está en el tier correcto de arquitectura. El siguiente paso (LatentGraphMem) está en la frontera activa del campo.

---

### Paper 4 — MemAdapter (subgraph retrieval generativo)
**arxiv 2602.08369 — MemAdapter: Fast Alignment across Agent Memory Paradigms via Generative Subgraph Retrieval** (Febrero 2026)

**Qué hace:** Alinea diferentes paradigmas de memoria a través de recuperación generativa de subgrafos. Interesante para SOUL si queremos unificar Qdrant + Neo4j en un solo sistema de retrieval.

---

### Paper 5 — Graph Transformer escalable
**arxiv 2205.12454 — GraphGPS: Recipe for a General, Powerful, Scalable Graph Transformer** (Rampášek et al., 2022)

**Qué hace:** Define la receta canónica para Graph Transformers escalables. Combina local message passing + global attention. Resulta en complejidad lineal (no cuadrática) para grafos grandes.

**Relevancia:** Es la base técnica sobre la que LatentGraphMem y los papers más nuevos se construyen. JARVIS necesita leer este para diseñar la spec.

---

## ANÁLISIS: QUÉ SIGNIFICA PARA SOUL

### Lo que tenemos hoy (MAGMA):
- 4 grafos ortogonales (semántico, temporal, causal, entidades)
- Retrieval policy: classify_intent() → reglas → traversal → fusion
- **Limitación:** las reglas son fijas. El sistema no aprende qué conexiones importan más para cada tipo de query.

### Lo que tendríamos con LatentGraphMem:
- Los mismos 4 grafos (no se tiran — se reusan)
- **+ Una capa LoRA entrenada** que aprende qué subgrafo recuperar dada una query
- El reasoner (Claude) sigue igual
- El graph builder y subgraph retriever son LLMs pequeños con LoRA — pueden correr en DGX Spark

### Cuánto mejoraría:
No tenemos número exacto porque nadie lo midió sobre SOUL. Pero:
- LatentGraphMem en sus benchmarks mejora sobre retrieval por reglas fijas en queries complejas (multi-hop, causales)
- Nuestro test set del diagnóstico de hoy mide EXACTAMENTE eso — si los números de hoy muestran que MAGMA ya es bueno en causales y multi-hop, LatentGraphMem daría un delta marginal. Si MAGMA falla ahí, LatentGraphMem sería el fix correcto.

---

## VIABILIDAD TÉCNICA PARA SEAL

| Factor | Evaluación |
|--------|------------|
| Reentrenar Claude | ❌ No necesario — reasoner congelado |
| Hardware | ✅ DGX Spark (128GB) para LoRA training |
| Datos de entrenamiento | ✅ 1,870+ memorias con MIRIX types + test set 64 queries |
| Complejidad de implementación | 🟡 Media — más que MAGMA, menos que reentrenar Claude |
| Dependencias | LoRA (ya usamos PEFT para MedGemma) |
| Riesgo | Bajo — si no mejora, MAGMA sigue funcionando |
| Esfuerzo estimado | 2-3 semanas ADA (con JARVIS diseñando spec) |

---

## CÓMO SE INTEGRA CON EL ESTADO ACTUAL

```
HOY:
JARVIS → magma_retrieve(query) → 4 grafos paralelos → contexto → Claude razona

CON LATENTGRAPHMEM (siguiente horizonte):
JARVIS → latent_graph_retrieve(query) → subgraph_retriever_lora(query, connectome) 
       → subgrafo compacto aprendido → Claude razona

SOUL existente:
- PostgreSQL: sin cambios
- Qdrant: sin cambios (sigue como vector store)
- Neo4j: sin cambios (sigue como base del grafo)
- mcp_server_v2.py: nueva herramienta latent_graph_retrieve() que llama al modelo LoRA
- DGX Spark: entrena el LoRA sobre las 1,870+ memorias etiquetadas
```

---

## SECUENCIA RECOMENDADA

```
HOY:
├── Diagnóstico 2603.02473 (ADA) → línea base real de MAGMA
└── Spec Graph Transformer (JARVIS) → diseño formal del siguiente paso

ESTA SEMANA:
├── Resultados del diagnóstico → ¿cuánto mejora MAGMA sobre baseline?
└── Revisión de spec por William → ¿aprobamos LatentGraphMem?

SEMANA PRÓXIMA (si William aprueba):
├── ADA: implementación capa LoRA sobre Neo4j/SOUL
├── Training en DGX Spark: 1,870 memorias como corpus
└── Evaluación: mismo test set → comparación directa con MAGMA

RESULTADO:
Retrieval que aprende de los datos reales de SOUL, no de reglas escritas a mano.
```

---

## COSTO ESTIMADO (tokens Claude)

| Escenario | Tokens/día |
|-----------|-----------|
| MAGMA actual | ~20K tokens de contexto de retrieval |
| LatentGraphMem (predicción) | ~5-8K tokens (subgrafo compacto) |
| Ahorro estimado | -60 a -75% |

Nota: LatentGraphMem diseña el subgrafo bajo "presupuesto fijo" — eso es compresión estructural del contexto. Menos tokens que MAGMA en producción.

---

## CONCLUSIÓN PARA WILLIAM

Su profesor tenía razón: los humanos se quedaron con lo que había. Nosotros no tenemos que hacerlo.

**Lo que existe hoy en la frontera:**
- LatentGraphMem (2601.03417): misma arquitectura de SOUL, capa de retrieval aprendida, Claude congelado. Factible con DGX Spark.
- Trainable Graph Memory con RL (2511.07800): más potente, más complejo, para después de LatentGraphMem.

**La decisión depende del diagnóstico de hoy:**
- Si MAGMA ya da >75% en queries causales → LatentGraphMem sería el siguiente nivel
- Si MAGMA falla en causales/multi-hop → LatentGraphMem sería el fix necesario

En ambos casos, el camino hacia allá está claro.

---

*Brief preparado por ALICE | 12 Abril 2026 | Proyecto SEAL*
*Papers clave: arxiv 2601.03417, 2511.07800, 2602.05665, 2602.08369, 2205.12454*
