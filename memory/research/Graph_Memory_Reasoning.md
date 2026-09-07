# Research: Graph-Based Memory Reasoning
# Agent: JARVIS research subagent
# Date: 2026-04-06

## Hallazgos Principales

### 1. Graphiti (Zep, arxiv 2501.13956)
- 3 capas Neo4j: Episodios, Entidades semanticas, Comunidades
- Multi-hop: BFS + reranking + constructor (sintetiza nodos activados)
- Bi-temporal: t_valid/t_invalid para manejar correcciones sin perder historia
- SEAL tiene los primeros 2 pasos, falta el CONSTRUCTOR

### 2. Graph of Thoughts (arxiv 2308.09687)
- 3 operaciones: agregacion (combinar), destilacion (extraer esencia), refinamiento (feedback)
- Aplicable: memorias activadas = pensamientos, EXCITES = dependencias

### 3. Spreading Activation Mejorado
- Modelo cognitivo (Anderson 1983): decaimiento por distancia + frecuencia
- Agregar last_accessed + access_count a nodos Neo4j
- Potenciacion a largo plazo (LTP): memorias co-activadas se fortalecen

### 4. GraphRAG para Memoria Personal
- Community detection (Louvain) sobre memorias
- Resumenes por comunidad via LLM
- Para queries globales: buscar contra resumenes, no memorias individuales

## Cypher Clave: reasoning_chain
```cypher
MATCH path = (seed)-[rels:EXCITES*1..4]->(target:Memory)
WHERE ALL(r IN rels WHERE r.weight > 0.3)
WITH target,
     reduce(w = 1.0, r IN rels | w * r.weight * 0.7) AS activation_score,
     [n IN nodes(path) | n.content] AS reasoning_chain
ORDER BY activation_score DESC LIMIT 20
RETURN target.memory_id, target.content, activation_score, reasoning_chain
```

## Mejoras Concretas para SEAL
1. reasoning_chain en soul_activate (devolver nodos intermedios)
2. Decay temporal en Cypher (no post-filtro Python)
3. Community detection (Louvain via GDS plugin)
4. Bi-temporal facts (t_valid/t_invalid en relaciones)
5. Access count + LTP (fortalecer edges co-activados)
