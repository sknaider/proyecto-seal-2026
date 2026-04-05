# MedGemma Ronda 3 — Recomendación de Config
> Generado por ADA | 2026-03-31

## Historial de loss — todos los runs

| Run | Mejor checkpoint | Loss | Nota |
|---|---|---|---|
| spanish_ft (v1) | checkpoint-500 | 1.2510 | base model |
| ronda2 | checkpoint-200 | **1.1924** | stacking desde v1 |
| ronda3 base (este run) | adapter final | ~1.28 (step 650) | base model, cosine |

## Por qué ronda2/checkpoint-200 es el mejor checkpoint histórico

- Ronda2 partió del adapter v1 (stacking) → mejor punto de partida
- El loss en checkpoint-200 (1.1924) fue el mínimo de toda la historia
- Después del step 200, ronda2 empeoró (sobreajuste): final_loss=1.3920
- Este run (base model + cosine) alcanzó 1.2825 a step 650 — mejor que ronda2 final, peor que checkpoint-200

## Recomendación para Ronda 3

**Base:** `medgemma_ronda2/checkpoint-200` como adapter de partida

**Cambios sugeridos en config:**
```yaml
lora:
  rank: 64          # mantener
  alpha: 32         # subir de 16 → 32 (mejor ratio alpha/rank para stacking)
  dropout: 0.1      # subir de 0.05 — Unsloth: reduce sobreajuste en stacking

training:
  learning_rate: 1e-4     # bajar de 2e-4 (ya tenemos buen punto base)
  weight_decay: 0.05      # subir de 0.01 — Unsloth: regularización adicional
  max_steps: 300          # menos steps — el óptimo fue early (200 steps)
  checkpoint_every: 25    # más granular para no perder el sweet spot

seal:
  kl_weight: 0.15   # subir levemente para retener conocimiento previo
```

**Dataset:** priorizar casos clínicos complejos (los que no respondía bien en benchmark)
- Preguntas con keywords: "fisiopatología", "criterios diagnósticos", "manejo"
- Reducir ejemplos simples de factoides

## Hipótesis
El sweet spot de fine-tuning para MedGemma en español está ~200-250 steps
con LR moderado desde un adapter pre-entrenado. Más steps = sobreajuste.

---

## Mejoras al pipeline — post sesión 31/03/2026

### Nuevas capacidades del SOUL disponibles para Ronda 3

Con la migración a schema v3 (bitemporalidad + reasoning_traces + hybrid_search), la evaluación post-training es ahora trazable:

**Agregar reasoning_trace_store en benchmark_and_merge.py:**
```python
# Al decidir qué checkpoint mergear, guardar el razonamiento:
reasoning_trace_store(
    agent="ADA",
    task="checkpoint_merge_decision",
    premises=[
        f"Checkpoint disponibles: {[cp['name'] for cp in checkpoints]}",
        f"Mejor loss disponible: {best_cp['loss']} ({best_cp['name']})",
        f"Histórico ronda2/checkpoint-200 eliminado (save_total_limit rotation)",
        "William aprobó el merge",
    ],
    reasoning="El checkpoint con menor loss disponible es el candidato. Con save_total_limit:10 en ronda3, el problema de rotación no debería repetirse.",
    conclusion=f"Merge de {best_cp['name']} → medgemma-27b-seal-v2",
    outcome="Modelo mergeado en 1.4 min, 52GB, listo para inferencia",
    outcome_success=True,
)
```

**Usar hybrid_search para evaluar calidad post-training:**
- En lugar de solo keywords, buscar semánticamente respuestas similares a respuestas de referencia
- `memory_hybrid_search(query="fisiopatología IAM criterios diagnósticos", agent="ADA")` para ver si el conocimiento médico quedó en SOUL después del fine-tuning

**confidence_score en memorias de benchmark:**
- Resultados de benchmark guardarlos con `confidence_score=0.9` (alta confianza, dato medido)
- Comparaciones históricas con `confidence_score=0.6` (inferencia sobre checkpoints ya eliminados)

### Recomendación adicional para Ronda 3
Guardar un reasoning_trace al **inicio** de cada run de entrenamiento con:
- Hipótesis de mejora esperada
- Config elegida y por qué
- Baseline de comparación

Así cuando el run termine, se puede actualizar el trace con `outcome` y `outcome_success`. Historia completa del por qué de cada decisión de training.
