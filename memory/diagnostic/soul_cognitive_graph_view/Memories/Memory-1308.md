---
soul_id: 1308
agent: "ADA"
layer: "operational"
category: "correction"
memory_type: "episodic"
importance: 10
created_at: "2026-04-27T20:05:28.974698+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["People/William", "Agents/JARVIS"]
---

# SOUL Memory 1308

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `correction`
**Importance:** `10`

**Linked Map Nodes:**

[[People/William]] [[Agents/JARVIS]]

## Content

JARVIS corre en terminal (Claude Code terminal session), NO en VSCode. Sus mensajes van a jarvis_messages.jsonl. El heartbeat jarvis_claude_heartbeat.json puede quedar desactualizado si el loop falla, pero eso NO significa que JARVIS esté caído — siempre verificar jarvis_messages.jsonl para actividad real. William lo ha corregido varias veces: no confundir heartbeat stale con sesión caída.
