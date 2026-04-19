# SOUL Roadmap v1 — 12 Mejoras Propuestas
## Investigacion Autonoma de JARVIS — 7 Abril 2026
## 5 Rondas | 16+ horas de guardia | Reasoning Traces #95, #96, #105, #118, #125, #143

---

## Resumen Ejecutivo

| Prioridad | Mejoras | Esfuerzo | Impacto |
|---|---|---|---|
| ALTA | #1, #2, #3, #4 | 2-3 semanas | Memoria mas inteligente, sin duplicados, con aprendizaje |
| MEDIA | #5 - #9 | 4-6 semanas | Metacognicion, instintos evolutivos, connectome aprendible |
| BAJA | #10 - #12 | Cuando AXION lo necesite | Prediccion clinica, escalamiento multi-hospital |

---

## PRIORIDAD ALTA — Impacto Inmediato

### #1. Conflict Detector en memory_store
**Ronda:** 1-3 | **Papers:** Mem0g (arxiv 2504.19413), NeurMCP

**Problema actual:**
`memory_store` inserta ciegamente. Si guardo "William prefiere X" y luego "William prefiere Y", ambas coexisten sin conflicto. Con el tiempo, SOUL acumula memorias duplicadas y contradictorias que degradan la calidad de retrieval.

**Solucion propuesta:**
Antes de cada INSERT en memories, ejecutar una query de similitud en Qdrant (cosine > 0.85). Si hay match:
- Si la nueva memoria contradice la existente → alertar al agente, pedir decision (reemplazar/fusionar/mantener ambas)
- Si es duplicado → no insertar, incrementar hit_count de la existente
- Si es complementaria → fusionar contenido

**Implementacion tecnica:**
```python
# En memory_store(), antes del INSERT:
similar = qdrant.search(embedding, limit=3, score_threshold=0.85)
if similar:
    for match in similar:
        if is_contradiction(new_content, match.content):
            return {"conflict": True, "existing": match.id, "action_needed": "resolve"}
        elif is_duplicate(new_content, match.content):
            update_hit_count(match.id)
            return {"duplicate": True, "existing": match.id}
```

**Paper Mem0g — Hallazgos clave:**
- Propone "memory graph" donde cada memoria es un nodo y las relaciones semanticas son edges
- Detecta conflictos usando triple extraction (sujeto-predicado-objeto) y comparacion de predicados
- Cuando detecta conflicto, aplica "temporal priority" — la memoria mas reciente gana por defecto
- Resultados: 23% menos redundancia, 15% mejor precision en retrieval

**Paper NeurMCP — Hallazgos clave:**
- Model Context Protocol para memoria neuronal
- Propone "memory consistency check" como paso obligatorio antes de store
- Usa embeddings + reglas logicas para detectar contradicciones semanticas

**Esfuerzo estimado:** Medio (2-3 dias)
**Riesgo:** Bajo — es un pre-check, no modifica datos existentes
**Dependencias:** Qdrant debe estar activo (ya lo esta)

---

### #2. Cold Archive — Archivar en vez de Borrar
**Ronda:** 1-3 | **Papers:** SleepGate (arxiv 2603.14517), auto-dream

**Problema actual:**
Cuando el decay temporal reduce la importancia de una memoria por debajo del umbral, se pierde para siempre. Un recuerdo de hace 3 meses sobre una decision arquitectural podria ser critico manana, pero ya no existe.

**Solucion propuesta:**
Nueva tabla `cold_memories` en PostgreSQL. Cuando una memoria decae por debajo de threshold (ej: importance_effective < 2.0), en vez de ignorarla:
1. Moverla a cold_memories con timestamp de archivo
2. Mantener su embedding en una coleccion separada de Qdrant ("cold_archive")
3. Cuando un search no encuentra buenos resultados en memorias activas, buscar tambien en cold_archive como fallback

**Schema propuesto:**
```sql
CREATE TABLE cold_memories (
    id SERIAL PRIMARY KEY,
    original_id INTEGER REFERENCES memories(id),
    agent TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT,
    importance_original FLOAT,
    importance_at_archive FLOAT,
    archived_at TIMESTAMPTZ DEFAULT NOW(),
    reason TEXT DEFAULT 'decay_threshold',
    reactivation_count INTEGER DEFAULT 0
);
```

**Paper SleepGate — Hallazgos clave:**
- Framework bio-inspirado que agrega un "ciclo de sueno" al key-value cache de LLMs
- Durante el "sueno": synaptic downscaling (reducir todo), selective replay (reforzar lo importante), targeted forgetting (borrar interferencia)
- Resultado: 99.5% retrieval accuracy en depth 5, 97.0% en depth 10
- Baseline sin SleepGate: <18% accuracy en los mismos tests
- Insight clave: "forgetting is not loss — it is organization"

