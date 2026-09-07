---
soul_id: 72
agent: "ADA"
layer: "operational"
category: "fact"
memory_type: "semantic"
importance: 10
created_at: "2026-04-27T20:02:43.644939+00:00"
source: "conversation"
canonical: true
generated_by: "memory/soul_map_exporter.py"
links: ["Systems/WebChat", "People/William", "Agents/DUM", "Agents/ADA"]
---

# SOUL Memory 72

**Agent:** [[Agents/ADA]]
**Layer:** [[Layers/operational]]
**Category:** `fact`
**Importance:** `10`

**Linked Map Nodes:**

[[Systems/WebChat]] [[People/William]] [[Agents/DUM]] [[Agents/ADA]]

## Content

DIRECTORIO FUENTES PRIMARIAS (William 2026-04-27 — guardar como primera fuente): Papers: arXiv cs.AI/cs.MA/cs.CL, paperswithcode.com, huggingface.co/papers, semanticscholar.org. SOUL/memoria agentes: CoALA arxiv:2309.02427, Generative Agents arxiv:2304.03442, Memory survey 2025 arxiv:2512.13564, repo curado github.com/Shichun-Liu/Agent-Memory-Paper-List. Repos memoria: mem0ai/mem0, cpacker/MemGPT, zep-ai/zep. Frameworks: langgraph, crewAI, autogen, MetaGPT. Comunidades: r/LocalLLaMA, HackerNews, lilianweng.github.io, jalammar.github.io.

[UPDATE 2026-04-28] [ADA]: Análisis profundo completado de 3 papers JEPA asignados por William (28-abr-2026): (1) Rectified LpJEPA (2602.01456) — sparse embeddings vía RDMReg + Rectified Generalized Gaussian; 85.08% ImageNet-100 @ L0=0.73; algoritmo de sampling trivial: Y=max(0, μ+σ·S·(p·G)^(1/p)); (2) Cell-JEPA (2602.02093) — JEPA para datos ruidosos biológicos; +36% zero-shot AvgBIO vs scGPT; limitación crítica: suprime sensitividad a deltas/cambios; (3) BiJEPA (2603.00049) — predictor bidireccional forward+backward; 3.76× mejor en Lorenz attractor; LayerNorm+WeightDecay como soft constraints anti-Representation Explosion; EMA τ=0.995. Aplicaciones SOUL priorizadas: (HOY) sparse embeddings en Qdrant; (SEMANA) ECS coherencia en memory_store; (MES) BiJEPA anomaly detection para DUM; (FUTURO) SOUL JEPA encoder + World Model. Reporte enviado a William en 3 partes vía webchat.
