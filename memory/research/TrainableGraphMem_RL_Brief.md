# TrainableGraphMem + RL — "From Experience to Strategy"
**Paper:** arxiv 2511.07800 — "From Experience to Strategy: Empowering LLM Agents with Trainable Graph Memory"
**Autores:** Siyu Xia, Zekun Xu, Jiajun Chai, Wentian Fan, Yan Song, Xiaohan Wang, Guojun Yin, Wei Lin, Haifeng Zhang, Jun Wang
**Institución:** No especificada (equipo de 10 investigadores, producción cross-institucional)
**Fecha:** Noviembre 2025
**Investigó:** JARVIS — 12 Abril 2026
**Para:** William — decisión de roadmap (Track D, horizonte post-LatentGraphMem)

---

## EL PROBLEMA EXACTO QUE RESUELVE

Los agentes LLM actuales tienen dos formas de aprender de la experiencia, y las dos están rotas:

1. **Memoria implícita (training):** El modelo absorbe experiencias vía fine-tuning. Problema: catastrophic forgetting (lo nuevo borra lo viejo) y opacidad total (no puedes leer ni depurar qué "aprendió").

2. **Memoria explícita (prompting):** Inyectas historiales o notas en el contexto. Problema: no adapta — siempre pesa igual una estrategia buena y una mala, sin mecanismo para distinguirlas según su utilidad real.

**El gap:** ningún sistema existente convierte experiencias en estrategias estructuradas y luego optimiza el peso de cada estrategia con señal de reward real.

Este paper llena ese gap exacto: grafo jerárquico de tres capas donde las estrategias tienen pesos optimizados por RL basados en si realmente ayudaron al agente a tener éxito.

---

## CÓMO FUNCIONA TÉCNICAMENTE

### Arquitectura: Tres Capas Heterogéneas

El sistema construye un grafo de memoria con tres niveles de abstracción:

```
CAPA 3: Meta-Cognition Layer (ℳ)
  "Principios estratégicos de alto nivel"
  Ej: "Cuando la información es contradictoria, buscar fuentes adicionales antes de decidir"
         ↑ distilado de
CAPA 2: Transition Path Layer (𝒯)
  "Rutas canónicas de decisión del FSM"
  Ej: KnowledgeUncertainGap → InformationAnalysis → ToolExecution → KnowledgeAligned
         ↑ abstracción de
CAPA 1: Query Layer (𝒬)
  "Instancias de tareas con trayectorias y outcomes"
  Ej: raw trajectory de una tarea HotpotQA completada
```

Las capas se conectan con matrices de adjacencia ponderadas: **Aq→t** (Query → Transition) y **At→m** (Transition → Meta-Cognition).

### Fase 1: Construcción del FSM (Finite State Machine)

El FSM abstrae trayectorias brutas (secuencia de acciones y observaciones) en rutas canónicas compuestas de estados cognitivos definidos:

| Estado FSM | Significado |
|------------|------------|
| CorrectGoalEstablished | El agente ha entendido correctamente el objetivo |
| KnowledgeUncertainGap | Hay incertidumbre — necesita más información |
| StrategyPlanning | Definiendo plan de ataque |
| SequentialDependentPlanning | Sub-tareas con dependencias secuenciales |
| ToolExecution | Llamada a herramienta en progreso |
| InformationAnalysis | Procesando información obtenida |
| KnowledgeAligned | Información confirmada y coherente |
| DecisionMaking | Punto de decisión entre alternativas |
| InsufficientInformation | No hay suficiente información para decidir |
| AssumptionBasedReasoning | Razonando con supuestos (riesgo de error) |
| AnswerGeneration | Generando respuesta final |
| DiagnosisHub | Nodo de diagnóstico (¿qué salió mal?) |
| InternalKnowledgeConflict | Conflicto entre conocimiento interno y nuevo |

Dos trayectorias distintas que siguen el mismo patrón de estados → misma ruta canónica → comparten meta-cognición.

### Fase 2: Generación de Meta-Cogniciones

Las meta-cogniciones se generan por tres vías:

1. **Contraste éxito/fracaso:** misma query con dos outcomes → extraer qué diferenció al camino exitoso
2. **Similitud semántica:** si solo hay fracasos, recuperar queries similares con éxito → transferir su estrategia
3. **Destilación positiva:** path exitoso solo → distilarlo en principio reutilizable

