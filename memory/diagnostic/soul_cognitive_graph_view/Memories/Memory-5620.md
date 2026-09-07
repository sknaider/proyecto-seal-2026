---
soul_id: 5620
agent: "ADA"
layer: "operational"
category: "insight"
memory_type: "semantic"
importance: 10
created_at: "2026-04-27T20:49:33.892233+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: []
---

# SOUL Memory 5620

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `insight`
**Importance:** `10`

## Content

TÉCNICA PRÁCTICA: SCRATCHPAD pattern — archivo mutable (SESSION_STATE.json) que se escribe cada N mensajes por un loop y se lee post-compactación. Cursor, Roo Code y Windsurf convergen independientemente en esta misma solución. Complemento: repo-map generator (tree-sitter parsed summary de archivos — cabe un codebase entero en ~2K tokens). APLICACIÓN SOUL: crear SESSION_STATE.json actualizado por loop cron + repo-map de memory/ para post-compaction recovery.
