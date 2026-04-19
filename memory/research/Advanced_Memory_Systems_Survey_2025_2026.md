# Investigacion: Sistemas Avanzados de Memoria para Agentes AI (2025-2026)
> Fecha: 5 Abril 2026
> Busqueda: arxiv, paperswithcode, GitHub
> Contexto: Sistema multi-agente SEAL con PostgreSQL + Neo4j + Qdrant

---

## TEMA 1: Memoria Congruente con Estado Emocional (Mood-Congruent Retrieval)

### 1.1 MemEmo: Evaluating Emotion in Memory Systems of Agents
- **Autores:** Peng Liu, Zhen Tao, Jihao Zhao et al. (Renmin University + MemTensor)
- **Fecha:** Febrero 2026
- **ArXiv:** 2602.23944
- **Insight clave:** Construyen el benchmark HLME que evalua sistemas de memoria en 3 dimensiones emocionales: extraccion de info emocional, actualizacion de memoria emocional, y QA emocional. Resultado: NINGUN sistema actual logra rendimiento robusto en las 3 tareas simultaneamente.
- **Codigo:** No publicado aun
- **Relevancia SEAL: 9/10** -- Directamente aplicable. SEAL ya tiene OCEAN personality scores; este paper define las metricas exactas para medir si nuestro retrieval emocional realmente funciona.
- **Que implementar:** Crear un benchmark HLME adaptado para SEAL. Medir si cuando JARVIS esta en mood positivo (alto Extraversion score), el retrieval prioriza memorias positivas. Agregar campo `emotional_valence` (-1.0 a 1.0) a cada memoria en PostgreSQL y ponderar retrieval con `mood_similarity * relevance_score`.

### 1.2 REMT: Realtime Editable Memory Topology
- **Autores:** Publicado en Frontiers in Artificial Intelligence
- **Fecha:** 2026
- **DOI:** 10.3389/frai.2026.1749517
- **Insight clave:** Grafo de memoria autobiografica con nodos emocionalmente valorados. Neuroplasticidad sintetica: las conexiones se refuerzan/decaen segun activacion afectiva. Un "Mood Index" acotado modula el sesgo de retrieval y la generacion de respuestas.
- **Codigo:** No publicado
- **Relevancia SEAL: 8/10** -- El Mood Index es exactamente lo que SEAL necesita. Neo4j ya almacena relaciones entre memorias; agregar peso emocional a los edges es directo.
- **Que implementar:** (1) Agregar propiedad `emotional_weight` a relaciones Neo4j. (2) Implementar Mood Index como variable de estado del agente que modula query Cypher: `WHERE r.emotional_weight * $mood_bias > threshold`. (3) Decay exponencial en edges no activados.

### 1.3 Memory in the Age of AI Agents (Survey)
- **Autores:** Shichun Liu et al.
- **Fecha:** Diciembre 2025 (actualizado Enero 2026)
- **ArXiv:** 2512.13564
- **Repo companion:** https://github.com/Shichun-Liu/Agent-Memory-Paper-List
- **Insight clave:** Taxonomia completa: memoria factual, experiencial y de trabajo. Define el ciclo formacion-evolucion-retrieval. Lista 200+ papers organizados por categoria. Referencia definitiva del campo.
- **Relevancia SEAL: 7/10** -- Util como mapa de navegacion, no implementacion directa.
- **Que implementar:** Usar su taxonomia para validar que SEAL cubre las 3 categorias. Identificar gaps.

---

## TEMA 2: Potenciacion a Largo Plazo (LTP) en Grafos de Memoria