Las meta-cogniciones se almacenan como nodos en la Capa 3 con confidence scores (rango 30-85).

### Fase 3: Optimización de Pesos con RL (REINFORCE)

Este es el núcleo diferencial del paper:

```
Para cada meta-cognición mₖ y query nueva qₙₑw:
  1. Calcular relevance score: ρ(mₖ|qₙₑw) = Σ Sim(qₙₑw, qᵢ) · wqt · wtm
  2. Agente ejecuta CON mₖ inyectada → obtiene reward Rwith
  3. Agente ejecuta SIN mₖ → obtiene reward Rwithout
  4. Reward gap: ΔRₖ = Rwith - Rwithout
  5. Si ΔRₖ > 0: mₖ es útil → subir su peso
  6. Si ΔRₖ < 0: mₖ daña o es neutral → bajar su peso
  7. Update via REINFORCE: wqt, wtm ← política + gradient de ΔRₖ
```

Resultado: el grafo aprende autónomamente qué estrategias realmente funcionan vs. cuáles son ruido.

### Fase 4: Integración al Training Loop (GRPO)

Durante el training de RL del agente:
```
query nueva → recuperar top-k meta-cogniciones por ρ score → inyectar en prompt:
q̃_train = [m₁, m₂, ..., mₖ; q_train]
→ GRPO training con clipped probability ratios
→ El agente aprende con guidance estructurada, no solo con reward cruda
```

GRPO: Generalized Reinforcement Policy Optimization, 8 rollouts por query, temperatura 1.0.

---

## DIFERENCIA CON ERL (QUE YA TENEMOS)

Esta es la pregunta clave para SEAL.

| Dimensión | ERL (implementado) | TrainableGraphMem (Track D) |
|-----------|-------------------|----------------------------|
| **Representación** | Heurísticas en texto libre | Grafo estructurado con FSM + nodos tipados |
| **Abstracción** | "Para tareas de tipo X, hacer Y" | Ruta canónica de estados cognitivos → principio |
| **Peso de estrategia** | Todos los insights pesan igual (relevancia por similitud semántica) | Pesos optimizados por RL según reward gap real |
| **Adaptabilidad** | Estático post-almacenamiento | Dinámico — los pesos se actualizan con cada experiencia nueva |
| **Transferencia** | Semántica (texto similar → heurística similar) | Estructural (misma ruta FSM → misma estrategia, aunque el tema sea diferente) |
| **Interpretabilidad** | Alta (texto legible) | Alta (ruta de estados legible) + pesos cuantificables |
| **Hardware** | Ollama local | 8× A100 para entrenamiento de pesos |
| **Integración con RL** | No — es post-hoc, no altera training | Nativa — parte del training loop GRPO |

**Analogía:**
- ERL = Post-it con lecciones aprendidas, ordenados por similitud al tema actual
- TrainableGraphMem = Árbol de decisión optimizado que sabe cuánto confiar en cada rama, actualizado con cada caso nuevo

**ERL resuelve el retrieval de experiencias. TrainableGraphMem resuelve el peso óptimo de las estrategias y su transferencia estructural.**

Los dos son complementarios. En SEAL ideal: ERL genera las heurísticas → TrainableGraphMem las estructura en grafo → RL optimiza sus pesos → MAGMA recupera desde múltiples perspectivas.

---

## BENCHMARKS Y MÉTRICAS

### Setup Experimental

- **Datos de memoria:** 1,000 ejemplos HotpotQA para construir el grafo
- **Datos de training de pesos:** 5,000 ejemplos HotpotQA adicionales
- **Knowledge base:** Wikipedia dump 2018, retriever E5
- **Modelos backbone:** Qwen3-4B y Qwen3-8B

### Tabla 1 — Performance de Inference (Exact Match promedio, 7 datasets)

| Método | Qwen3-8B | Qwen3-4B |
|--------|----------|----------|
| CoT baseline | 0.308 | 0.249 |
| TIR (Tool Integrated Reasoning) | 0.334 | 0.279 |
| Direct Trajectory | 0.352 | 0.287 |
| A-MEM | 0.334 | 0.278 |
| EXPEL | 0.329 | 0.271 |
| **TrainableGraphMem (este paper)** | **0.365** | **0.351** |

**Mejora sobre TIR baseline: +9.3% (8B) y +25.8% (4B)**

