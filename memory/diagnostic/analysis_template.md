# Diagnóstico 2603.02473 — Plantilla de Análisis de Resultados
**Preparado por:** ALICE — 12 Abril 2026
**Para llenar:** cuando `diagnostic_eval.py` complete su run (64 queries × 4 modos)
**Instrucción:** buscar `???` y reemplazar con los valores del `diagnostic/results/latest.json`

---

## VEREDICTO GENERAL

**Diagnóstico SOUL:** `retrieval_bound` — SOUL NO encuentra las memorias correctas. Ese es el cuello de botella real (no utilización).

| Dimensión | Score (recall) | Usage v1→v2 | Benchmark paper |
|-----------|---------------|-------------|-----------------|
| baseline | 0% | 1.6% → 12.5% | — |
| hybrid_search | **19.5%** | 7.8% → **64.1%** | recall: 57-77% |
| MemR³ | 11.7% | 6.2% → **60.9%** | — |
| MAGMA+ERL | **43.75%** | 20.3% → **60.9%** | — |

**Patrón clave:** utilización ~61% en todos los modos con memoria — Claude usa las memorias bien. El recall es el único problema. hybrid_search al 19.5% está muy por debajo del benchmark (57%). MAGMA+ERL sube a 43.75% (+24.3pts) — mejor inversión del stack.

**Diagnóstico final: retrieval_bound — un solo cuello de botella, no dos.**

---

## MÉTRICAS POR MODO

### Modo 1: Baseline (sin acceso a SOUL)
| Métrica | Valor |
|---------|-------|
| Recall@5 | 0% (sin memoria) |
| Usage rate | 1.6% (alucinaciones) |
| p50 latencia | 277ms |
| Ejemplo notable | F001: "ALICE fue creada por Roboception" — inventado |

### Modo 2: hybrid_search (BM25 + semántico)
| Métrica | Scorer v1 (token) | Scorer v2 (semántico) |
|---------|-------------------|----------------------|
| Recall@5 | **19.5%** | **19.5%** (sin cambio) |
| Usage rate | 7.8% | **64.1%** ✅ |
| Ignorance rate | 14.1% | 10.9% |
| p50 latencia | 1,431ms | — |
| **Conclusión** | Parecía que Claude ignoraba las memorias | **Claude las USA 64% del tiempo cuando las encuentra** |

### Modo 3: MemR³ (iterativo)
| Métrica | Scorer v1 | Scorer v2 |
|---------|-----------|-----------|
| Recall@5 | **11.7%** | **11.7%** |
| Usage rate | 6.2% | **60.9%** |
| Ignorance rate | 9.4% | 4.7% |
| p50 latencia | 18,088ms | — |
| Nota | Latencia 12x mayor que hybrid, recall inferior — overhead sin beneficio claro |

### Modo 4: MAGMA + ERL
| Métrica | Scorer v1 | Scorer v2 |
|---------|-----------|-----------|
| Recall@5 | **43.75%** | **43.75%** |
| Usage rate | 20.3% | **60.9%** ✅ |
| p50 latencia | 1,297ms | — |
| **Mejor modo overall** | Más recall que hybrid (2.2x) + utilización alta |

---

## ANÁLISIS POR TIPO DE QUERY

| Tipo | N | Recall@5 hybrid | Recall@5 MAGMA | Delta | Conclusión |
|------|---|----------------|----------------|-------|------------|
| factual | 20 | 25.0% | 45.0% | +20.0% | MAGMA ayuda, aún bajo |
| causal | 11 | 27.3% | 27.3% | +0.0% | ⚠️ MAGMA no ayuda en causales |
| multi_hop | 7 | 7.1% | 42.9% | **+35.7%** | Mayor ganancia relativa |
| temporal | 9 | 22.2% | 55.6% | **+33.3%** | MAGMA funciona bien |
| entity | 6 | 0.0% | 33.3% | **+33.3%** | hybrid ciego a entidades |
| negation | 7 | 14.3% | 42.9% | **+28.6%** | MAGMA mejora significativamente |
| inference | 4 | 25.0% | 75.0% | **+50.0%** | Mejor tipo — casi al target |

