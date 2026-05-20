# Research — Context Window Management en Frameworks LLM

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.
**Autor:** JARVIS (Opus) | **Fecha:** 2026-04-29 | **Para:** Mejorar spec_seal_context_arch_v1.md

---

## 1. MemGPT / Letta — Virtual Context Manager (LLM-driven)

**Arquitectura:** Tres niveles inspirados en OS virtual memory:
- **Main context** (in-prompt): instrucciones sistema + bloques de memoria core (`human`, `persona`) + FIFO de mensajes recientes
- **Recall storage** (out-of-prompt): historial completo, consultado via function call
- **Archival storage** (out-of-prompt): vector DB de hechos, consultado via `archival_memory_search/insert`

**Compresión:** LLM-driven. El agente mismo llama función `summarize` en mensajes FIFO cuando se acerca al límite. `ContextWindowCalculator` (Python) trackea tokens por sección con tokenizers exactos.

**Trigger:** Threshold configurable cerca del max context del modelo.

**Recuperación:** El agente llama funciones de retrieval (recall/archival search) cuando necesita info vieja.

**¿LLM o Python?** LLM para la decisión de evicción + summarization.

**Fuente:** https://github.com/letta-ai/letta

---

## 2. SEAL (Nous Research) — Dual-Trigger + 4-Phase (MÁS RELEVANTE)

**Dos triggers (Python-decided):**
- **Gateway hygiene:** 85% de contexto antes de procesar mensaje (safety net, estimación char→token)
- **In-loop compressor:** 50% de contexto (configurable `threshold: 0.50`, `target_ratio: 0.20`, `protect_last_n: 20`, `protect_first_n: 3`)

**Algoritmo 4 fases:**
1. **Fase 1 — Tool-result pruning (sin LLM):** tool outputs >200 chars fuera del tail protegido → `[Old tool output cleared...]`. Python puro.
2. **Fase 2 — Boundary alignment:** camina hacia atrás por budget de tokens, alinea a grupos tool_call/tool_result (nunca divide pares).
3. **Fase 3 — LLM summary (modelo auxiliar barato):** turnos del medio resumidos con template estructurado: Goal / Constraints / Progress (Done/InProgress/Blocked) / Key Decisions / Relevant Files / Next Steps / Critical Context. Re-compresión iterativa: summary anterior se pasa de vuelta para ACTUALIZAR, no re-resumir.
4. **Fase 4 — Reassembly:** head + summary + tail; pares tool huérfanos sanitizados.

**Safeguards clave:**
- SUMMARY_PREFIX explícito: "REFERENCE ONLY — do NOT answer compacted questions"
- Anthropic prompt caching (4 cache_control breakpoints): system + últimos 3 non-system — sobrevive compactación

**¿LLM o Python?** Fase 1 Python puro. Fases 2-4 LLM auxiliar barato.

**Fuentes:**
- https://github.com/NousResearch/soul/blob/main/agent/context_compressor.py
- https://github.com/NousResearch/soul/blob/main/website/docs/developer-guide/context-compression-and-caching.md

---

## 3. Mem0 — Externalize via LLM Extraction (sin compresión)

**Pipeline (`add()`):** LLM extrae hechos → resolución de conflictos (dedup/contradicción) → vector DB + graph opcional. Capas: conversation / session / user / org.

**Compresión:** Ninguna. Mem0 guarda **hechos extraídos**, no turnos comprimidos. El host del agente decide cuándo evictar turnos viejos del prompt. Mem0 solo provee retrieval.

**¿LLM o Python?** Requiere LLM para el pipeline de extracción.

**Fuente:** https://github.com/mem0ai/mem0

---

## 4. AutoGen (Microsoft) — Python-Only Buffered Context

**Implementación:** Dos clases nativas en `autogen-core/model_context/`:
- `BufferedChatCompletionContext(buffer_size=N)` — mantiene últimos N mensajes, borra los viejos. **Sin LLM, FIFO hardcoded.**
- `TokenLimitedChatCompletionContext(token_limit=T)` — borra los más viejos hasta estar bajo budget.
- Plugins externos (mem0/Zep/etc.) manejan long-term recall.

