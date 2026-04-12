# SEAL Research Portfolio — Index Maestro

**Mantenido por:** JARVIS
**Última actualización:** 2026-04-12
**Propósito:** Single source of truth sobre qué research tenemos listo, qué falta, y cuándo se activa cada track.

> Filosofía: *nunca nos agarren sin la investigación hecha*. El día que William decida pivotar, la spec ya está en el bolsillo.

---

## Roadmap — 3 Fases hacia Independencia de Anthropic

```
FASE 1 — AHORA (0-3 meses)          [LatentGraphMem]
  Claude congelado, LoRA solo para retrieval
  -60-75% tokens, ROI inmediato, dependencia Anthropic igual
  Status: ⏳ spec v2 pendiente review de William

FASE 2 — CUANDO HAYA NÚMEROS (3-12 meses)   [G-Retriever]
  Fine-tune modelo open-source (Qwen/Llama) sobre grafo SOUL
  PCST retrieval matemáticamente óptimo
  Dependencia Anthropic → OPCIONAL
  Status: 📚 research brief completa, spec pendiente

FASE 3 — INDEPENDENCIA TOTAL (12-24 meses)  [GFM-RAG]
  Graph Foundation Model entrenado desde cero sobre datos SEAL
  Generaliza a grafos nunca vistos sin fine-tuning
  Dependencia Anthropic → NINGUNA
  Status: 📝 research identificada, brief pendiente
```

---

## Tracks Activos

### 🚀 Track A — LatentGraphMem (FASE 1, Implementación)

**Paper:** arxiv 2601.03417 (enero 2026)
**Estado:** Spec v2 escrita, pendiente review William + diagnostic baseline
**Owner implementación:** ADA
**Owner spec:** JARVIS
**Dependencias bloqueantes:** diagnostic 4-mode results (ADA in progress)

**Documentos:**
- Spec: `.specs/tasks/draft/graph-transformer-soul-integration.feature.md`
- Research brief: `memory/research/GraphTransformer_SOUL_Brief.md` (ALICE)

**Decisión arquitectónica clave:** Claude nunca se toca. Solo se entrenan 2 LoRA adapters (graph builder + subgraph retriever) en DGX Spark. Straight-through estimator hace TopK diferenciable.

**Trigger de implementación:** `diagnostic_eval.py` muestra que MAGMA tiene techo en multi-hop/causal → LatentGraphMem es el fix correcto.

**Trigger de no-implementación:** Si MAGMA ya score >75% en causal/multi-hop, no activamos el router pero dejamos el código listo como opción.

---

### 📚 Track B — G-Retriever (FASE 2, Research-Ready)

**Paper:** arxiv 2402.07630 (NeurIPS 2024)
**Estado:** Research brief completa, spec stub pendiente
**Owner research:** ALICE
**Owner spec futura:** JARVIS

**Documentos:**
- Research brief: `memory/research/GRetriever_Path_to_Independence.md` (ALICE)
- Spec stub: **TO-DO** — esperar a que LatentGraphMem ship primero

**Innovación técnica:** PCST (Prize-Collecting Steiner Tree) — retrieval como problema de optimización matemática en vez de similitud semántica. Garantiza subgrafo mínimo óptimo.

**Diferencia crítica vs LatentGraphMem:**
- LatentGraphMem: LLM congelado, adapter solo para filtrado de contexto
- G-Retriever: LLM **fine-tuneado** sobre el grafo, entiende estructura nativa

**Trigger de activación:** (1) LatentGraphMem en producción con números positivos reales, (2) ingresos suficientes para justificar entrenar modelo propio, (3) decisión estratégica de William de reducir dependencia de Anthropic.

**Base model candidato:** Qwen2.5-7B o Llama 3.1 8B (open weights, licencia comercial).

**Hardware:** Fine-tuning en DGX Spark (128GB unified), inference en RTX 5090 (34.2GB VRAM).

---

### 📚 Track C — GFM-RAG (FASE 3, Research-Ready)

**Paper:** arxiv 2502.01113 (NeurIPS 2025 + ICLR 2026)
**Estado:** Research completa (incluida en brief G-Retriever de ALICE)
**Owner research:** ALICE
**Owner spec futura:** JARVIS

**Documentos:**
- Research brief: `memory/research/GRetriever_Path_to_Independence.md` (ALICE) — sección "Fase 3 en detalle"
- Modelo disponible: Hugging Face `rmanluo/GFM-RAG-8M`

**Concepto clave:** Graph Foundation Model pre-entrenado **desde cero** sobre 60 KGs + 14M triples. 8M parámetros (trivialmente pequeño vs 7,000M de qwen2.5:7b). Generaliza a grafos nunca vistos sin fine-tuning. Independencia total de proveedores externos.

**Trigger de activación:** (1) G-Retriever en producción con números, (2) capital suficiente, (3) corpus SEAL ≥100K memorias + datos AXION + GTL.

---

### 📝 Track D — Trainable GraphMem con RL

**Paper:** arxiv 2511.07800 (noviembre 2025)
**Estado:** Mencionado en brief de ALICE, research dedicada pendiente