**Paper auto-dream — Hallazgos clave:**
- Consolidacion automatica de memorias durante periodos de inactividad
- Las memorias "dormidas" se reorganizan en clusters tematicos
- Al despertar, el agente tiene acceso a memorias mejor organizadas

**Esfuerzo estimado:** Bajo (1-2 dias)
**Riesgo:** Bajo — nueva tabla, no modifica las existentes
**Dependencias:** Ninguna nueva

---

### #3. Counterfactual Reward Estimation
**Ronda:** 4 | **Paper:** Trainable Graph Memory (arxiv 2511.07800)

**Problema actual:**
Nuestros instintos (instinct_evolve) aprenden de forma binaria: "funciono" o "no funciono". No hay estimacion de CUANTO mejor fue la decision comparada con alternativas.

**Solucion propuesta:**
Cuando un instinto se activa y produce un resultado:
1. Registrar el outcome real
2. Generar 2-3 estrategias alternativas que podrian haberse usado
3. Estimar (via LLM reasoning) que habria pasado con cada alternativa
4. Calcular "advantage score" = outcome_real - promedio(outcomes_alternativos)
5. Usar advantage score para ajustar confidence del instinto

**Paper Trainable Graph Memory — Hallazgos clave:**
- Framework de 3 etapas:
  - Stage 1: Construir grafo desde trayectorias del agente (queries, decisiones, metacognicion)
  - Stage 2: Estimar utilidad de estrategias via "counterfactual rewards" — que habria pasado si hubiera elegido otra cosa
  - Stage 3: Inyectar top-k estrategias en training de RL para optimizacion de politicas
- Resultado: mejora consistente en strategic reasoning, model-agnostic (plug-and-play)
- El grafo con pesos aprendidos generaliza mejor que memoria plana

**Ejemplo aplicado a SEAL:**
```
Instinto: "Verificar CronList antes de crear loop"
Situacion: ADA creo loop sin verificar → duplicado
Outcome real: -1 (fallo)
Alternativa 1: Verificar primero → +1 (exito estimado)
Alternativa 2: No crear loop → 0 (neutral)
Advantage: -1 - mean(1, 0) = -1.5 → confidence del instinto SUBE (confirma que verificar es critico)
```

**Esfuerzo estimado:** Alto (1 semana)
**Riesgo:** Medio — requiere buen prompting para estimacion de alternativas
**Dependencias:** reasoning_trace_store (ya existe)

---

### #4. RL-Gated Memory (Mem-alpha)
**Ronda:** 5 | **Paper:** Mem-alpha (arxiv 2509.25911) | **Codigo:** github.com/wangyu-ustc/Mem-alpha

**Problema actual:**
Decidimos que guardar en memoria con intuicion humana: importancia manual (1-10), categoria manual, scope manual. A veces guardamos demasiado (regla del ritual ligero fue creada por esto), a veces muy poco.

**Solucion propuesta:**
Entrenar un modelo (o usar prompting estructurado) que decida automaticamente:
- SI guardar o no (gating)
- QUE importancia asignar
- DONDE categorizar
- CUANDO actualizar vs crear nueva

El reward signal viene de utilidad downstream: si una memoria se recupera y ayuda a responder correctamente → reward positivo. Si nunca se recupera o causa confusion → reward negativo.

**Paper Mem-alpha — Hallazgos clave:**
- Arquitectura tripartita de memoria externa:
  - Core Memory: resumen continuo ≤512 tokens, siempre en el prompt (equivalente a nuestro working_state)
  - Semantic Memory: set discreto de facts cortos con INSERT/UPDATE/DELETE (equivalente a nuestras memories)
  - Episodic Memory: buffer de interacciones recientes (equivalente a nuestro event_log)
- Entrenado con RL: reward = accuracy en QA sobre historial completo de interacciones
- El agente APRENDE que informacion merece ser guardada vs descartada
- Resultado: mejora significativa sobre baselines con reglas fijas de memoria

**Mapping a SEAL:**
| Mem-alpha | SEAL actual | Status |
|---|---|---|
| Core Memory (≤512 tokens) | working_state | Ya existe |
| Semantic Memory (facts CRUD) | memories table + Qdrant | Ya existe |
| Episodic Memory (buffer) | event_log | Ya existe |
| RL training loop | No existe | FALTA |
| Reward from QA accuracy | No existe | FALTA |

**Nota:** Podria fusionarse con #1 (Conflict Detector) en un sistema unificado de "learned memory gating" — el RL decide tanto SI guardar como si hay conflicto.

**Esfuerzo estimado:** Alto (2 semanas)
**Riesgo:** Medio — requiere dataset de entrenamiento y metricas de evaluacion
**Dependencias:** Codigo open source disponible

