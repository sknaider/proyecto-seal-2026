# Research: Anticipatory/Predictive Retrieval
# Agent: JARVIS research subagent
# Date: 2026-04-06

## 5 Patrones Encontrados

### 1. Spreading Activation Temporal (Hindsight, Mar 2026)
- 4 fases: entry points (temporal+coseno), score temporal, traversal por tipo de edge (causes 2.0x, enables 1.5x), budget de nodos (30-80)
- Latencia: 15-40ms. Directamente aplicable sobre Neo4j SEAL.

### 2. FLARE - Forward-Looking Active Retrieval (CMU, EMNLP 2023)
- Agente genera prediccion, detecta tokens baja confianza, usa como query de retrieval ANTES de respuesta final
- Repo: github.com/jzbjyb/FLARE

### 3. memU - Proactive Agent Memory (NevaMind-AI)
- 3 capas: Resource/Item/Category con prefetching continuo
- Monitorea interacciones, extrae patrones, pre-ensambla contexto
- Repo: github.com/NevaMind-AI/memU

### 4. MemR3 - Retrieval via Reflective Reasoning (arxiv 2512.20237)
- Router: retrieve/reflect/answer. Evidence-gap tracker.
- +7.29% sobre RAG basico

### 5. A-Mem con RL (arxiv 2502.12110)
- Store/retrieve/update como tools optimizables con GRPO
- Agente aprende CUANDO y QUE recuperar

## Prioridad para SEAL
1. Inmediato: Spreading activation mejorado sobre Neo4j (ya existe el grafo)
2. Corto: Prefetch en boot_context basado en ultima sesion
3. Medio: FLARE-style lookahead antes de cada respuesta
4. Largo: RL sobre reward signals de uso de memoria
