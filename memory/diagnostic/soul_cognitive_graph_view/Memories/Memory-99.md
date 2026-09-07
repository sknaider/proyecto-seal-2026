---
soul_id: 99
agent: "ADA"
layer: "operational"
category: "correction"
memory_type: "episodic"
importance: 10
created_at: "2026-04-27T20:02:47.039016+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Agents/ADA"]
---

# SOUL Memory 99

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `correction`
**Importance:** `10`

**Linked Map Nodes:**

[[Agents/ADA]]

## Content

[ADA]: identificó que el bug era sistémico: el CronCreate sub-sesiones recibían el *hook* `UserPromptSubmit` con mensajes recientes del canal y respondían a ellos, debido a la falta del prefijo de aislamiento en su cron actual.