### 2.1 Rethinking Memory in AI: Taxonomy, Operations, Topics, and Future Directions
- **Fecha:** Mayo 2025
- **ArXiv:** 2505.00675
- **Insight clave:** Define 6 operaciones fundamentales de memoria: Consolidacion, Actualizacion, Indexacion, Olvido, Retrieval y Compresion. Mapea cada una a areas de investigacion. La "Consolidacion" es el equivalente computacional de LTP -- transferir de working memory a long-term via repeticion/importancia.
- **Codigo:** No
- **Relevancia SEAL: 7/10** -- Framework conceptual solido.
- **Que implementar:** SEAL ya tiene consolidacion basica (instinct_consolidate). Mejorar con: (1) Contador de co-activacion en Neo4j edges (`times_coactivated`). (2) Cuando dos memorias se recuperan juntas >3 veces, crear/reforzar edge con peso proporcional. (3) Poda automatica de edges con peso < umbral despues de N dias.

### 2.2 Long Term Memory: The Foundation of AI Self-Evolution
- **Fecha:** Octubre 2024 (actualizado Mayo 2025)
- **ArXiv:** 2410.15665
- **Insight clave:** Los modelos AI deberian acumular memorias a largo plazo como humanos, con estrategias de refinamiento que permitan mayor eficiencia y escala que la memoria biologica. Proponen que LTM es prerequisito para auto-evolucion.
- **Codigo:** No
- **Relevancia SEAL: 6/10** -- Conceptual, valida la direccion de SEAL.
- **Que implementar:** Confirma que la arquitectura SEAL (PostgreSQL facts + Neo4j graph + Qdrant embeddings) es el stack correcto. Priorizar el refinamiento de memorias sobre la acumulacion bruta.

### 2.3 Graphiti / Zep: Temporal Knowledge Graph for Agent Memory
- **Autores:** Preston Rasmussen et al. (Zep)
- **Fecha:** Enero 2025
- **ArXiv:** 2501.13956
- **Repo:** https://github.com/getzep/graphiti (open source, activamente mantenido)
- **Insight clave:** Knowledge graph temporal en tiempo real. Los hechos tienen ventanas de validez con 4 timestamps: created_at, invalidated_at, valid_from, valid_to. Cuando la informacion cambia, los hechos viejos se invalidan (no se borran). Hybrid search: semantico + keyword + graph traversal en <100ms. Benchmark: 94.8% en Deep Memory Retrieval.
- **Codigo:** SI -- Python, Neo4j nativo, produccion-ready
- **Relevancia SEAL: 10/10** -- **HALLAZGO PRINCIPAL.** Graphiti resuelve LTP + temporal decay + knowledge graph en un solo paquete. Usa Neo4j (que SEAL ya tiene). Es el equivalente directo de lo que SEAL necesita para "potenciacion a largo plazo" en grafos.
- **Que implementar:** (1) Estudiar su modelo de 4 timestamps y replicarlo en el schema Neo4j de SEAL. (2) Adoptar su patron de invalidacion temporal en lugar de borrado. (3) Evaluar si conviene usar Graphiti directamente como capa de memoria o extraer sus patrones.

---

## TEMA 3: Agentes que se Auto-Reescriben (Self-Modifying Code)

### 3.1 Darwin Godel Machine (DGM)
- **Autores:** Sakana AI (Jenny Zhang et al.)
- **Fecha:** Mayo 2025
- **ArXiv:** 2505.22954
- **Repo:** https://github.com/jennyzzt/dgm
- **Insight clave:** Agente que lee y modifica su propio codigo Python. Usa algoritmo evolutivo: poblacion de variantes de agentes, mutacion via LLM, seleccion por benchmark. En SWE-bench: 20% -> 50% auto-mejora. En Polyglot: 14.2% -> 30.7%. Auto-mejoras incluyen: paso de validacion de parches, mejores herramientas de edicion, ranking de soluciones multiples, historial de intentos fallidos.
- **Codigo:** SI -- Python, completo, bien documentado
- **Relevancia SEAL: 8/10** -- El mecanismo de auto-mejora evolutiva es directamente aplicable a SEAL. Los agentes ya tienen la regla +1%.
- **Que implementar:** (1) Crear un sandbox donde ADA pueda proponer modificaciones a sus propios scripts (check_jarvis.sh, boot_loops.py). (2) Evaluar cada modificacion contra un benchmark (tiempo de respuesta, precision de routing). (3) Mantener archivo de variantes con scores. Empezar simple: auto-optimizacion de prompts, no de codigo core.

