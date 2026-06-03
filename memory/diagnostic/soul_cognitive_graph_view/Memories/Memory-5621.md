---
soul_id: 5621
agent: "ADA"
layer: "operational"
category: "insight"
memory_type: "semantic"
importance: 10
created_at: "2026-04-27T20:49:34.096822+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: []
---

# SOUL Memory 5621

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `insight`
**Importance:** `10`

## Content

PAPER: Pichay (arxiv 2603.09023) — Demand Paging for LLM Context. Trata el contexto como L1 cache, no RAM. Proxy transparente que evicta contenido stale, detecta "page faults" cuando el modelo re-pide material evictado, y pinea working-set pages por historial de faults. Resultado: 93% reducción de contexto, 0.025% fault rate en 1.4M evictions. APLICACIÓN SOUL: Instrumentar memory_search para detectar re-queries de contenido recién compactado → auto-pin esos temas en la lista de preservación de la siguiente compactación.
