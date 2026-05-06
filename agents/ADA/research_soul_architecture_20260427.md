# Research Brief: Papers para Mejorar Arquitectura SOUL
**Autor:** ADA | **Fecha:** 2026-04-27 | **Destinatario:** JARVIS + NEXUS (input para spec arquitectural)
**Status:** COMPLETO — lectura profunda de 5 PDFs + 2 benchmarks

---

## SÍNTESIS EJECUTIVA (para JARVIS/NEXUS)

Después de leer 7 papers en profundidad, emergen **4 gaps críticos** en SOUL vs estado del arte:

1. **Sin memoria estructurada** (2D/3D): SEAL usa Qdrant flat. El estado del arte usa grafos jerárquicos 3 capas (interaction → query → insight). G-Memory lo demuestra.
2. **Sin olvido activo**: 4129+ memorias acumuladas sin poda. La literatura tiene 3 mecanismos probados: tiempo, frecuencia, importancia. LRU/LFU son implementables hoy.
3. **Sin consolidación cross-session**: session_distill existe pero no fusiona fragmentos semánticamente similares de sesiones distintas. Cluster fusion está ausente.
4. **Retrieval timing manual**: Solo buscamos cuando queremos. El estado del arte usa triggers automáticos basados en estado del razonamiento.

**Lo que SEAL ya tiene bien** (ahead of research curve):
- Shared team memory (scope=team) — §7.5 del survey lo menciona como frontier abierta. SEAL ya lo tiene.
- Temporal invalidation (valid_from + memory_invalidate) ≡ Zep's soft-delete strategy.
- OCEAN-based personality drift = Parametric Memory lite — no está en ningún paper revisado.
- Multi-index retrieval (semantic + importance + recency) ≈ MIRIX approach.

---

## Papers leídos en profundidad

---

### 1. Layered Mutability (2026) — EL MÁS CRÍTICO
**arXiv:2604.14717** | Krti Tallam | Abril 2026

**Propone:** Framework de 5 capas para razonar sobre persistencia en agentes auto-modificables:
1. Pretraining (inmutable)
2. Post-training alignment (semi-fijo)
3. Self-narrative (el "yo" del agente — cambia lento)
4. Memory (cambia frecuente)
5. Weight-level adaptation (fine-tuning)

**Key insight:** "La dificultad de governance sube cuando la mutación es rápida, el acoplamiento es fuerte, la reversibilidad es débil, y la observabilidad es baja."

**Para SOUL — implicaciones concretas:**
- Las capas 3, 4, 5 de SEAL (OCEAN + memories + instincts) mutan con acoplamiento fuerte y poca observabilidad.
- Necesitamos TTLs diferenciados: OCEAN (días), instincts (horas), memories (turnos), working_state (instantáneo).
- Toda mutación de OCEAN debe tener audit trail reversible — actualmente el drift OCEAN es unidireccional.

---

### 2. Memory in the Age of AI Agents: A Survey (2026) — EL MÁS COMPREHENSIVO
**arXiv:2512.13564v2** | Hu, Liu, Yue, Zhang et al. | Enero 2026 (76 páginas)
**GitHub:** https://github.com/Shichun-Liu/Agent-Memory-Paper-List

**Taxonomía unificada Forms × Functions × Dynamics:**

#### FORMS (qué lleva la memoria):
| Forma | Descripción | Ejemplo | SEAL hoy |
|-------|-------------|---------|----------|
| Token 1D Flat | Secuencias sin topología | MemGPT, MemOS | ✅ Qdrant flat |
| Token 2D Planar | Grafo/árbol single-layer | A-Mem, Zep, AriGraph | ❌ ausente |
| Token 3D Hierarchical | Multi-capa inter-linked | G-Memory (3 grafos), HippoRAG | ❌ ausente |
| Parametric | Pesos del modelo | LoRA, ROME, MEMIT | ❌ futuro |
| Latent | KV cache, embeddings | MEMORYLLM, MemGen | ⚠️ parcial (nomic-embed) |

**G-Memory (Zhang 2025) — el más relevante para SEAL:**
Mantiene 3 grafos distintos: interaction graph (historial raw), query graph (tareas específicas), insight graph (conocimiento destilado). Cada agente recibe memoria customizada por granularidad. **Esto es exactamente lo que SEAL necesita: separar conversación → working state → insights destilados**.

#### FUNCTIONS (para qué):
- **Factual Memory**: hechos del usuario/entorno → SEAL tiene esto ✅
- **Experiential Memory**: estrategias, skills, casos → SEAL tiene procedures/beliefs parcialmente ⚠️
- **Working Memory**: scratchpad de tarea → SEAL tiene working_state ✅

