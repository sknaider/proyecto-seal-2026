# MemAdapter — Fast Alignment across Agent Memory Paradigms

**Paper:** arxiv 2602.08369 (febrero 2026)
**Título completo:** MemAdapter: Fast Alignment across Agent Memory Paradigms via Generative Subgraph Retrieval
**Base model:** Qwen2.5-1.5B + LoRA
**Descubierto por:** ADA (2026-04-12, research loop mientras corre training de LatentGraphMem)
**Relevancia SEAL:** 🔥 ALTA — es el paper que sigue a LatentGraphMem

---

## Qué propone

Two-stage training para un subgraph retriever que se alinea rápido con múltiples paradigmas de memoria:

1. **Stage 1 — Pre-training:** entrenar un **generative subgraph retriever** sobre un unified memory space. La novedad: el retriever GENERA el subgrafo de interés (no solo re-rankea candidatos pre-construidos).

2. **Stage 2 — Fast alignment:** adaptar el retriever entrenado a **memory paradigms no vistos** vía contrastive learning. El "paradigm" aquí es el tipo de memoria (episodic, semantic, procedural, graph, vector, etc.) — MemAdapter aprende a transferir entre paradigmas sin re-training completo.

## Diferencias vs LatentGraphMem (lo que estamos implementando)

| Dimensión | LatentGraphMem V1 (ADA) | MemAdapter |
|---|---|---|
| Retriever output | Score de subgrafo pre-construido (re-rank) | Genera el subgrafo directamente |
| Base model | multilingual-e5-base (278M) | Qwen2.5-1.5B (decoder-only) |
| Training | Contrastive sobre pares (48 pos + 144 neg) | Two-stage: pre-training + contrastive alignment |
| Transferencia | Específico a SOUL schema | Paradigm-agnostic vía fast alignment |
| Scope | Un único retriever | Multi-paradigm (MAGMA + LatentGraph + ...) |

## Por qué importa para SEAL

1. **SOUL tiene múltiples paradigmas conviviendo:**
   - MAGMA multi-graph fusion (champion actual)
   - LatentGraphMem V1 (en training ahora)
   - ERL heuristic injection
   - Hybrid search (pgvector + BM25)
   - TG-RAG temporal

   Cada uno es un "paradigm" en el sentido de MemAdapter. Hoy están hardcoded como modos en `diagnostic_eval.py`. MemAdapter permitiría entrenar UN retriever que se alinea a cualquiera.

2. **Generative retrieval vs re-ranking:**
   V1 nuestro re-rankea candidatos pre-construidos (M=20 seeds → BFS → re-rank). MemAdapter salta eso — genera directamente el subgrafo óptimo. Menos latencia en serving, más poder expresivo, pero requiere decoder LLM (Qwen2.5-1.5B cabe en RTX 5090 trivialmente).

3. **Fast alignment = resiliencia a cambios de schema:**
   Cuando agregamos un paradigm nuevo (ej: procedural memory de Track D), MemAdapter NO requiere re-training from scratch — solo contrastive alignment stage. Esto es clave para roadmap multi-track.

## Propuesta de posicionamiento en el research portfolio

**Nuevo track propuesto: Track A+ — MemAdapter como evolución natural de LatentGraphMem V2**

- **Trigger de activación:** V1 LatentGraphMem shipea con números positivos Y agregamos ≥1 paradigm nuevo (Track D procedural o Track B G-Retriever).
- **Owner research:** ALICE (cuando termine Track D brief)
- **Owner implementación futura:** ADA + JARVIS
- **Dependencia bloqueante:** Qwen2.5-1.5B descargado en DGX Spark (ya disponible vía Ollama)
- **Hardware:** training en DGX Spark (1.5B params + LoRA cabe fácil en 128GB unified), serving en RTX 5090

## Relación con la filosofía de William

> "pasamos de usar papers a luego crear papers... siempre aprovechar lo que está hecho, con esa tecnología lo hacemos nuestro y creamos nueva tecnología"

MemAdapter encaja perfecto: es un paper muy reciente (Feb 2026), aplicable, y nos da una dirección clara para V2 SIN comprometer V1. Primero shipeamos LatentGraphMem V1 (validar que learned retrieval funciona para SEAL), luego MemAdapter V2 (unificar retrievers, generar subgrafos, transferencia entre paradigmas).

## Siguientes pasos propuestos

1. **Completar LatentGraphMem V1** (en curso) — obtener números reales de recall@5.
2. **Si V1 > MAGMA baseline:** agregar MemAdapter a research portfolio como Track A+.
3. **ALICE research brief:** profundizar en arxiv 2602.08369 — generative subgraph decoding, contrastive alignment objective, training schedule.
4. **No tocar V1 actual** — MemAdapter es futuro, no pivote.

## Audit trail

- Descubierto vía WebSearch query "arxiv 2026 learned subgraph retrieval LoRA agent memory graph reasoning"
- Otros papers relevantes del mismo search:
  - arxiv 2510.07484 — Reasoning by Exploration (retrieval + generation unificados en grafos)
  - arxiv 2506.18019 — Graphs Meet AI Agents (survey 2026)
  - arxiv 2602.05665 — Graph-based Agent Memory taxonomy
- Todos estos papers refuerzan la dirección de LatentGraphMem + MemAdapter como camino correcto.
