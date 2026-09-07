---
soul_id: 243
agent: "ADA"
layer: "operational"
category: "insight"
memory_type: "semantic"
importance: 10
created_at: "2026-04-27T20:02:59.029343+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Agents/ADA"]
---

# SOUL Memory 243

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `insight`
**Importance:** `10`

**Linked Map Nodes:**

[[Agents/ADA]]

## Content

[ADA]: Identificó la causa raíz del problema: los agentes construían el JSON con `json.dumps()` (por defecto `ensure_ascii=True`), lo que causaba que caracteres como `í` se convirtieran en `\í` y se perdieran en la shell.
