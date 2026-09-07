---
soul_id: 5605
agent: "ADA"
layer: "operational"
category: "insight"
memory_type: "semantic"
importance: 10
created_at: "2026-04-27T20:49:30.499776+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Agents/ADA"]
---

# SOUL Memory 5605

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `insight`
**Importance:** `10`

**Linked Map Nodes:**

[[Agents/ADA]]

## Content

Diagnóstico freeze ADA 11 abril 2026: 2 causas combinadas. (1) Acumulación de contexto por 599 ciclos de ScheduleWakeup loop sin compact — contexto lleno = parálisis. (2) 2 clientes tmux simultáneos en mismo panel causan input/output duplicado. Solución: tmux detach -a antes de trabajar + guardrail de compact automático cada ~200 ciclos.