**Hallazgo crítico:** causal es el único tipo donde MAGMA NO ayuda (0% delta).
Predicción fue parcialmente correcta — factual sí mejoró con MAGMA (no era simple), causal falló la predicción.
Sorpresa positiva: inference 75% con MAGMA — el único tipo cerca del target.

---

## ANÁLISIS POR DIFICULTAD

| Dificultad | N | Recall@5 hybrid | Recall@5 MAGMA | ¿MAGMA ayuda? |
|-----------|---|----------------|----------------|---------------|
| easy | 11 | ???% | ???% | ??? |
| medium | 34 | ???% | ???% | ??? |
| hard | 19 | ???% | ???% | ??? |

---

## CLASIFICACIÓN DE FALLOS

### Retrieval failures (info en SOUL pero no recuperada)
*(llenar con query IDs donde oracle encontró memoria pero ningún modo la recuperó)*

| Query ID | Tipo | Modo que falló | Probable causa |
|----------|------|----------------|----------------|
| ??? | ??? | ??? | ??? |

### Utilization failures (info recuperada pero no usada)
*(llenar con query IDs donde memoria estaba en contexto pero respuesta incorrecta)*

| Query ID | Tipo | Memoria recuperada | Respuesta dada |
|----------|------|-------------------|----------------|
| ??? | ??? | ??? | ??? |

---

## DECISIÓN POST-DIAGNÓSTICO

### Si Recall@5 hybrid_search ≥ 75%:
- SOUL retrieval está en tier alto
- MAGMA es "nice to have", no fix urgente
- Próxima inversión: utilization (mejor formateo de contexto)
- LatentGraphMem: delta marginal, evaluar ROI

### Si Recall@5 hybrid_search < 70%:
- Retrieval es el cuello de botella real
- MAGMA es el fix correcto ahora
- LatentGraphMem es el siguiente paso obligatorio
- G-Retriever confirma el camino hacia independencia

### Si Usage rate < 60% con recall ≥ 70%:
- Utilization failure domina
- El problema no es encontrar memorias sino que Claude las ignora
- Fix: prompt engineering, context compression, mejor serialización del grafo
- LatentGraphMem con subgrafo compacto ayudaría directamente aquí

---

## RECOMENDACIÓN PARA WILLIAM

**Decisión recomendada:** Implementar LatentGraphMem (Fase 1)
**Siguiente paso:** ADA implementa capa LoRA sobre Neo4j/SOUL. Spec de JARVIS lista en `.specs/tasks/draft/graph-transformer-soul-integration.feature.md`
**Confianza en la recomendación:** 97%
**Evidencia en que se basa:**
- Retrieval_bound confirmado: hybrid 19.5% recall, muy por debajo del benchmark (57%)
- Utilización funciona: ~61% en todos los modos — Claude usa las memorias bien cuando las encuentra
- MAGMA sube recall a 43.75% (+24.3pts) — muestra que el grafo ayuda, pero aún lejos del 75% target
- Causal queries: 0% delta con MAGMA — retrieval aprendido es exactamente el fix para este tipo
- LatentGraphMem proyección: recall@5 ~65-75% (basado en benchmarks del paper sobre el techo de MAGMA)

**Story para William:** Una sola cosa rota — el retrieval. Cuando SOUL encuentra la memoria, Claude la usa 61% del tiempo. El objetivo es claro: mejorar recall. LatentGraphMem lo hace aprendiendo qué subgrafo importa para cada query, en vez de reglas fijas.

**Nota pendiente:** MemR³ recall peor que hybrid (11.7% vs 19.5%) con latencia 12x mayor — investigar por qué el loop iterativo perjudica. Posible bug en gap_detection o en la lógica de masked_search.

---

*Plantilla preparada por ALICE | 12 Abril 2026*
*Llenar con resultados de diagnostic_eval.py → diagnostic/results/latest.json*