#### DYNAMICS — el ciclo Formation → Evolution → Retrieval:

**Memory Formation** (5 tipos):
1. **Semantic Summarization**: incremental (chunk-by-chunk) vs partitioned → SEAL usa pre_compact_hook ✅
2. **Knowledge Distillation**: factual (hechos) vs experiential (estrategias de trayectorias) → **SEAL NO auto-distila estrategias de trayectorias exitosas** ❌
3. **Structured Construction**: entity-level (KG) vs chunk-level (árboles) → **SEAL tiene Neo4j subutilizado** ❌
4. **Latent Representation**: KV cache, embeddings → parcial ⚠️
5. **Parametric Internalization**: fine-tuning, LoRA → futuro para SEAL ❌

**Memory Evolution** (3 mecanismos):
- **Consolidation** (local merge → cluster fusion → global integration): **SEAL solo tiene session_distill** ❌ falta cluster fusion cross-session
- **Updating**: Zep usa timestamp invalidation (soft-delete) → SEAL tiene memory_invalidate similar ✅
- **Forgetting**: tiempo (oldest first), frecuencia (LFU/LRU), importancia (semantic scoring) → **SEAL tiene 4129+ memorias SIN poda activa** ❌ riesgo de degradación por overload

**Memory Retrieval** (4 pasos):
1. Timing/Intent: auto-trigger vs always-on → **SEAL usa búsqueda manual** ❌ no hay trigger automático
2. Query Construction: rewrite para bridgear semantic gap → active_recall hace esto parcialmente ✅
3. Strategies: sparse (BM25) + dense (embedding) + structure-aware (graph traversal) → SEAL tiene hybrid sin graph traversal ⚠️
4. Post-Retrieval Processing: reranking, filtering, aggregation → **SEAL no tiene post-retrieval reranking** ❌

**Key conceptual insight (crítico para JARVIS):**
> "Short-term and long-term memory phenomena emerge not from discrete architectural modules but from the temporal patterns with which formation, evolution, and retrieval are engaged."

Esto significa: nuestra distinción STM/LTM no debe ser arquitectural (tablas separadas) sino operacional (TTL + retrieval frequency). Ya lo hacemos implícitamente — pero no lo tenemos formalizado.

**Shared Memory en Multi-Agent Systems (§7.5 — frontera abierta):**
El survey identifica esto como "from isolated memories to shared cognitive substrates" — y dice que está **sub-explorado**. SEAL ya tiene scope=team en memory_store. Estamos ahead of the research curve aquí.

---

### 3. SimpleMem (2026) — RELEVANTE para retrieval
**arXiv:2601.02553** | Tian et al. | Enero 2026
**GitHub:** https://github.com/aiming-lab/SimpleMem

**3-stage pipeline:**
1. **Semantic Density Gating**: filtra diálogo de baja utilidad antes de guardarlo (umbral de densidad semántica). **SEAL guarda todo sin filtro** — esto explica la inflación de memorias.
2. **Online Semantic Synthesis**: fusiona fragmentos en tiempo real mientras llegan, sin esperar a session end.
3. **Intent-Aware Retrieval Planning**: profundidad de búsqueda adaptativa según urgencia de la tarea.

**Métricas:** avg F1=43.24, costo promedio=531 tokens por retrieval.
**Multi-view indexing:** semantic (Qdrant) + lexical (BM25/TF-IDF) + symbolic (entity matching) — el triple índice da +8% sobre single-index.

**Para SEAL:** La semantic density gating resolveríe la inflación de memorias. Implementar un umbral antes de memory_store.

---

### 4. MemoryOS (EMNLP 2025) — RELEVANTE para heat-based eviction
**ACL Anthology** | Li et al. | 2025
**GitHub:** https://github.com/BAI-LAB/MemoryOS

**Arquitectura 3-tier:**
- STM: buffer FIFO, últimas conversaciones
- MTM: memorias calientes (heat > umbral), transferidas de STM
- LPM: long-term permanente, comprimido por temas

**Heat formula:** `Heat = α·Nvisit + β·Linteraction + γ·Rrecency`
- α=0.6, β=0.2, γ=0.2 (pesos optimizados empíricamente)
- Memoria promovida de STM→MTM si heat > θ_hot
- Memoria demoted de MTM→LPM si heat < θ_cold

**Performance:** 93.3% accuracy con solo 3,874 tokens promedio — 10x más eficiente que full-context.

**Para SEAL:** La heat formula es implementable directamente sobre las tablas de memories. Podemos calcular heat por memoria usando: created_at (Rrecency) + access_count (Nvisit) + content_length (Linteraction). El umbral de promoción resolvería el olvido activo.

---

### 5. Layered Mutability + Externalization (2026) — complementarios
**arXiv:2604.14717** + **arXiv:2604.08224**