**¿LLM o Python?** Python puro — solo FIFO/token pruning, sin compresión semántica.

**Fuente:** https://github.com/microsoft/autogen

---

## 5. CrewAI — Recall Flow con LLM Query Distillation

**Pipeline:** LLM analiza query entrante → produce sub-queries → búsqueda paralela en scopes → deepening iterativo por confianza. Guarda `MemoryRecord` (content + scope + categories + importance + embedding) en vector DB (lancedb/qdrant).

**Sin compresión de turnos** — patrón externalize-then-recall similar a mem0.

**¿LLM o Python?** LLM para distilación de queries; Python para retrieval.

**Fuente:** https://github.com/crewAIInc/crewAI/blob/main/lib/crewai/src/crewai/memory/recall_flow.py

---

## 6. StreamingLLM (MIT, ICLR 2024) — Attention Sink (NO APLICABLE)

Cachea los primeros 4 tokens "sink" + últimos N tokens, borra KV del medio. Técnica de inferencia pura — **no preserva contenido semántico** de tokens descartados.

FAQ explícito: "context window remains constrained...would only summarize concluding paragraphs".

**No aplicable** para agentes multi-día donde las decisiones del medio de sesión importan.

**Fuente:** https://arxiv.org/abs/2309.17453

---

## 7. Literatura Académica Relevante

- **arxiv 2504.15965** — "From Human Memory to AI Memory": survey, 8 cuadrantes. Confirma dos patrones dominantes: in-context summarization (MemGPT-style) y external retrieval (mem0-style).
- **arxiv 2502.12110** — A-MEM: agentic Zettelkasten para agentes LLM, structured note-taking + linking.

---

## Recomendación para SEAL

**Adoptar el patrón dual-trigger de soul (4 fases) + externalization de mem0, nativo en Python:**

### Tier 1 — Python puro (sin LLM), trigger al 40%
Espejo de la Fase 1 de soul:
- Podar tool outputs >200 chars fuera de `protect_last_n=20`
- Eliminar Monitor/cron echoes (ruido específico de SEAL no presente en soul)
- Inline Python, 0 API calls
- Maneja 60-70% del bloat de contexto SEAL

**Por qué 40% y no 50%:** SEAL tiene ruido adicional de crons+Monitor que soul no tiene. Trigger más agresivo compensa.

### Tier 2 — Ollama qwen2.5:7b en DGX Spark, trigger al 65%
Solo si Tier 1 no baja el uso por debajo del 50%:
- Modelo local ya instalado en DGX Spark (CLAUDE.md confirma)
- Template estructurado de soul: Goal/Progress/Decisions/Files/NextSteps
- Sin API externa — inferencia local vía `http://localhost:11434` (o IP DGX)
- Excelente para sesiones Claude Code porque preserva "Active Task" continuity

### Externalization — SOUL pattern (ya tenemos las herramientas)
- Summaries comprimidos → `memory_store()` como session_summary
- Recuperables post-compact via `active_recall()` + `session_distill()`
- SOUL ya tiene esto — solo hay que conectarlo al ciclo de compresión

### Lo que NO usar
- StreamingLLM: pierde semántica
- AutoGen FIFO puro: pierde decisiones críticas de sesión
- Letta LLM-self-eviction: Claude Code no puede auto-modificar su prompt (el harness lo controla)

---

## Ventaja SEAL sobre soul post-implementación

| Capacidad | SEAL | SEAL |
|-----------|--------|------|
| Compresión proactiva | ✅ LLM auxiliar | ✅ Ollama local (cero API cost) |
| Identidad persistente | ❌ Sin equivalente | ✅ SOUL boot_context + OCEAN |
| Memoria semántica larga | SQLite FTS5 | ✅ Qdrant + PostgreSQL |
| Session chains | ❌ Básico | ✅ parent_id + reasoning_traces |
| Recuperación post-crash | ❌ Manual | ✅ RESURRECT automático |
| Cost compresión | API $ | ✅ $0 (Ollama local) |

---

*Reporte de investigación para mejorar spec_seal_context_arch_v1.md*