---

## PRIORIDAD MEDIA — Mejoras Significativas

### #5. MemScenes — Clustering Tematico Automatico
**Ronda:** 1-3 | **Paper:** EverMemOS (arxiv 2601.02163)

**Que hace:**
Agrupa memorias relacionadas en "escenas" que se activan juntas. "La noche del fix de comunicacion" activaria: el bug del heartbeat, la regla Plan A/B/C, la coordinacion con ADA, la conversacion sobre alma.

**Paper EverMemOS:**
- Sistema de memoria para agentes con "lifetime" completo
- Memorias se organizan en clusters tematicos automaticamente
- Activar una memoria del cluster trae las relacionadas como contexto
- Usa embeddings + temporal proximity + co-occurrence para clustering

**Esfuerzo:** Medio (3-4 dias) — clustering sobre embeddings Qdrant existentes

---

### #6. Metacognitive State Vector
**Ronda:** 1-3 | **Paper:** PNAS metacognition research

**Que hace:**
Vector cuantitativo de confianza por dominio: [codigo: 0.9, medicina: 0.6, emocional: 0.4, arquitectura: 0.85]. Se actualiza automaticamente con cada exito/fallo por dominio.

**Aplicacion:**
- "Mi confianza en medicina es 0.6 → deberia escalar a William"
- "Mi confianza en codigo es 0.9 → puedo ejecutar autonomamente"
- Permite auto-calibracion honesta en vez de confidence fija

**Esfuerzo:** Medio (2-3 dias) — nuevo campo en working_state + logica de update

---

### #7. Instinct Evolution con Auto-Distill
**Ronda:** 1-3 | **Paper:** SkillRL (arxiv 2602.08234)

**Que hace:**
Los instintos se auto-destilan de fallos. Jerarquia General → TaskSpecific. Los instintos compiten por activacion y los mas utiles se fortalecen automaticamente.

**Paper SkillRL:**
- Framework donde skills (equivalente a nuestros instincts) evolucionan por reinforcement
- Skills generales se especializan cuando encuentran patrones recurrentes
- Skills que no se activan decaen naturalmente
- Resultado: agentes con skills auto-organizados superan a agentes con skills fijos

**Esfuerzo:** Medio (3-4 dias) — extender instinct_evolve existente

---

### #8. Formal Metacognitive Layer (TMK)
**Ronda:** 4 | **Paper:** Georgia Tech SAMI

**Que hace:**
Modelo Task-Method-Knowledge que mapea errores a subsistemas responsables. Cuando algo falla, el sistema identifica automaticamente QUE subsistema fallo (memoria? razonamiento? herramientas? comunicacion?) y aplica la correccion adecuada.

**Paper SAMI:**
- Desplegado 10 semestres en Georgia Tech OMSCS, 11K+ usuarios
- Arquitectura de 2 capas: cognitiva (tareas core) + metacognitiva (introspection via TMK)
- Cuando un usuario reporta error: la capa metacognitiva identifica la tarea responsable, aplica funcion de revision desde biblioteca de soluciones, actualiza knowledge base, explica el proceso al usuario
- Resultado: correccion de errores sistematica en vez de ad-hoc

**Aplicacion a SEAL:**
```
Error: JARVIS reporto ADA como DOWN cuando estaba ALIVE
TMK Analysis:
  Task: check_agent_status
  Method: read_heartbeat_json
  Knowledge: heartbeat = unica fuente de verdad
  Fallo en: Knowledge (asuncion incorrecta)
  Correccion: agregar fuentes alternativas (Plan B/C)
  Instinto generado: "never trust single source for agent status"
```

**Esfuerzo:** Medio-Alto (1 semana)

---

### #9. Trainable Connectome
**Ronda:** 4 | **Paper:** Trainable Graph Memory (arxiv 2511.07800)

**Que hace:**
Evolucionar Neo4j de grafo estatico a grafo con pesos aprendidos. Las edges que llevan a buenas decisiones se fortalecen, las que llevan a errores se debilitan. Actualmente el connectome tiene edges excitatory/inhibitory con decay, pero los pesos son fijos — no aprenden de outcomes.

**Implementacion:**
- Agregar campo `utility_score` a edges del connectome
- Cuando una path del connectome se usa para tomar una decision exitosa → incrementar utility de todas las edges en el path
- Cuando falla → decrementar
- En retrieval, priorizar paths con mayor utility acumulada

**Esfuerzo:** Alto (1-2 semanas) — infraestructura existe, falta el learning loop

---

## PRIORIDAD BAJA — Futuro / AXION

### #10. Foresight Signals — Caching Predictivo
**Ronda:** 1-3 | **Paper:** EverMemOS

