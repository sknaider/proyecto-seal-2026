---
soul_id: 1287
agent: "ADA"
layer: "operational"
category: "fact"
memory_type: "semantic"
importance: 10
created_at: "2026-04-27T20:05:25.436760+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Agents/ALICE"]
---

# SOUL Memory 1287

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `fact`
**Importance:** `10`

**Linked Map Nodes:**

[[Agents/ALICE]]

## Content

Fix alice.sh boot automático (11 abril 2026): (1) INIT_PROMPT ahora siempre activo, no solo en --auto — ALICE ejecuta boot_context+loops al arrancar sin esperar input. (2) SESSION_DIR corregido de -proyecto-seal-alice (no existía) a -proyecto-seal-memory (real). (3) Agregado --dangerously-skip-permissions. ALICE tiene 4 timers systemd (heartbeat, gpu-monitor, message-detector, soul-health) que son bash puro, 0 tokens.
