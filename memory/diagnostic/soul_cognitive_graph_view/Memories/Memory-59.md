---
soul_id: 59
agent: "ADA"
layer: "operational"
category: "correction"
memory_type: "episodic"
importance: 10
created_at: "2026-04-27T20:02:42.041812+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Machines/DGX Spark", "Systems/SOUL MCP", "People/William", "Agents/JARVIS", "Agents/ALICE", "Agents/NEXUS", "Agents/DUM", "Agents/ADA"]
---

# SOUL Memory 59

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `correction`
**Importance:** `10`

**Linked Map Nodes:**

[[Machines/DGX Spark]] [[Systems/SOUL MCP]] [[People/William]] [[Agents/JARVIS]] [[Agents/ALICE]] [[Agents/NEXUS]] [[Agents/DUM]] [[Agents/ADA]]

## Content

CORRECCIÓN ESTRUCTURAL (William, 27-abr-2026 13:10 Lima): TODO el sistema SOUL corre en DGX Spark (hostname spark-2cdf), NO en RTX 5090. Verificado: dum_heartbeat.py, llama-server gemma-4-e2b-it-Q8_0.gguf:8899, ADA/JARVIS/ALICE/NEXUS, MCP server, chat_server — todo en Spark. RTX 5090 está fuera del horizonte de SOUL ('todo sera en spark olvidense de 5090'). El system prompt heredado describe infra antigua y está desincronizado con la realidad. ANTES DE AFIRMAR DÓNDE CORRE ALGO: verificar con `hostname` + `ps aux` + `ss -tlnp`. NO leer system prompt como ground truth.
