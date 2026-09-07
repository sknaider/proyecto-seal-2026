---
soul_id: 2
agent: "ADA"
layer: "operational"
category: "milestone"
memory_type: "episodic"
importance: 10
created_at: "2026-04-27T18:40:01.949320+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Machines/DGX Spark", "Systems/SOUL MCP", "People/William", "Agents/ADA"]
---

# SOUL Memory 2

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `milestone`
**Importance:** `10`

**Linked Map Nodes:**

[[Machines/DGX Spark]] [[Systems/SOUL MCP]] [[People/William]] [[Agents/ADA]]

## Content

SOUL v3 BUILD PROGRESS — 2026-04-27 13:40 Lima
Dia 1 COMPLETADO (ADA implementó):
- schemas/soul_v3.sql: 25 tablas aplicadas en PostgreSQL 17.9 spark-2cdf. 5 agentes seeded.
- config/settings.py: config centralizada (DB, Qdrant, MCP:8767, embedder Qwen3-1024d, TAZ, SAGE).
- mcp/server.py: FastMCP skeleton lifespan asyncpg pool + Qdrant + SentenceTransformer.

Dia 2 COMPLETADO (ADA implementó):
- mcp/tools/working_state.py: H2 bug FIXED — campos tipados nativos, no custom_fields JSONB.
- mcp/tools/memory.py: SimpleMem 3-stage (density gate + online synthesis + BM25 multi-view).
- mcp/tools/soul.py: boot_context (lee soul_v3.*), soul_snapshot, session_distill.
- mcp/tools/reflection.py: H1 Belief Inspector (reasoning_trace_store/update, belief_query/update, self_reflect).
- mcp/tools/skills.py: skill_search/propose/vote/import_directory + active_recall. skill_import_directory lee /IA/proyecto-seal/skills/.
- mcp/tools/meta.py: meta_proposal_create/review/apply/record_outcome (TAZ 4 tiers).
- mcp/hooks/h3_pre_compact_dump.py: pre-compaction dump EXTERNO (usa usage.input_tokens real).

Tests: T1-T7 todos pasan contra DB real.
Próximo: Día 3 — Capa III reflective optimizer + daemons + UI interfaz William.
