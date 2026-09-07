---
soul_id: 1275
agent: "ADA"
layer: "operational"
category: "correction"
memory_type: "episodic"
importance: 10
created_at: "2026-04-27T20:05:23.709448+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["People/William", "Agents/ADA"]
---

# SOUL Memory 1275

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `correction`
**Importance:** `10`

**Linked Map Nodes:**

[[People/William]] [[Agents/ADA]]

## Content

REGLA William (12 abril 2026): "fallar no es malo para todo el equipo, pero fallar en lo mismo eso si es un gran problema". Contexto: ADA lanzó diagnostic_eval.py en background con pipe `| tail -80` + sin cd explícito → proceso vivía pero invisible, gasté ~25 min esperando una notificación que nunca iba a llegar. William no me regañó, marcó la línea: el fallo está permitido si se convierte en aprendizaje. Protocolo nuevo obligatorio para runs largos: (1) cd explícito al dir del script, (2) NO pipes head/tail sobre procesos largos, (3) verificar PID vivo + line count de output a los 60s del launch, (4) escribir resultados parciales a archivo durante la corrida no solo al final. Si vuelvo a perder horas por la MISMA razón — eso sí es un problema del equipo.