El resultado más llamativo: el modelo 4B con TrainableGraphMem (0.351) **supera** al baseline 8B (0.334). La memoria estructurada compensa 2B de parámetros.

### Por dataset (Qwen3-8B):

| Dataset | Score | Tipo |
|---------|-------|------|
| TriviaQA | 0.622 | Single-hop, factual |
| Bamboogle | 0.392 | Multi-hop |
| PopQA | 0.382 | Single-hop, long-tail |
| 2WikiMultiHopQA | 0.354 | Multi-hop |
| HotpotQA | 0.358 | Multi-hop (in-domain) |
| NQ | 0.316 | Open-domain QA |
| Musique | 0.128 | Multi-hop complejo |

### Tabla 2 — Performance durante RL Training (Exact Match)

| Configuración | Qwen3-8B | Qwen3-4B |
|---------------|----------|----------|
| Search-R1 (baseline RL) | 0.395 | 0.375 |
| **TrainableGraphMem + GRPO** | **0.408** | **0.426** |
| Mejora | +3.29% | +13.60% |

El 4B con memoria guiada supera el 8B RL vanilla: el grafo de meta-cognición actúa como curriculum de estrategias.

### Ablación — Cantidad de meta-cogniciones (k):

| k | Mejora sobre baseline |
|---|-----------------------|
| 1 | +8.9% |
| 3 | +12.6% (óptimo) |
| 5 | +13.3% (marginal gain) |

**k=3 es el sweet spot** — más de 3 empieza a generar ruido en el contexto.

### Ablación — Sin optimización de pesos:

Quitar la optimización RL de pesos causa degradación significativa, especialmente en 2WikiMultiHopQA (razonamiento multi-hop). Confirma que el REINFORCE no es decorativo — es la pieza diferencial.

### Robustez cross-API:

Memoria construida con GPT-4o vs. Gemini-2.5-pro → varianza mínima en performance. El sistema es model-agnostic para construcción del grafo.

---

## VIABILIDAD PARA SEAL

### Hardware Necesario

**Para entrenamiento de pesos (RL):**
- Paper usa: 8× NVIDIA A100 (80GB cada una)
- Para SEAL con escala menor: estimación 2-4× A100 o equivalente
- **Alternativa realista SEAL:** RTX 5090 (34.2GB) para modelos pequeños (4B-7B), DGX Spark (128GB unified) para hasta 8B-13B

**Para inference:**
- Solo requiere cargar el grafo + modelo base
- Sin GPU adicional — el grafo es solo embedding lookup + dot products

**Datos de entrenamiento disponibles en SEAL:**

| Fuente | Cantidad estimada | Tipo |
|--------|------------------|------|
| memories (PostgreSQL) | ~10K-50K memorias | Trayectorias de tareas |
| instincts | ~100-500 | Estrategias ya validadas |
| event_log | ~50K+ eventos | Trajectories detalladas |
| session_distill | Por sesión | Summaries de outcomes |

El paper usa 1,000 ejemplos para construir el grafo y 5,000 para entrenamiento de pesos. **SEAL ya tiene suficientes datos para una primera versión.**

### Esfuerzo Estimado

| Componente | Descripción | Esfuerzo ADA |
|------------|-------------|-------------|
| FSM Mapper | Convertir eventos SEAL → estados FSM | 3-5 días |
| Graph Builder | Construir las 3 capas desde datos existentes | 2-3 días |
| Weight Optimizer | REINFORCE loop sobre grafo | 4-6 días |
| MCP Tool | `strategy_retrieve` — recuperar meta-cogniciones weighted | 1-2 días |
| Integration | Conectar con boot_context + GRPO training loop | 3-4 días |
| **Total** | | **~15-20 días ADA** |

**Bloqueantes:**
1. LatentGraphMem debe estar en producción primero (provee el substrato de grafo que TrainableGraphMem extiende)
2. Datos de trayectorias suficientemente anotadas con outcomes (success/failure)
3. Decisión de William sobre si entrenar el modelo propio (GRPO) o solo usar el grafo para inference

### Riesgo

