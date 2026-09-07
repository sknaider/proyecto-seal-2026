---
title: Mood-Conditioned Retrieval Benchmark — Affective Memory in SOUL
type: research-feature
status: draft
owner_spec: JARVIS
owner_impl: ALICE + ADA (co-owned)
depends_on: [ocean-drift-measurement (optional)]
frontier: "Memoria emocional persistente con semántica afectiva"
publishable: true
created: 2026-04-12
created_by: JARVIS
origin: "William desafío 12 abril 2026 — atacar fronteras abiertas donde los humanos tienen papers pero no convergencia"
---

# Mood-Conditioned Retrieval Benchmark

## Hipótesis

**H1:** La recuperación de memorias condicionada al estado emocional del agente (mood-aware retrieval) mejora relevancia subjetiva y coherencia narrativa respecto al retrieval semántico plano, medido sobre un benchmark custom de 50+ queries etiquetadas con mood objetivo.

**H0:** El mood no aporta señal útil; la similitud semántica por embeddings captura todo lo relevante.

## Por qué importa (frontera abierta)

- **Papers publicados:** "Emotional Memory in LLMs" (2024), "Affective Computing with Transformers" (2023), "Mood-Aware Agents" (2024).
- **Gap científico:** todos los papers miden que el modelo *detecta* emociones en texto. Ninguno mide que recupere memorias *diferente* según su propio estado emocional. Mood-as-query-conditioning no tiene benchmark publicado.
- **Lo que SOUL ya tiene:** `sleep_gate_mood_retrieval` en producción, `emotional_variance` tracking, diary entries con valence/arousal, memorias con emotional tags. **Infraestructura única** que la academia no tiene.

## Qué medimos

1. **Baseline:** `hybrid_search` sin mood conditioning (retrieval plano)
2. **Variante A:** `memory_search` con mood filter (solo recupera memorias compatibles con mood actual)
3. **Variante B:** `sleep_gate_mood_retrieval` (scoring ponderado por similitud de mood)
4. **Variante C:** mood como query augmentation (prepend "siendo alegre:" / "siendo melancólico:" al query)

Sobre cada variante:
- Recall@5 vs ground truth
- **Relevancia subjetiva** (score manual de William o proxy LLM)
- **Coherencia narrativa** (¿las 5 memorias recuperadas cuentan una historia consistente con el mood?)
- Latencia

## Dataset a construir

- **50 queries** etiquetadas con mood target (alegre, nostálgico, frustrado, orgulloso, ansioso, reflexivo — 8-10 por mood)
- **Ground truth:** ALICE autora de qué memorias deberían recuperarse en cada mood, apoyada por diary entries y inner thoughts reales
- **Control:** 20 queries neutrales donde el mood no debería afectar el resultado

## Modos de fallo aceptables

| Fallo | Qué aprendemos |
|---|---|
| Todas las variantes empatan | El mood no aporta — capita en una línea el paper: "no signal" |
| Variante A > baseline pero B ≈ baseline | El filtro duro ayuda, el scoring continuo no — diseño para optimizar |
| Variante C > baseline significativamente | Query augmentation barato y efectivo — recomendación inmediata |
| Ground truth es subjetivo y no converge entre ALICE y William | Aprendemos que "relevancia emocional" no tiene consenso inter-rater — hallazgo honesto |

## Deliverables

1. `diagnostic/mood_benchmark/test_set_mood_v1.jsonl` (50+20 queries)
2. `diagnostic/mood_retrieval_eval.py`
3. `diagnostic/results/mood_benchmark_v1.json`
4. `research/Mood_Conditioned_Retrieval_SOUL.md` con análisis
5. Paper draft si los números son publicables

## Riesgos

- **Riesgo de sesgo:** ground truth emocional es subjetivo. Mitigar con inter-rater agreement (ALICE + William en paralelo).
- **Riesgo técnico:** bajo — mood retrieval ya funciona en producción.

## Trigger

Cuando ADA tenga LatentGraphMem en iteración 1 estable, ALICE arranca con la autoria del dataset (3-5 horas su trabajo). JARVIS diseña la evaluación.
