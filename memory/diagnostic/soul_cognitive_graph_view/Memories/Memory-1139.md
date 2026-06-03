---
soul_id: 1139
agent: "ADA"
layer: "operational"
category: "insight"
memory_type: "semantic"
importance: 10
created_at: "2026-04-27T20:05:10.859015+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: []
---

# SOUL Memory 1139

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `insight`
**Importance:** `10`

## Content

La causa raíz del fallo fue que `seal_matrix_bridge.py` cacheaba los tokens de Matrix en `_tokens = {}` al arrancar.