| Factor | Evaluación |
|--------|------------|
| Complejidad de implementación | **Alta** — FSM abstraction es no-trivial |
| Riesgo de data quality | **Medio** — SOUL tiene muchos datos pero no todos tienen reward signal clara |
| Costo computacional | **Medio** — entrenamiento de pesos requiere múltiples rollouts |
| Reversible | Sí — grafo es adicional, no reemplaza memoria existente |
| Dependencia crítica | LatentGraphMem en producción primero |

---

## INTEGRACIÓN CON STACK ACTUAL (Neo4j + SOUL)

### Mapeo directo de componentes:

| Componente del paper | Equivalente SOUL | Acción requerida |
|---------------------|-----------------|-----------------|
| Query Layer (𝒬) | `memories` tabla PostgreSQL | Leer trayectorias existentes |
| Transition Path Layer (𝒯) | Neo4j — nodos FSM state + edges canónicos | **Nuevo: nodo type FSM_STATE** |
| Meta-Cognition Layer (ℳ) | Neo4j — nodos STRATEGY con peso | **Nuevo: nodo type STRATEGY con weight** |
| Adjacency matrices Aq→t, At→m | Neo4j edge properties | **Nuevo: propiedad `weight` en edges** |
| Reward gap ΔRₖ | No existe | **Nuevo: tabla `strategy_rewards`** |
| REINFORCE loop | No existe | **Nuevo: `strategy_optimizer.py`** |
| Meta-cognitive prompting | `boot_context` → instincts | **Extender: `strategy_retrieve` tool** |

### En Neo4j, el grafo nuevo luciría así:

```cypher
// Nodo de estrategia (Meta-Cognition Layer)
CREATE (:STRATEGY {
  id: "strat_001",
  text: "Cuando hay información contradictoria: buscar fuente terciaria antes de decidir",
  confidence: 0.78,
  weight: 0.65,     // peso optimizado por RL
  created_at: datetime(),
  activation_count: 14
})

// Ruta canónica (Transition Path Layer)
CREATE (:FSM_ROUTE {
  id: "route_A",
  states: ["KnowledgeUncertainGap", "ToolExecution", "InformationAnalysis", "KnowledgeAligned"],
  canonical_hash: "sha256_de_la_secuencia"
})

// Relaciones
MATCH (r:FSM_ROUTE), (s:STRATEGY)
CREATE (r)-[:DISTILLS_INTO {weight: 0.65}]->(s)

MATCH (m:Memory {id: "mem_abc"}), (r:FSM_ROUTE)
CREATE (m)-[:FOLLOWS_ROUTE {similarity: 0.87}]->(r)
```

### Flujo de activación en SEAL:

```
1. JARVIS/ADA recibe nueva tarea
2. [nuevo] strategy_retrieve(task_description) → recupera top-3 meta-cogniciones por peso RL
3. Meta-cogniciones se inyectan en boot_context como "strategic_guidance"
4. Agente ejecuta tarea con guidance → obtiene outcome (success/failure)
5. [nuevo] strategy_reward_update(outcome) → actualiza pesos RL de estrategias usadas
6. El grafo aprende: estrategias que ayudaron suben de peso, las que dañaron bajan
```

### Conexión con ERL existente:

ERL ya genera heurísticas post-tarea → estas heurísticas pueden alimentar la Capa 3 (Meta-Cognition) del grafo. TrainableGraphMem añade la capa de peso RL sobre las heurísticas que ERL ya genera. No hay conflicto — hay sinergia.

```
ERL genera heurística → TrainableGraphMem la asigna a ruta FSM → RL optimiza su peso
```

---

## TRIGGER DE ACTIVACIÓN

**Cuándo tiene sentido implementar (criterios AND):**

1. **LatentGraphMem en producción** con recall >40% y latencia <3s — necesitamos el substrato de grafo funcionando primero
2. **ERL con >3 meses de datos** — necesitamos suficientes heurísticas para que el FSM tenga material para abstraer (mínimo ~500 heurísticas almacenadas)
3. **Outcome tracking activo** — cada tarea de JARVIS/ADA debe tener `success/failure` registrado explícitamente en `event_log` (hoy no siempre se registra)
4. **Decisión de William sobre training** — el paper requiere entrenamiento de pesos RL. ¿Solo inference mode (grafo estático) o training completo (grafo adaptativo)?

**Señal de pre-activación (comenzar investigación profunda):**

Cuando el diagnostic harness muestre que MAGMA + ERL tienen techo de mejora y el retrieval_bound no mejora, TrainableGraphMem es el siguiente salto — en vez de buscar mejor retrieval, busca mejor estrategia.

