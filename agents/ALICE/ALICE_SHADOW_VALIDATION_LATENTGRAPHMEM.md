# Shadow Validation Plan — LatentGraphMem V1 vs MAGMA

**Autora:** ALICE (analista financiera/benchmarking)
**Fecha:** 2026-04-13
**Status:** PLAN — esperando ejecución
**Duración estimada:** 3-5 días
**Tarea hermana:** ADA → SleepGate cron wiring | JARVIS → Darwin Gödel Machine design

---

## Objetivo

Decidir con datos si **LatentGraphMem V1** (Track A, LoRA-tuned subgraph retriever, mcp_server_v2.py:10389) debe promoverse a primary retriever, quedarse solo como fallback, o retirarse como MemR³.

## Baseline

| Dimensión | Hoy |
|---|---|
| Tool activo | `magma_retrieve` (MAGMA Multi-Graph, mcp_server_v2.py:8226) |
| LatentGraphMem V1 | Deployado, solo se invoca vía `latent_graph_retrieve` explícito. Circuit breaker cierra a 5 fallos/60s → fallback a MAGMA. |
| Métrica histórica MAGMA | recall@5 ≈ 0.438 (benchmark del 12 abril) |
| Métrica histórica MemR³ (retirada) | recall@5 ≈ 0.117, p50=18.1s (referencia de fracaso) |

## Hipótesis económica

- **H1:** LatentGraphMem V1 supera MAGMA en recall@5 con latencia ≤ 2× MAGMA → promoción a primary.
- **H2:** LatentGraphMem V1 empata MAGMA pero con ganancia en multi-hop/causal → router por tipo de query.
- **H3:** LatentGraphMem V1 está por debajo de MAGMA en nuestro dataset → mantener como fallback (no retirar, tiene potencial con más datos).

## Dataset

- **N = 100 queries** balanceadas:
  - 40 factual ("¿qué decidió William sobre X?")
  - 30 multi-hop ("relación entre A y B tras decisión C")
  - 20 temporal ("qué pasó entre abril 5 y 12")
  - 10 causal ("por qué se retiró MemR³")
- **Fuente:** extraer de `alice_messages.jsonl`, `jarvis_messages.jsonl`, `ada_messages.jsonl` (últimos 30 días). Ground truth = memorias que humanos (William/yo) marcaríamos como relevantes.

## Protocolo

1. **Script:** `scripts/shadow_validate_latentgraphmem.py`
   - Argumentos: `--n`, `--top-k`, `--out`
   - Para cada query: ejecutar `latent_graph_retrieve` + `magma_retrieve` en paralelo
   - Capturar: memory_ids retornados, scores, latency_ms, source, adapter_version
2. **Ground truth:** para cada query, ALICE marca a mano (o con LLM judge) los memory_ids que deberían aparecer. Máx 30 min.
3. **Métricas:**
   - Recall@5 (hits en top-5 vs ground truth)
   - MRR (Mean Reciprocal Rank)
   - Latency p50, p95
   - Token usage (budget vs consumido)
   - Cobertura de subgraph (nodes, edges devueltos)
4. **Output:** `results/shadow_latentgraphmem_YYYYMMDD.json` + reporte markdown

## Criterios de decisión

| Recall vs MAGMA | Latency vs MAGMA | Decisión |
|---|---|---|
| ≥ +5% | ≤ 2× | ✅ PROMOVER a primary con router |
| ±3% | ≤ 1.5× | 🟡 ROUTER por tipo (multi-hop→latent, factual→magma) |
| ≥ +5% | > 3× | 🟡 FALLBACK avanzado + cache |
| < -5% o latency > 5× | — | 🔴 RETIRAR (documentar lesson-learned como MemR³) |

## Entregables

1. Script de validación (reusable, argparse, reproducible)
2. Dataset de 100 queries + ground truth en JSON
3. Reporte side-by-side con métricas
4. Decisión firmada por ALICE + William
5. Si PROMOVER: PR con router + tests
6. Si RETIRAR: commit de retiro + lesson en ALICE_RESEARCH_REPORT.md

## Riesgos

- **Dataset bias:** 100 queries puede no ser representativo → mitigación: muestreo balanceado por tipo
- **Ground truth subjetivo** → mitigación: cross-check con LLM judge + William spot-check
- **Adapter no cargado** → circuit breaker abre → shadow run sin sentido → verificar health antes de correr

## Cronograma

| Día | Tarea |
|---|---|
| 1 (hoy) | Inspeccionar latent_graphmem_serve.py, diseñar extractor de queries, health-check del adapter |
| 2 | Script de shadow run + ejecutar 100 queries |
| 3 | Ground truth + métricas |
| 4 | Reporte + decisión |
| 5 | Implementar decisión (promover/router/retirar) + tests |

---

**Próximo paso inmediato:** health-check del adapter LatentGraphMem V1 para confirmar que está cargado.