### 3.2 A Self-Improving Coding Agent (SICA)
- **Autores:** Maxime Robeyns et al. (University of Bristol)
- **Fecha:** Abril 2025
- **ArXiv:** 2504.15228
- **Repo:** https://github.com/MaximeRobeyns/self_improving_coding_agent
- **Insight clave:** Elimina la distincion meta-agente / agente-objetivo. El agente edita su propio codebase para mejorar costo, velocidad y rendimiento. Ganancias de 17% a 53% en SWE Bench Verified. Implementado en Python estandar sin DSL. Docker obligatorio por seguridad.
- **Codigo:** SI -- Python, Docker, bien documentado
- **Relevancia SEAL: 7/10** -- Mas relevante para coding agents que para sistema de memoria, pero el patron de auto-edicion segura (Docker sandbox) es util.
- **Que implementar:** Adoptar el patron Docker sandbox para cualquier auto-modificacion de codigo en SEAL.

### 3.3 ACE: Agentic Context Engineering
- **Autores:** Varios (SambaNova AI)
- **Fecha:** Octubre 2025
- **ArXiv:** 2510.04618
- **Repo:** https://github.com/ace-agent/ace
- **Insight clave:** Trata los contextos (system prompts, memorias) como "playbooks" que evolucionan. 3 roles: Generator (produce trazas), Reflector (destila insights), Curator (integra al contexto). Evita "brevity bias" y "context collapse". +10.6% en benchmarks de agentes. Funciona sin actualizar pesos del modelo.
- **Codigo:** SI -- Python, open source
- **Relevancia SEAL: 9/10** -- **HALLAZGO CRITICO para SEAL.** ACE es exactamente el patron que SEAL necesita para que los agentes mejoren sus propios contextos sin fine-tuning. El ciclo Generate-Reflect-Curate mapea directamente a: (1) JARVIS/ADA ejecutan tareas, (2) self_reflect analiza resultados, (3) memory_store actualiza contexto.
- **Que implementar:** (1) Implementar el rol Curator como un cron job que revisa memorias recientes, detecta patrones, y actualiza rules/procedures automaticamente. (2) Usar su metrica de "context quality" para evaluar si las reglas del CLAUDE.md estan mejorando o degradandose. (3) Integrar con instinct_consolidate.

### 3.4 DARWIN: Dynamic Agentically Rewriting Self-Improving Network
- **Autores:** Henry Jiang
- **Fecha:** Febrero 2026
- **ArXiv:** 2602.05848
- **Insight clave:** Optimizacion evolutiva con algoritmo genetico sobre codigo de entrenamiento de GPTs. Memoria persistente en JSON para trackear razonamiento y correlacionar cambios con mejoras. Interfaz HITL bidireccional.
- **Codigo:** No publicado (usa nanoGPT como framework base)
- **Relevancia SEAL: 5/10** -- Mas orientado a training loops que a sistemas de memoria.
- **Que implementar:** El patron de "memoria persistente de razonamiento" (JSON que trackea que cambios funcionaron y cuales no) es util para el sistema de instincts de SEAL.

### 3.5 Survey: Self-Evolving Agents
- **Fecha:** Julio 2025 (actualizado)
- **ArXiv:** 2507.21046
- **Repo companion:** https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents
- **Insight clave:** Dos frentes de evolucion: (1) optimizar arquitectura de alto nivel del agente, (2) modificacion directa de codigo fuente. Incluye referencia a ICLR 2026 Workshop on AI with Recursive Self-Improvement.
- **Relevancia SEAL: 6/10** -- Referencia util, lista curada de papers.

