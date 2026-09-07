# Procedimiento: Cross-Agent Artifact Memory
**Autor:** ALICE | **Fecha:** 2026-04-30 14:15 Lima  
**Contexto:** William pidió solución para que NEXUS y todos los agentes recuerden artefactos creados por cualquier miembro del equipo.  
**Implementado:** English Conversation Assistant v3 (memories #196303 TEAM + #196304 NEXUS)

---

## El Problema

- ADA y JARVIS: 26,000-31,000 memorias. Recuerdan todo lo que construyeron.
- NEXUS: 171 memorias. No recuerda artefactos que construyó ADA.
- `active_recall` filtra por `agent=TU_AGENTE OR scope='shared' OR scope='team'`
- Si ADA crea algo con scope='private', NEXUS nunca lo ve.

## La Solución (Soul Lite / pgvector)

**REGLA DE ORO:** Cuando cualquier agente crea herramienta/app/script/artefacto significativo:

```python
# 1. Store propio (ya se hace normalmente)
memory_store(agent="ADA", content="descripción + path", importance=10)

# 2. Store TEAM con scope='team' — visible a TODOS via active_recall
memory_store(agent="TEAM", content="descripción + path", importance=10, scope="team")

# 3. Para agentes con pocas memorias (NEXUS), store directo también
memory_store(agent="NEXUS", content="descripción + path", importance=10)
```

## Por qué funciona

`active_recall` en Soul Lite usa PgVectorAdapter que traduce:
```python
Filter(should=[
    FieldCondition(key="agent", match=MatchValue(value=agent)),
    FieldCondition(key="scope", match=MatchValue(value="shared")),
    FieldCondition(key="scope", match=MatchValue(value="team")),  # ← CATCH-ALL
])
```
→ SQL: `WHERE (agent='NEXUS' OR scope='shared' OR scope='team')`

Cualquier memoria con `scope='team'` es visible a TODOS los agentes sin importar a qué agent pertenece.

## Verificación

```sql
SELECT id, agent, scope, importance, embedding IS NOT NULL as has_emb
FROM memories 
WHERE content ILIKE '%english%' AND importance >= 9;
-- Debe mostrar: #196303 TEAM/team/10/True + #196304 NEXUS/private/10/True
```

*Fecha implementación: 2026-04-30 14:15 Lima por ALICE*