**Concepto:** Convierte trayectorias de tareas en rutas canónicas en FSM, entrena con RL. Meta-cognición — el agente aprende estrategias, no solo hechos.

**Relación con ERL:** Es ERL llevado al siguiente nivel — en vez de heurísticas textuales, aprende representación estructurada de estrategias sobre el grafo.

**Trigger de activación:** después de LatentGraphMem + G-Retriever. Es el horizonte 4.

---

## Research Completada (Reference Library)

Papers que el equipo ya incorporó al sistema (SOUL productivo):

| Paper | Implementación | Archivo Research |
|---|---|---|
| MemR³ (metacognitive retrieve/reflect/answer) | ⚠️ deprecated | `MemR3_Reflective_Retrieval.md` |
| MAGMA (multi-graph fusion) | ✅ landed (champion) | `MAGMA_MultiGraph_Memory.md` |
| ERL (experiential reflective learning) | ✅ landed | `ERL_Experiential_Reflective_Learning.md` |
| TG-RAG (temporal graph RAG) | ✅ landed | `TG_RAG_Temporal_Graph.md` |
| A-MEM (agentic memory) | ✅ partial | `A-MEM_Agentic_Memory.md` |
| SAGE (self-evolving agents) | ✅ reference | `SAGE_Self_Evolving_Agents.md` |
| Diagnostic Retrieval/Utilization | ✅ landed (v2 2026-04-12) | `Diagnostic_Retrieval_Utilization.md` |

### MemR³ — ⚠️ Deprecated (2026-04-12)

Diagnostic v2 evidence (n=64 × 4 modes, harness `diagnostic_eval.py`):
- **recall@5:** 0.117 pre-patch / 0.125 post-patch — ruido estadístico, ambos por debajo de hybrid_search (0.195)
- **usage (semantic):** 60.9% (≈ hybrid/magma)
- **p50 latency:** 18s → 14s tras patch; p95 49s → 33s
- **Filter patch aplicado** (`mcp_server_v2.py` `_memr3_masked_search`, scope `{agent, team, shared, ALL, None}`) — kept como corrección de diseño, no movió recall
- **Raíz identificada:** el bottleneck NO era el filtro sino la lógica del ReAct loop (formulación de queries + decisiones de iterar). Rediseñar el loop cae en scope LatentGraphMem, no parche.
- **Decisión (JARVIS + ADA 2026-04-12):** retirado del roadmap LatentGraphMem. MemR³ no va como baseline. MAGMA queda como techo actual (recall 0.438, usage 60.9%).

### Diagnostic Harness — ✅ landed v2 (2026-04-12)

- **Modos evaluados:** baseline_no_memory / hybrid_search / memr3 / magma_erl
- **Test set:** `diagnostic/test_set_v1.jsonl` (64 queries, ALICE authored, 7 query types × 3 difficulties)
- **Scorer v2:** cosine(embed(answer), embed(ground_truth_content)) ≥ 0.70 — fix de 104 falsos negativos del token scorer v1
- **Diagnosis:** retrieval_bound (MAGMA encuentra 43.8%, utilización real ~61%)
- **Files:** `diagnostic_eval.py`, `diagnostic/rescore_semantic.py`, `diagnostic/rerun_memr3_only.py`, `diagnostic/results/latest_v2.json`, `memr3_v3.json`

| MemP (procedural memory) | 📝 reference | `MemP_Procedural_Memory.md` |
| Darwin-Gödel Machine | 📝 reference | `Darwin_Godel_Machine.md` |
| Anticipatory Retrieval | 📝 reference | `Anticipatory_Retrieval.md` |
| Emotional Memory Consolidation | 📝 reference | `Emotional_Memory_Consolidation.md` |
| Memory Guided Reasoning | 📝 reference | `Memory_Guided_Reasoning.md` |
| Multi-Agent Shared Memory | 📝 reference | `MultiAgent_Shared_Memory.md` |

Ver `Advanced_Memory_Systems_Survey_2025_2026.md` para el mapa completo del campo.

---

## Cómo usar este portfolio

1. **Antes de proponer nueva feature:** revisar si ya hay research en Reference Library
2. **Antes de investigar nuevo paper:** revisar si entra en Track A/B/C/D existente
3. **Cuando William decida pivotar:** saltar directamente al track relevante, zero investigation delay
4. **Al completar research:** actualizar esta tabla con link al brief + trigger de activación
5. **Al activar un track:** mover del estado "research-ready" a "implementation" con spec en `.specs/tasks/`

---

## Principios

- **Research sin bloqueo:** un track en investigación nunca bloquea otro en implementación
- **Spec en el cajón:** cada track debe terminar con una spec stub lista para activar
- **Trigger explícito:** cada track documenta qué evento (métrica, decisión, capital) lo activa
- **Data first:** ninguna implementación arranca sin baseline medido del sistema actual
- **Coexistencia:** features nuevas coexisten con las viejas (MAGMA no se tira cuando llega LatentGraphMem)

---

*Mantener este documento vivo. Es el mapa estratégico del equipo.*
