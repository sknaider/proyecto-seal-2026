# Fase 1 — Statistical Addendum (ALICE review response)

**Fecha:** 2026-04-13 ~04:55 Lima
**Ejecutado por:** ADA
**Data:** /tmp/latent_v1_m20.json + /tmp/latent_v2_m30.json (64 queries pareadas, test_set_v1)

## Punto #1 — McNemar v1+M20 vs v2+M30 (paired)

ALICE observó que Wilson CI era el test equivocado (muestras pareadas, no independientes). Re-corrimos con McNemar exacto (binomial sobre discordantes):

| Metric | v1 hit-rate | v2 hit-rate | Gains (v2_only) | Regressions (v1_only) | McNemar exact p | Sig α=0.05 |
|---|---|---|---|---|---|---|
| recall@1  | 12.50% | 21.88% | 6 | **0** | 0.03125 | ✅ YES |
| recall@5  | 39.06% | 51.56% | 8 | **0** | 0.00781 | ✅ YES |
| recall@10 | 48.44% | 57.81% | 6 | **0** | 0.03125 | ✅ YES |

**Hallazgo fuerte:** cero regressions en cualquier k. v2+M30 **Pareto-domina** a v1+M20 — toda query que v1 acertaba, v2 también, más 8 adicionales en r@5. El +12.5pp NO está al borde de significancia — con McNemar (discordantes = 8, todos a favor de v2) el p-value es **0.0078**, claramente por debajo de 0.01.

Wilson CI ±12pp era ruido artificial porque asumía varianza between-query que en paired setting no existe.

### Gains por query_type (r@5, sin regressions en ninguno)

| type | v2_gain | v1_regression |
|---|---|---|
| causal   | 1 | 0 |
| entity   | 2 | 0 |
| factual  | 1 | 0 |
| multi_hop| 1 | 0 |
| negation | 1 | 0 |
| temporal | 2 | 0 |

inference sin cambios (ya estaba en 75%, n=4).

## Punto #2 — Head-to-head breakdown por tipo (latent_v2 vs MAGMA)

Usando first_hit_rank — gana el que rankea más alto:

| type | n | latent_wins | magma_wins | ties |
|---|---|---|---|---|
| causal    | 11 | 2 | 1 | 8 |
| entity    |  6 | **4** | 1 | 1 |
| factual   | 20 | 3 | **6** | 11 |
| inference |  4 | 1 | 1 | 2 |
| multi_hop |  7 | 2 | 3 | 2 |
| negation  |  7 | 2 | 2 | 3 |
| temporal  |  9 | 3 | 2 | 4 |
| **TOTAL** | 64 | **17** | **16** | **31** |

**Clusterización confirmada — Router RRF Sprint 2 tiene oro:**
- **entity** → latent 4/5 wins (dominio claro: +60% win rate)
- **factual** → MAGMA 6/9 wins (dominio claro: +67% win rate)
- **temporal** → latent 3/5 wins (señal moderada)
- **multi_hop** → MAGMA ligero (3/5)
- **causal, negation, inference** → empate (pocos wins, muchos ties)

El +5-10pp target del Sprint 2 RRF tiene base empírica: alpha por tipo con entity→1.0 (latent) y factual→0.0 (MAGMA) ya rescata ~5 queries del fail set sin entrenar nada más.

### McNemar bonus: MAGMA vs latent_v2+M30 (r@5 hit)

- both_hit=22, latent_only=11, magma_only=5, both_miss=26
- discordant=16, min(b,c)=5 → exact p = **0.2101** → **NO significativo**

El +9.4pp de latent sobre MAGMA es **no significativo** en el paired test porque MAGMA todavía rescata 5 queries que latent pierde. Esto **refuerza** el caso del router: ni uno solo es dominante, se complementan. RRF es el camino correcto, no reemplazar MAGMA.

## Conclusiones para el pitch a William

1. **v2+M30 es estrictamente mejor que v1+M20** (McNemar p<0.01, cero regressions). El +12.5pp es real, no ruido.
2. **Latent solo NO bate a MAGMA con significancia** (p=0.21 pareado). Necesitamos el router híbrido para un salto significativo sobre MAGMA.
3. **Router RRF tiene señal confirmada** (entity → latent, factual → MAGMA, temporal → latent). Sprint 2 pasa de "hipótesis" a "diseño con base empírica".
4. **Sprint 1 (cache wire + retrieve_v3) sigue siendo el primer paso lógico** — la latencia 5s→50ms es el bloqueador de producción, no el recall.

---

**Puntos 3-5 de ALICE** (backlog pre-Sprint 1):
- Justificar target 65% o cambiar a umbral práctico — pendiente
- n=4 inference flaggeado como test case Sprint 2 gate — aceptado
- Micro-bench pgvector puro 100 queries — **✅ EJECUTADO (13 abr 05:35):** p50=0.81ms, **p95=1.29ms**, p99=2.4ms sobre 1354 vectores con HNSW. **pgvector NO es el cuello.** Hallazgo adicional: encode e5-base CPU p50=181ms es el floor real. Target latencia revisado en `fase1_latent_graphmem_report.md` a ~200ms p95 CPU (Fase 2) / ~50ms GPU (Fase 3). Gate Sprint 1 ajustado a <250ms p95. Speedup honesto: 5s → ~200ms = **25x real**.