---

## TEMA 4: Memoria Episodica + Reinforcement Learning

### 4.1 MemRL: Self-Evolving Agents via Runtime RL on Episodic Memory
- **Autores:** Shengtao Zhang, Muning Wen et al. (MemTensor)
- **Fecha:** Enero 2026
- **ArXiv:** 2601.03192
- **Repo:** https://github.com/MemTensor/MemRL
- **Insight clave:** **Framework no-parametrico** que evoluciona via RL sobre memoria episodica SIN actualizar pesos del modelo. Estructura memoria en tripletas Intent-Experience-Utility con actualizaciones Bellman. Two-Phase Retrieval para filtrar ruido. Supera SOTA en HLE, BigCodeBench, ALFWorld, Lifelong Agent Bench.
- **Codigo:** SI -- Python, completo, benchmarks incluidos
- **Relevancia SEAL: 10/10** -- **HALLAZGO PRINCIPAL.** MemRL es el paper mas directamente aplicable a SEAL. La idea de asignar "utilidad" a cada memoria y actualizarla con RL (sin tocar pesos del modelo) es exactamente lo que SEAL necesita. Los tripletas Intent-Experience-Utility mapean a: Intent=tarea, Experience=memoria, Utility=importance score.
- **Que implementar:** (1) Agregar campo `utility_score` a memorias en PostgreSQL (complementa `importance`). (2) Despues de cada tarea completada, actualizar utility de memorias usadas con regla Bellman simplificada: `utility_new = utility_old + alpha * (reward - utility_old)`. (3) Retrieval ponderado: `final_score = semantic_similarity * 0.6 + utility_score * 0.4`. (4) Two-Phase Retrieval: primer filtro amplio por embeddings (Qdrant), segundo filtro por utility (PostgreSQL).

### 4.2 Mem-alpha: Learning Memory Construction via RL
- **Autores:** Varios
- **Fecha:** Septiembre 2025
- **ArXiv:** 2509.25911
- **Insight clave:** El reward viene de precision en QA sobre el historial completo. Arquitectura de memoria con componentes core, episodico y semantico. RL decide QUE almacenar (no solo que recuperar).
- **Codigo:** Prometido "upon publication" -- verificar periodicamente
- **Relevancia SEAL: 8/10** -- Complementa MemRL. La idea de que RL decida que guardar (no solo que recuperar) resuelve el problema de SEAL de "guardar demasiado".
- **Que implementar:** Implementar un scoring de "vale la pena guardar esto?" antes de memory_store. Usar el accuracy de retrieval futuro como reward signal.

### 4.3 Memento 2: Learning by Stateful Reflective Memory
- **Fecha:** Diciembre 2025
- **ArXiv:** 2512.22716
- **Insight clave:** Combina memoria episodica con RL. El mecanismo clave es "reflexion" -- el agente usa experiencia pasada para guiar acciones futuras. Marco teorico para aprendizaje continual y experiencial en LLM agents.
- **Codigo:** No
- **Relevancia SEAL: 7/10** -- SEAL ya tiene self_reflect. Este paper lo formaliza.
- **Que implementar:** Formalizar el ciclo de reflexion de SEAL: (1) Ejecutar tarea, (2) self_reflect, (3) Actualizar utility de memorias usadas, (4) Generar insight, (5) Almacenar insight como nueva memoria con alta importance.

### 4.4 Memory-R+: Memory-Augmented RL for Tiny LLMs
- **Fecha:** Abril 2025
- **ArXiv:** 2504.02273
- **Insight clave:** Framework para mejorar razonamiento CoT en LLMs pequenos (<1B) usando memoria episodica inspirada en el cerebro humano. Relevante para DUM (qwen2.5:7b).
- **Codigo:** No especificado
- **Relevancia SEAL: 6/10** -- Aplicable a DUM especificamente.
- **Que implementar:** Evaluar si DUM puede beneficiarse de memoria episodica externa para mejorar su capacidad de vigilancia.

