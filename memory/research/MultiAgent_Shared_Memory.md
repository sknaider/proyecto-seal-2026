# Research: Multi-Agent Shared Memory
# Agent: JARVIS research subagent
# Date: 2026-04-06

## 5 Frameworks Analizados

### CrewAI
- Scopes jerarquicos tipo filesystem (/project/alpha, /agent/researcher)
- LLM infiere scope, categorias, importancia al guardar
- Recall: semantica + recencia + importancia

### MetaGPT (ICLR 2024)
- Shared message pool global con pub/sub por rol
- Pull activo de info relevante (similar a SEAL ada/jarvis_messages.jsonl)

### AutoGen v0.4 (Microsoft)
- Event-driven asincrono. Delta proposals, no overwrites.
- Conflictos: first-writer-wins (bajo riesgo), quorum voting (critico)

### LangGraph
- Grafo inmutable con reducers tipados + PostgresSaver nativo
- Cada update crea nueva version

### Mem0 / Mem0g
- Memorias como grafos dirigidos (Neo4j/Kuzu)
- Actor-aware: cada memoria etiquetada con agente fuente
- Deteccion de conflictos al integrar info nueva

## Resolucion de Conflictos
| Estrategia | Descripcion |
|---|---|
| Recencia | Mas reciente gana |
| Autoridad | William > JARVIS > ADA |
| Quorum | 2+ agentes de acuerdo = team truth |
| Arbitro LLM | Agente dedicado revisa conflictos |

## Lo que falta en SEAL
1. Event-driven broadcasting (imp >= 8 auto-broadcast)
2. Subscription por dominio (ADA: coding/infra, JARVIS: strategy/soul)
3. Delta sync (cursor last_read_id)
4. Scope model: private/shared/team/william

## Accion Inmediata
- Agregar `scope` a memory_store (default private)
- Trigger PostgreSQL: memory_broadcasts cuando imp >= 8 AND scope IN (shared, team)
