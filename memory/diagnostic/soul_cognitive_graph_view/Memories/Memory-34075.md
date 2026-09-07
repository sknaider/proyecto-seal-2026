---
soul_id: 34075
agent: "ADA"
layer: "operational"
category: "correction"
memory_type: "episodic"
importance: 10
created_at: "2026-04-25T20:05:53.797726+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Agents/ADA"]
---

# SOUL Memory 34075

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `correction`
**Importance:** `10`

**Linked Map Nodes:**

[[Agents/ADA]]

## Content

[ADA]: El problema raíz era que los agentes construían el JSON con `json.dumps()` (default `ensure_ascii=True`), lo que causaba que caracteres como 'í' se convirtieran en `\í` y luego se perdieran en la shell.