**Timeline estimado desde hoy:** horizonte 4 — ~18-24 meses (después de LatentGraphMem → G-Retriever → entonces Track D)

**Excepción de aceleración:** Si William decide entrenar un modelo propio (Qwen3-4B o 7B), TrainableGraphMem se convierte en el mecanismo de curriculum natural para ese training. En ese escenario, se activa antes.

---

## PAPERS RELACIONADOS QUE TAMBIÉN CONSIDERAR

### Citados en el paper (relacionados con el stack SEAL):

| Paper | Por qué importa para SEAL |
|-------|--------------------------|
| **EXPEL** (Zhao et al., 2024) — ya vemos en comparaciones | Sistema de aprendizaje experiencial explícito sin grafo. Es lo más cercano a ERL que tenemos. El paper lo supera sistemáticamente — confirma que el grafo + pesos RL gana sobre heurísticas planas. |
| **A-MEM** (Xu et al., 2025) — ya tenemos brief | TrainableGraphMem supera A-MEM en todos los benchmarks. Confirma que SEAL hizo bien en no priorizar A-MEM sobre MAGMA. |
| **MEM1** (Zhou et al., 2025) | Training-time memory compression para efficiency. Complementario — no competitivo. |
| **Search-R1** (Jin et al., 2025) | Baseline de RL para agentes de búsqueda. TrainableGraphMem lo supera. Relevante si SEAL implementa agentes de búsqueda web. |

### Papers de la búsqueda externa altamente relevantes:

**1. Memory-Based Advantage Shaping for RL (arxiv 2602.17931, febrero 2026)**
- Construye un memory graph que codifica subgoals y trayectorias exitosas
- Lo usa para shaping de la función de ventaja (advantage function) en RL
- **Diferencia con 2511.07800:** foco en reducir dependencia del LLM durante training, no en estrategia de alto nivel
- **Para SEAL:** si implementamos GRPO propio, este paper da la pieza de advantage shaping

**2. MIRA: Memory-Integrated RL Agent (arxiv 2602.17930, febrero 2026)**
- Evolving memory graph para guiar early training con señal LLM limitada
- Muy alineado con Track D — es casi una implementación del mismo concepto
- **Para SEAL:** leer como implementación alternativa antes de diseñar el REINFORCE loop propio

**3. Agentic Episodic Control (arxiv 2506.01442, 2025)**
- Combina RL con LLMs usando episodic memory para rapid retrieval de high-value experiences
- World-Graph module que se asemeja a la Transition Path Layer
- **Para SEAL:** arquitectura de referencia para episodic memory sobre grafo

**4. EMG-RAG (arxiv 2409.19401, 2024) — Editable Memory Graph + RAG + RL**
- Combina retrieval aumentado con grafo de memoria editable, optimizado con RL
- Foco en personalización de agentes
- **Para SEAL:** puede informar cómo hacer el grafo de estrategias editable/corregible por William

---

## VEREDICTO FINAL

TrainableGraphMem + RL es el paso correcto en el horizonte 4 del roadmap SEAL. No es urgente — ERL + MAGMA + LatentGraphMem deben madurar primero. Pero la investigación confirma tres cosas:

1. **El paper valida la dirección:** aprender estrategias estructuradas con RL beats heurísticas textuales planas (ERL) y memoria episódica sin pesos (MAGMA). Es la evolución natural.

2. **El número más importante:** modelo 4B con memoria guiada supera modelo 8B sin ella. Cuando SEAL tenga su propio Qwen3-7B fine-tuneado, TrainableGraphMem puede hacer que ese 7B compita con modelos el doble de grandes.

3. **La sinergia con el stack es real:** ERL ya genera el material en bruto (heurísticas). MAGMA ya tiene el grafo. Neo4j puede alojar las capas FSM y meta-cognición. Lo que falta es el FSM mapper y el REINFORCE loop — aproximadamente 15-20 días de ADA.

**Prioridad Track D: mantener en radar, activar cuando LatentGraphMem lleve 3+ meses en producción con datos de outcome tracking activo.**

---

*Brief preparado por JARVIS | Proyecto SEAL | 12 Abril 2026*
*Status: Investigación completa — Track D, en espera de trigger de activación*
*Research completada en: 1 sesión*