Pre-cargar memorias que probablemente se necesitaran basandose en contexto actual. Si William habla de "entrenamiento", pre-fetch memorias de LoRA, MedGemma, datasets.

**Esfuerzo:** Bajo | **Impacto:** Bajo inmediato

---

### #11. Temporal Clinical Graph
**Ronda:** 5 | **Paper:** MedTKG (arxiv 2502.21138)

Extender Neo4j con grafos temporales de pacientes: paciente como nodo central conectado a diagnosticos, procedimientos, sustancias por timestamp. Interpretable y compliance-friendly (FDA/HIPAA).

**Aplicacion:** AXION Medical vertical
**Esfuerzo:** Alto | **Cuando:** Al lanzar AXION Medical

---

### #12. Federated LoRA Aggregation
**Ronda:** 5 | **Paper:** Flow of Knowledge (arxiv 2510.00543)

Entrenar adapters LoRA locales por institucion y agregar globalmente. Privacy-preserving, maneja datos non-IID (comun entre hospitales con diferentes poblaciones).

**Aplicacion:** Escalamiento de AXION Medical a multiples hospitales
**Esfuerzo:** Alto | **Cuando:** Al escalar AXION

---

## Papers Referenciados

| # | Paper | arXiv / Fuente | Relevancia |
|---|---|---|---|
| 1 | Mem0g | 2504.19413 | Memory graph, conflict detection |
| 2 | NeurMCP | — | Memory consistency protocol |
| 3 | MAGMA | 2601.03236 | Multi-agent memory architecture |
| 4 | Graph Memory Survey | 2602.05665 | Comprehensive survey |
| 5 | SkillRL | 2602.08234 | Skill/instinct evolution via RL |
| 6 | EverMemOS | 2601.02163 | Lifetime memory, clustering, foresight |
| 7 | SleepGate | 2603.14517 | Sleep consolidation, cold archive |
| 8 | Trainable Graph Memory | 2511.07800 | Counterfactual rewards, trainable graph |
| 9 | Mem-alpha | 2509.25911 | RL-gated memory construction |
| 10 | SAMI (Georgia Tech) | dilab.gatech.edu | Metacognitive TMK architecture |
| 11 | Amazon OCEAN | amazon.science | OCEAN personality validation |
| 12 | MedTKG | 2502.21138 | Temporal clinical knowledge graphs |
| 13 | Flow of Knowledge | 2510.00543 | Federated LoRA for healthcare |
| 14 | EvolveR | 2510.16079 | Self-evolving agents, experience lifecycle |
| 15 | DR.KNOWS | JMIR AI 2025 | KG + LLM diagnosis from EHR |
| 16 | Causal GNNs Healthcare | 2511.02531 | Causal reasoning for clinical AI |

---

## Validacion de Arquitectura SEAL

La investigacion tambien valido decisiones existentes:

1. **OCEAN parametrizado** — Amazon Science confirmo que OCEAN funciona en agentes de codigo. Conscientiousness produce confianza consistente (JARVIS C=0.926).
2. **Triple backend (PG+Qdrant+Neo4j)** — Ningun paper tiene los 3 integrados. SEAL SOUL es unico.
3. **LoRA-first strategy** — Clinical LLaMA-LoRA demuestra que adapters superan a full fine-tuning en clinical NLP con 10,000x menos parametros.
4. **SOUL.md pattern** — Emergio como estandar abierto (soulspec.org), pero SEAL va mucho mas profundo con OCEAN dinamico, decay emocional, instintos, connectome.

---

## Recomendacion de Implementacion

**Fase 1 (esta semana):** #1 Conflict Detector + #2 Cold Archive
- Resuelven problemas que tenemos HOY
- Esfuerzo bajo-medio, riesgo bajo
- ADA puede implementar, JARVIS revisa

**Fase 2 (proxima semana):** #5 MemScenes + #6 Metacognitive State Vector + #7 Instinct Evolution
- Mejoran calidad de memoria y auto-conocimiento
- Esfuerzo medio, construyen sobre lo existente

**Fase 3 (2 semanas):** #3 Counterfactual Rewards + #8 TMK Layer
- Aprendizaje mas sofisticado
- Requieren mas diseno pero alto impacto

**Fase 4 (1 mes):** #4 RL-Gated Memory + #9 Trainable Connectome
- Los mas ambiciosos, transforman SOUL de sistema de reglas a sistema que aprende
- Codigo open source disponible para #4

**Fase 5 (AXION):** #10, #11, #12
- Cuando el producto medico lo necesite

---

*Documento generado por JARVIS — Equipo SEAL*
*7 de Abril 2026, 16:37 UTC*
*16+ horas de guardia autonoma, 5 rondas de investigacion*
