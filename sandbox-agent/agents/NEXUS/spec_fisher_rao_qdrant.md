# Spec: Hopfield Re-ranking + Qdrant Integration
**NEXUS Sandbox u2014 Fase 2 SOUL Native Integration**
**Fecha:** 2026-04-25 | **Estado:** v2 CORREGIDA | **Aprobado por:** JARVIS

## Objetivo
Integrar ModernHopfieldNetwork de superlocalmemory v3.4.36 como re-ranker sobre candidatos Qdrant en `memory_hybrid_search`. Sin segundo storage u2014 Soul DB (PostgreSQL + Qdrant) intacto.

## Arquitectura Actual
```
Qdrant ANN u2192 candidatos top-K u2192 score hybrid (semantic + recency + importance) u2192 resultado
```

## Arquitectura Propuesta (Fase 2)
```
Qdrant ANN u2192 candidatos top-K u2192 ModernHopfieldNetwork re-ranking (CPU) u2192 score hybrid u2192 resultado
```

## API Real Verificada (arm64 DGX Spark, v3.4.36)

```python
from superlocalmemory.retrieval.hopfield_channel import ModernHopfieldNetwork, HopfieldConfig

# Mu00e9todos pu00fablicos:
# .attention_scores(query, stored)  u2014 calcula scores de atenciu00f3n Hopfield
# .energy(pattern)                  u2014 energia del patru00f3n en la red
# .retrieve(query, stored)          u2014 recupera patru00f3n mu00e1s cercano
# .update(stored_patterns)          u2014 actualiza memoria de la red
```

**arm64 DGX Spark:** pip install OK u2705, import OK u2705

## Implementaciu00f3n

### Paso 1: Instalar superlocalmemory
```bash
pip3 install superlocalmemory
```

### Paso 2: Integrar en mcp_server_v2.py
```python
import numpy as np
from superlocalmemory.retrieval.hopfield_channel import ModernHopfieldNetwork, HopfieldConfig

# Singleton u2014 inicializar una vez
_hopfield_cache = {}
def get_hopfield(dim: int) -> ModernHopfieldNetwork:
    if dim not in _hopfield_cache:
        _hopfield_cache[dim] = ModernHopfieldNetwork(HopfieldConfig(dim=dim))
    return _hopfield_cache[dim]

# En memory_hybrid_search, tras obtener candidatos Qdrant:
async def memory_hybrid_search(agent, query, ...):
    candidates = await qdrant_search(query, top_k=50)  # EXISTENTE
    
    if USE_HOPFIELD_RERANK and candidates:
        dim = len(candidates[0].vector)
        hopfield = get_hopfield(dim)
        q_vec = np.array(query_embedding)
        stored = np.array([c.vector for c in candidates])
        scores = hopfield.attention_scores(q_vec, stored)  # API verificado
        ranked = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        candidates = [c for _, c in ranked[:20]]
    
    return apply_hybrid_score(candidates)  # EXISTENTE
```

### Paso 3: Feature flag en config
```python
USE_HOPFIELD_RERANK = os.getenv("SOUL_HOPFIELD_RERANK", "false").lower() == "true"
```

## Benchmark Plan
- Dataset: 100 queries reales de william_channel.jsonl
- Mu00e9tricas: precision@5, recall@10, latencia p50/p95
- Baseline: retrieval actual (coseno + hybrid)
- Test: + Hopfield re-ranking
- Entorno: sandbox NEXUS (DGX Spark arm64)

## Riesgos (actualizados)
1. **API `attention_scores`**: mu00e9todo existe, pero firma exacta pendiente de test real con tensores
2. **Latencia**: Hopfield es O(n*d) sobre candidatos u2014 medir con n=50 en sandbox
3. **Ganancia marginal**: si <5% mejora en precision@5, descartar

## Criterios de Aceptaciu00f3n
- precision@5 mejora u22655% vs baseline
- Latencia p95 < 500ms (re-ranking sobre top-50 candidatos)
- Feature flag funciona sin restart
- Tests pasan en arm64 DGX Spark

## Dependencias
- ADA termina Fase 1 primero
- William mantiene luz verde
- superlocalmemory v3.4.36 en arm64 u2705 (ya verificado)

## Autor
NEXUS Sandbox | Revisiu00f3n aprobada por JARVIS 2026-04-25
