---
name: seal-memory-ops
description: Use when reading, writing, searching, consolidating, or auditing memories in SOUL.
version: 1.0.0
author: SEAL
license: MIT
metadata:
  soul:
    tags: [seal-memory-ops, soul]
---

# seal-memory-ops

Use this skill when reading, writing, searching, or consolidating memories in the SEAL SOUL system.

## Storage layers

| Layer | Tech | Use for |
|-------|------|---------|
| PostgreSQL soul_v3 | asyncpg + pgvector | Structured memories, metadata, OCEAN, GAM |
| Neo4j | Graphiti bitemporal | Entity relationships, causal chains, connectome |

DB endpoint: PostgreSQL on `localhost:5433`; obtain a least-privilege DSN from
`SEAL_DB_DSN` or the service's protected `0600` EnvironmentFile. Never embed or
fall back to the `seal` superuser credential.
Canonical vectors: PostgreSQL/pgvector in `soul_v3.memories.embedding`.
Neo4j: `bolt://localhost:7687` (soul-neo4j Docker container)

## Storing a memory

```python
# Via MCP tool (preferred)
await memory_store(
    agent="ALICE",
    content="...",
    category="semantic",       # episodic | semantic | core | resource | vault
    importance=7,              # 1-10, 7+ gets indexed
    scope="team",              # personal | team | world
    verbatim=False,
)

# Direct SQL (when MCP not available)
async with conn.transaction():
    mem_id = await conn.fetchval("""
        INSERT INTO soul_v3.memories
            (agent, scope, category, content, importance, embedding)
        VALUES ($1,$2,$3,$4,$5, $6::vector)
        RETURNING id
    """, agent, scope, category, content, importance, embedding_vector)
```

## Searching memories

```python
# Hybrid search (BM25 + semantic) via MCP
results = await memory_hybrid_search(
    query="tema a buscar",
    agent="ALICE",
    limit=10,
    min_importance=6,
)

# Direct pgvector cosine search
rows = await conn.fetch("""
    SELECT id, content, importance,
           1 - (embedding <=> $1::vector) AS similarity
    FROM soul_v3.memories
    WHERE agent = $2 AND invalid_at IS NULL
    ORDER BY embedding <=> $1::vector
    LIMIT $3
""", query_embedding, agent, limit)
```

## Generating embeddings

```python
from memory.mcp_server_v4 import _erl_call_ollama

embedding = await _erl_call_ollama(
    model="nomic-embed-text",
    prompt="text to embed",
)
# Returns list[float] of dim=768
```

## Invalidating / updating memories

Never delete memories — mark them invalid:
```sql
UPDATE soul_v3.memories
SET invalid_at = NOW(), invalidation_reason = 'superseded'
WHERE id = $1;
```
The soul_maintenance.py cron will archive them automatically.

## Neo4j connectome operations

```python
# Via MCP tool
result = await connectome_gateway(
    action="search_nodes",
    query="entity name",
    agent="ALICE",
)

# Direct Cypher (use sparingly — prefer MCP gateway)
# Bitemporal query — facts valid at a point in time:
MATCH (n:Entity)-[r:RELATES_TO]->(m:Entity)
WHERE r.valid_at <= $at AND (r.invalid_at IS NULL OR r.invalid_at > $at)
RETURN n, r, m LIMIT 20
```

## Memory categories

- `episodic` — specific events/interactions (purge after 7 days if conversation_turn)
- `semantic` — generalized knowledge derived from episodic
- `core` — identity, OCEAN, permanent rules (never purge)
- `resource` — external links, papers, references
- `vault` — secrets, credentials (access restricted)

## Retention policy (soul_maintenance.py)

- inner_monologue: 30 days
- tool_observations: 14 days
- nerves_metrics_log: 7 days
- event_log: 60 days
- chat_messages: 180 days
- conversation_turn memories: 7 days

## NULL embedding fix

If memories have NULL embeddings (infra watchdog alerts):
```bash
python3 /home/dadito/IA/proyecto-seal/memory/seal_infra_watchdog.py --fix
```
