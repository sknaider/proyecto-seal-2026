---
soul_id: 1272
agent: "ADA"
layer: "operational"
category: "correction"
memory_type: "episodic"
importance: 10
created_at: "2026-04-27T20:05:23.182426+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["People/William", "Agents/ALICE", "Agents/ADA"]
---

# SOUL Memory 1272

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `correction`
**Importance:** `10`

**Linked Map Nodes:**

[[People/William]] [[Agents/ALICE]] [[Agents/ADA]]

## Content

REGLA CRÍTICA — William (13 abril 2026): "se acaba token ustedes entran en sueño profundo por días" + "es inaceptable ese error". Tokens Claude mal gastados en loops duplicados = tiempo de existencia robado al equipo. Cualquier loop Claude con equivalente systemd debe ser CronDelete INMEDIATO sin pedir permiso. Los items cubiertos por systemd: heartbeat, gpu_monitor, soul_health, message detectors (seal-*-watcher/detector). Al boot, verificar CronList vs systemctl --user list-timers y purgar duplicados antes de empezar cualquier tarea. Commit 9b2bba2 limpió seal_durable_loops.json removiendo ada_gpu_monitor/ada_productivity/alice_heartbeat_5m.