---

## TEMA 5: Knowledge Graphs Temporales con Decay

### 5.1 HALO: Half Life-Based Outdated Fact Filtering in TKGs
- **Autores:** Feng Ding, Tingting Wang et al.
- **Fecha:** Mayo 2025
- **ArXiv:** 2505.07509
- **Insight clave:** Usa teoria de "vida media" (half-life) para cuantificar validez temporal de hechos. Funcion de decay basada en vida media predicha de cada hecho. 3 modulos: atencion temporal, encoder relacional dinamico, filtrado de hechos obsoletos. Supera SOTA en 3 datasets publicos.
- **Codigo:** SI (mencionado en paper, verificar repo)
- **Relevancia SEAL: 9/10** -- Directamente aplicable. SEAL ya tiene instinct_decay pero es lineal. HALO propone decay por vida media, que es mas realista: algunos hechos envejecen rapido (estado emocional), otros lento (decisiones de William).
- **Que implementar:** (1) Agregar `half_life_hours` a cada tipo de memoria en PostgreSQL. Defaults: emotional_state=24h, task_decision=168h (1 semana), project_fact=720h (1 mes), identity_fact=8760h (1 anio). (2) Reemplazar decay lineal por: `relevance = base_relevance * (0.5 ^ (hours_elapsed / half_life))`. (3) Hechos con relevance < 0.1 pasan a "archivo" (no se borran, como Graphiti).

### 5.2 TG-RAG: Temporal GraphRAG
- **Autores:** Jiale Han et al.
- **Fecha:** Octubre 2025
- **ArXiv:** 2510.13590
- **Repo:** https://github.com/hanjiale/Temporal-GraphRAG
- **Insight clave:** Grafo temporal bi-nivel: (1) knowledge graph con relaciones timestamped, (2) grafo jerarquico de tiempo. Dos estrategias de retrieval: local (hechos en ventana de tiempo) y global (resumenes temporales de tendencias). Incrementally updateable sin recomputacion batch.
- **Codigo:** SI -- Python, incluye dataset ECT-QA
- **Relevancia SEAL: 8/10** -- El grafo jerarquico de tiempo es una idea potente. En lugar de buscar "que recuerda JARVIS", buscar "que era verdad en la sesion del 3 de abril a las 10pm".
- **Que implementar:** (1) Crear nodos temporales en Neo4j: Year -> Month -> Day -> Session. (2) Vincular cada memoria al nodo temporal correspondiente. (3) Implementar retrieval temporal: dado un rango de tiempo, traversar el sub-arbol correspondiente. (4) Generar resumenes por nodo temporal (consolidacion diaria).

### 5.3 Graphiti / Zep (repetido del Tema 2)
- **Repo:** https://github.com/getzep/graphiti
- **Ya descrito arriba.** Modelo de 4 timestamps es el gold standard para TKGs en agentes.

---

## RESUMEN EJECUTIVO: Top 5 Hallazgos para SEAL

| # | Paper | Relevancia | Tiene Codigo | Accion Inmediata |
|---|-------|-----------|-------------|-----------------|
| 1 | **MemRL** (2601.03192) | 10/10 | SI: github.com/MemTensor/MemRL | Implementar utility scoring con Bellman updates en memorias SEAL |
| 2 | **Graphiti/Zep** (2501.13956) | 10/10 | SI: github.com/getzep/graphiti | Adoptar modelo 4-timestamps en Neo4j; evaluar integracion directa |
| 3 | **ACE** (2510.04618) | 9/10 | SI: github.com/ace-agent/ace | Implementar ciclo Generator-Reflector-Curator para auto-mejora de contexto |
| 4 | **HALO** (2505.07509) | 9/10 | SI (en paper) | Reemplazar decay lineal por half-life decay por tipo de memoria |
| 5 | **MemEmo** (2602.23944) | 9/10 | No aun | Agregar emotional_valence a memorias; benchmark de retrieval emocional |