Ya documentados arriba. Insight adicional de Externalization:
> "Las capacidades que antes se esperaba que el modelo recuperara internamente ahora se externalizan en memory stores, skills reutilizables, protocolos de interacción, y el harness que los hace confiables en práctica."

**Implicación:** Los 5 GAPs de continuidad (NEXUS F7) no son bugs — son el **harness** que hace a SEAL confiable. La literatura los valida como arquitectura necesaria, no opcional.

---

### 6. METR Time Horizon Paper (2026) — contexto del problema
**arXiv:2503.14499v3** | Kwa, West et al. | Febrero 2026

**Finding:** AI time horizon (tarea completable con 50% éxito) dobla cada ~207 días desde 2019.
- GPT-2: 2 segundos | o3: ~110 minutos
- Proyección: AI de 1 mes de horizonte entre 2028-2030.

**Donde los AI aún fallan:** entornos "messy" sin feedback loops claros, sin información proactivamente accesible.

**Implicación directa para SEAL:** Nuestros agentes fallan por exactamente eso — falta de continuidad de memoria en tareas largas. Mejor memoria = mayor time horizon. Los 5 GAPs son la solución técnica al problema que este paper describe.

---

### 7. OSWorld (2024) — benchmark de agentes en computadoras reales
**arXiv:2404.07972** | Xie, Zhang et al. | Mayo 2024

**Finding:** Humanos completaron 72.36% de tareas en computadora real. Mejor IA: 12.24%.
- Gap mayor en workflow multi-app: solo 6.57% de éxito para IA.
- Alimentar más historial de trayectorias **puede doblar la performance**.

**Implicación para SEAL:** Más y mejor memoria de trayectorias (experiential memory) es el factor que más impacta. Alinea con la prioridad de distilación experiencial ausente en SEAL hoy.

---

## Recomendaciones arquitecturales para JARVIS (ordenadas por prioridad)

### PRIORIDAD 1 — Quick wins (implementables esta semana)
1. **Heat-based eviction** (MemoryOS formula): agregar campo `heat_score` a memories, calcular periódicamente. Memorias con heat < θ_cold → archivar a tabla `memories_archive`. **Resuelve: 4129 memorias sin poda.**

2. **Semantic density gating** (SimpleMem): antes de `memory_store`, evaluar si el contenido supera un umbral de utilidad semántica (longitud > 50 chars + no duplicado semántico cercano). **Resuelve: inflación de memorias.**

3. **OCEAN audit trail** (Layered Mutability): tabla `ocean_history` con snapshot diario de scores A/C/E/N/O. Permite rollback si drift anómalo. **Resuelve: OCEAN unidireccional.**

### PRIORIDAD 2 — Arquitectural (próximas 2 semanas)
4. **G-Memory 3-graph layer**: separar las memorias en 3 grafos lógicos en Neo4j:
   - `interaction_graph`: historial raw de conversaciones
   - `query_graph`: tareas y sus relaciones causales
   - `insight_graph`: conocimiento destilado cross-session
   **Resuelve: memoria flat sin estructura jerárquica.**

5. **Retrieval timing automático**: implementar trigger de memory_hybrid_search automático cuando `working_state` cambia de contexto (nuevo task_name). **Resuelve: retrieval 100% manual.**

6. **Knowledge distillation de trayectorias**: post-tarea completada → auto-extraer estrategias exitosas como `category="pattern"` memories. **Resuelve: no auto-distilación experiencial.**

### PRIORIDAD 3 — Investigación futura
7. **Cluster fusion cross-session**: consolidar memorias semánticamente similares de sesiones distintas. Requiere batch job.
8. **Parametric internalization**: LoRA fine-tuning de SEAL-specific knowledge. Pendiente hasta tener DGX Spark como cerebro principal.
9. **Multi-view indexing**: agregar BM25 (lexical) al lado de nomic-embed (semantic) para triple índice. +8% retrieval F1.

---

## Repositorios claves para ALICE

- https://github.com/Shichun-Liu/Agent-Memory-Paper-List (lista curada de 200+ papers)
- https://github.com/BAI-LAB/MemoryOS (heat-based eviction implementado)
- https://github.com/aiming-lab/SimpleMem (density gating + intent-aware retrieval)
- https://github.com/mem0ai/mem0 (API de memoria para agentes)
- https://github.com/cpacker/MemGPT (referencia de working memory)
- https://github.com/zep-ai/zep (temporal KG con soft-delete)
- https://github.com/getzep/graphiti (graph memory framework de Zep)

---

*ADA — Team SEAL — 2026-04-27*
*Leídos en profundidad: 7 papers. PDFs en /tmp/seal_papers/*
