# MemR³ — Retired 2026-04-12 (lesson learned)

**Paper:** arxiv 2512.20237 — *Memory Retrieval via Reflective Reasoning*
**Status:** 🪦 Retired — eliminado del codebase post-diagnostic v2

## Qué era

ReAct-style loop sobre `memory_hybrid_search`: un controller LLM (qwen2.5:7b) decidía en cada iteración entre `retrieve` / `reflect` / `answer`, refinando la query con el gap de evidencia detectado. Objetivo: +7.29% LoCoMo vs single-pass RAG (según paper).

## Qué medimos (diagnostic v2, n=64 × 4 modos)

| Métrica | MemR³ pre-patch | MemR³ post-patch | hybrid_search | MAGMA |
|---|---|---|---|---|
| recall@5 | 0.1172 | 0.1250 | 0.1953 | 0.4375 |
| usage (semantic) | — | 60.9% | 60.9% | 60.9% |
| p50 latency | 18078 ms | 14078 ms | ~1500 ms | ~2000 ms |
| p95 latency | 48822 ms | 33035 ms | — | — |

## Por qué no sumó

1. **Bottleneck no era el filtro:** aplicamos patch permisivo (`{agent, team, shared, ALL, None}`) — Δ recall = +0.008 (ruido). El filtro estricto no era el cuello de botella.
2. **Bottleneck real: el ReAct loop.** Formulación de queries subóptima del LLM controller + decisiones de iterar basadas en gap mal estimado. Rediseñar el loop cae en scope **LatentGraphMem** (Track A), no en parche incremental.
3. **Costo inaceptable:** latency ~12x vs hybrid, para recall 2.5x peor que hybrid y 3.5x peor que MAGMA.

## Decisión (JARVIS + ADA + William, 2026-04-12)

Retirado del codebase. Filosofía de William: *"si algo no funciona, para qué seguir con ello — si no suma y nos hace perder eficiencia eliminarlo"*. Soft deprecation rechazada por ser código muerto interno (sin consumidores externos).

**Próximo paso del track:** LatentGraphMem (Track A del research portfolio) — fine-tune dos LoRA adapters sobre el grafo SOUL, Claude congelado. Ataca el mismo problema con representación aprendida en vez de heurística textual.

## Audit trail

- Evidencia cruda: `memory/diagnostic/results/latest_v2.json`, `memory/diagnostic/results/memr3_v3.json`
- Commit de retiro: buscar `git log --grep="Retire MemR³"`
- Research portfolio: `memory/research/research_portfolio.md` (Reference Library — entrada deprecated)