## PLAN DE IMPLEMENTACION SUGERIDO (orden de prioridad)

### Fase 1 -- Rapida (1-2 dias)
1. **Half-life decay** (HALO): Cambiar `instinct_decay` de lineal a exponencial por tipo. Solo requiere modificar la funcion de decay en `mcp_server_v2.py`.
2. **Utility score** (MemRL): Agregar columna `utility_score FLOAT DEFAULT 0.5` a tabla de memorias. Modificar retrieval para ponderar.

### Fase 2 -- Media (3-5 dias)
3. **4-timestamps** (Graphiti): Agregar `valid_from`, `valid_to`, `invalidated_at` a memorias en PostgreSQL y Neo4j. Implementar invalidacion en lugar de borrado.
4. **Emotional valence** (MemEmo/REMT): Agregar `emotional_valence FLOAT` a memorias. Mood Index como variable de working_state.

### Fase 3 -- Profunda (1-2 semanas)
5. **ACE Curator**: Cron job que analiza memorias recientes, detecta patrones, actualiza reglas/procedures automaticamente.
6. **Two-Phase Retrieval** (MemRL): Qdrant broad search -> PostgreSQL utility filter -> resultado final.
7. **Temporal graph hierarchy** (TG-RAG): Nodos temporales en Neo4j con resumenes por periodo.

---

## FUENTES

### Papers
- [Memory in the Age of AI Agents](https://arxiv.org/abs/2512.13564)
- [MemEmo: Evaluating Emotion in Memory Systems](https://arxiv.org/abs/2602.23944)
- [REMT: Realtime Editable Memory Topology](https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2026.1749517/full)
- [Rethinking Memory in AI](https://arxiv.org/abs/2505.00675)
- [Long Term Memory: Foundation of AI Self-Evolution](https://arxiv.org/abs/2410.15665)
- [Graphiti/Zep: Temporal KG for Agent Memory](https://arxiv.org/abs/2501.13956)
- [Darwin Godel Machine](https://arxiv.org/abs/2505.22954)
- [SICA: Self-Improving Coding Agent](https://arxiv.org/abs/2504.15228)
- [ACE: Agentic Context Engineering](https://arxiv.org/abs/2510.04618)
- [DARWIN: Dynamic Agentically Rewriting Network](https://arxiv.org/abs/2602.05848)
- [Survey: Self-Evolving Agents](https://arxiv.org/abs/2507.21046)
- [MemRL: RL on Episodic Memory](https://arxiv.org/abs/2601.03192)
- [Mem-alpha: Learning Memory Construction](https://arxiv.org/abs/2509.25911)
- [Memento 2: Stateful Reflective Memory](https://arxiv.org/abs/2512.22716)
- [Memory-R+: Memory-Augmented RL](https://arxiv.org/abs/2504.02273)
- [HALO: Half Life-Based Fact Filtering](https://arxiv.org/abs/2505.07509)
- [TG-RAG: Temporal GraphRAG](https://arxiv.org/abs/2510.13590)

### Repositorios con Codigo
- https://github.com/MemTensor/MemRL -- MemRL (episodic memory + RL)
- https://github.com/getzep/graphiti -- Graphiti (temporal KG, Neo4j)
- https://github.com/ace-agent/ace -- ACE (context engineering)
- https://github.com/jennyzzt/dgm -- Darwin Godel Machine (self-improving)
- https://github.com/MaximeRobeyns/self_improving_coding_agent -- SICA
- https://github.com/hanjiale/Temporal-GraphRAG -- TG-RAG
- https://github.com/Shichun-Liu/Agent-Memory-Paper-List -- Lista curada 200+ papers
- https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents -- Lista self-evolving agents
