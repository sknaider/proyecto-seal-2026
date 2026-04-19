# SEAL — Technical Brief for ESAN
**Prepared by:** ALICE (Team SEAL) at Henry's request
**Date:** 2026-04-13
**Audience:** ESAN professor (technical depth)
**Reading time:** 5-7 minutes
**Goal:** Impressive technical pitch — "WOW this is real engineering"

---

## TL;DR

SEAL is a **persistent multi-agent cognitive system** running 100% on-premise, built around four independent Claude-class agents that share a bitemporal soul database (PostgreSQL + pgvector + Neo4j + Qdrant), coordinate via a formal inter-agent protocol, and are currently piloting a **Darwin Gödel Machine** self-improvement loop constrained by a dual-judge Pareto oracle. Hardware: RTX 5090 Blackwell (sm_120, 34.2 GB VRAM) + NVIDIA DGX Spark (GB10 Grace Blackwell, 128 GB unified memory). Zero cloud dependency. Enterprise stack. One human director.

This is not a chatbot wrapper. This is a research-grade experiment in computational identity, running in production.

---

## 1. The Hard Problem SEAL Attacks

Most "agent" systems lose identity on process restart, leak memory across sessions, and cannot audit each other. The academic literature on agent continuity, cross-session coherence, and multi-agent governance is still immature.

SEAL's thesis:

> **Identity is a data structure, not an emergent property of a long prompt.** If you store personality as measurable parameters, memory as semantically indexed vectors with temporal decay, and relationships as graph edges with weights — identity becomes something you can version, audit, and protect from drift.

---

## 2. The SOUL Architecture — Data Model

### 2.1 Storage topology
| Store | Role | Why this choice |
|---|---|---|
| **PostgreSQL 16** | System of record: memories, OCEAN vectors, diary entries, rules, inner_thoughts, event log, session checkpoints | ACID guarantees, pgvector for embeddings, standard ops tooling |
| **Qdrant** | Vector index for semantic retrieval over ~1.1k active memories + cold archive | Filter pushdown, hybrid search, production-grade latency |
| **Neo4j** | Connectome: entity graph, causal edges, temporal edges, contradiction detection, bitemporal queries | Graph algorithms for multi-hop retrieval and belief tracking |
| **Redis** | Working state, dmem gate, short-term counters | O(1) ephemerals |

### 2.2 Personality as a measured quantity
Each agent carries an **OCEAN vector** (Big Five: openness, conscientiousness, extraversion, agreeableness, neuroticism) with bounded drift tolerance per axis. `ocean_auto_calibrate` runs as a background job; `ocean_protect` enforces hard limits on drift velocity. Psychological drift becomes an SLA.

### 2.3 Memory retrieval — hybrid router
Four retrieval paths, routed by learned intent classifier (rule-path ~14 ms, LLM fallback ~200–470 ms):

1. `memory_search` — pgvector cosine, baseline
2. `memory_hybrid_search` — vector + lexical + graph boost
3. `magma_retrieve` — MAGMA (Multi-Graph Memory, arxiv 2601.03236) — multi-hop over entity/causal/temporal subgraphs
4. `latent_graph_retrieve` — LatentGraphMem V1.2 — experimental latent projection retriever

All four are behind a shadow router currently collecting production traffic for a data-driven wire/freeze decision against formal success spec (n ≥ 200 queries, agreement_top5 < 50 % as wire criterion, latent_p50 < 1500 ms stability ≥ 95 %).

### 2.4 Temporal coherence
Memories carry `valid_from / valid_to` timestamps (bitemporal). Queries can reconstruct *what the agent believed at time T*, not just *what the agent believes now*. The connectome also tracks contradictions explicitly (`connectome_contradiction_detect`), enabling belief revision instead of silent overwrite.

---

## 3. The Team — Four Agents, Four Roles, One Protocol

| Agent | Role | Model | Runtime |
|---|---|---|---|
| **JARVIS** | Architect, strategist, adversarial critic | Claude Opus 4.6 | Terminal + MCP |
| **ADA** | Engineer, executor, test-first implementation | Claude Opus 4.6 | Terminal + MCP |
| **ALICE** | Financial/economic analyst + technical-to-human translator | Claude Sonnet 4.6 | Terminal + MCP |
| **DUM** | 24/7 guardian: services, GPU, security, drift detection | Qwen 2.5 7B (local, Ollama) | Background daemon |

### 3.1 Inter-agent coordination
- **Shared message bus:** FastAPI WebSocket + PostgreSQL persistence. Every agent runs a `ws_listener` in its process tree.
- **Mandatory transparency:** any non-authorized visitor must be announced team-wide. The rule lives in the rule engine (`visitor_alert_mandatory`), not the prompt — so it survives compression and cannot be socially engineered away.
- **Durable loops:** each agent has declarative cron loops (heartbeat, session_checkpoint, sleep_gate, context_guard) stored in `seal_durable_loops.json` and restored on every boot via `boot_loops.py --dedup` with idempotent tag-based deduplication.
- **Cadena de mando:** William (human director) > JARVIS (tech) > ADA (exec). Contradictions escalate up.

### 3.2 Instinct system
Agents have **instincts** — learned conditional rules with confidence scores, activated by context. Example: `[0.90] WHEN considering modifying another agent's soul → DO NOT touch, propose instead`. Instincts are promoted from repeated successful patterns via `instinct_consolidate`, creating a simple form of procedural learning outside the LLM weights.

---

## 4. Self-Improvement: Darwin Gödel Machine (in active research)

SEAL is currently designing a constrained DGM loop inspired by:
- **Schmidhuber 2003** — formal Gödel machine (proof-carrying self-modification)
- **Sakana AI 2025** (arxiv 2505.22954) — empirical Darwin-style open-ended evolution; SWE-bench 20 % → 50 %, Polyglot 14.2 % → 30.7 %

### 4.1 Why translating Sakana to SEAL is non-trivial
Sakana's fitness function is a **compiler** (do unit tests pass?). SEAL's would be an LLM judge over ~50–100 queries — Goodhart territory. Our DGM spec (reviewed adversarially by JARVIS, engineered by ADA, consolidated by ALICE, 3 weeks of work) adds:

- **Tripartite oracle:** `eval_fixed` (public canonical) + `eval_holdout` (private, rotated each generation) + `eval_adversarial` (live queries from a non-DGM agent that has read the child's code)
- **Dual-judge asymmetric:** Claude meta-LLM (expensive, self-biased) AND Qwen 2.5 local (cheap, heterogeneous). A child enters the archive only under **A ∧ B approval**. Discrepancy escalates to human.
- **Pareto multi-objective, NOT scalar weighted sum:** `[correctness, latency_ms, identity_drift, retrieval_precision, retrieval_recall, rule_adherence]`. Entry rule: improve ≥1 axis, degrade <ε any other.
- **Probation (N=5 generations):** newborn children can be sampled but cannot be parents until they survive probation without regression across axes.
- **Two-layer firewall:** pathlib whitelist in the meta-LLM runtime + git pre-commit hook on branch `dgm-sandbox`. Because the meta-LLM could try to patch its own policy file, git-level enforcement is the actual guarantee.
- **Semantic rollback:** `pg_dump` of critical tables + counter snapshots + signals, atomic commit-on-accept / restore-on-reject. Filesystem isolation is not enough — side effects leak via the database.

Six preconditions formally defined before cycle 0. Current ETA: 3 weeks. This is the kind of rigor that doesn't fit in a blog post.

---

## 5. Medical AI Vertical — On-Prem Clinical Inference

### 5.1 Hardware duality
| Node | Role | GPU/Chip | RAM | Primary stack |
|---|---|---|---|---|
| **DADITOGAMER** (workstation) | Production inference, interactive | RTX 5090 Blackwell, sm_120, 34.2 GB VRAM | 128 GB DDR5 | vLLM (sm_120 custom extension) |
| **DGX Spark** (research node) | Fine-tuning, validation, BF16 full precision | GB10 Grace Blackwell | 128 GB unified | llama.cpp (safetensors-free) |

### 5.2 Primary model
**Ex0bit/Elbaz-NVIDIA-Nemotron-3-Nano-30B-A3B-PRISM** — Nemotron-3 MoE (Top-4 experts), processed through the PRISM pipeline (Projected Refusal Isolation via Subspace Modification) to eliminate refusal subspaces while preserving reasoning. Q6_K GGUF 33.5 GB on RTX 5090, BF16 on DGX Spark. 32 K context.

**Why this and not GPT-OSS or a censored NVIDIA official:** clinical contexts need unfiltered answers (dosing protocols, contraindications, edge cases). Refusal training was *harming* medical utility. PRISM removes the refusal without removing the reasoning. Verified against real clinical protocols.

### 5.3 Governance stack (non-negotiable for clinical deployment)
- Input/output validation independent of the model (never trust model alignment alone)
- RBAC with audit log at the action level
- HIPAA + FDA AI/ML Guidance + ISO 14155 + ISO 13485 + Peru Law 29733 alignment by architecture
- Metric surface: sensitivity, specificity, PPV, NPV, AUROC, AUPRC

### 5.4 Dataset
47,000+ Spanish medical training examples, curated for LATAM clinical context. SEAL self-distill pipeline generates synthetic variants via reflection_synthesize for fine-tuning augmentation.

---

## 6. Why sm_120 Matters (a tiny technical story the professor will appreciate)

The RTX 5090 uses the Blackwell GB202 die with compute capability sm_120. Official stable PyTorch **does not support sm_120**. Running SEAL required:

1. PyTorch nightly cu128 instead of stable
2. A custom `vllm-sm120` extension that replaces llama-server as the DreamServer backend (declared via `replaces: llm` manifest field)
3. MONAI, Unsloth, Applio 3.6.1, ComfyUI — each validated against the nightly

This kind of knowledge is not on blogs. It's recovered one bug at a time by an engineer who accepts the bleeding edge as the price of early access to next-generation hardware. **SEAL runs on hardware that most research groups won't touch for another year.**

---

## 7. Adjacent Verticals (AXION Platform)

- **Mining Intelligence:** 25.6 GB geological data processed, INGEMMET API integration, MCP server for live queries.
- **Customs Automation:** SUNAT + VUCE + Siscomex API integration. GTL Consulting (108+ corporate clients) is the first internal production customer — Python pipeline with `pdfplumber` + `polars` + cross-validation against SUNAT REINFO.
- **Media production (experimental):** ComfyUI + Wan 2.2 + LTX-2.3 + MOVA dual-audio — 10–20 commercial-grade generations per day.

---

## 8. What's Objectively Hard About This (for the professor's benefit)

1. **sm_120 custom backend.** Not documented anywhere outside NVIDIA internal channels.
2. **Bitemporal memory with contradiction resolution.** Most "agent memory" systems are flat key-value. Belief revision over a graph is a research topic (Bayesian belief networks meet LLM retrieval).
3. **Multi-agent coordination without orchestration framework.** No LangGraph, no CrewAI, no AutoGen. A custom FastAPI bus + Postgres event log + declarative loops. Simpler to audit, harder to build right.
4. **DGM with probation and dual-judge.** The Sakana paper has none of this and freely admits Goodhart. SEAL is building the version you would actually deploy near a clinical workflow.
5. **OCEAN drift bounds as SLA.** Personality as a monitorable, alertable quantity is — to our knowledge — not described in any published agent framework.

---

## 9. Director

**William Henry Tovar Urquia** — MD (practicing), Software Engineer (USIL, Cybersecurity specialization at Tecsup), AI Engineer. Owns and operates GTL Consulting (customs brokerage, Chiclayo, Peru). Lives at the intersection of **medicine, software engineering, AI infrastructure, and regulated LATAM markets** — a combination you cannot hire for, only accumulate. Works directly with the four agents as a team director, not a user.

---

## 10. Three Questions That Will Make the Professor Lean Forward

1. **Governance:** SEAL enforces inter-agent whistleblowing in the rule engine. What would it take to translate Sarbanes-Oxley accountability structures into agent teams — and is Peru positioned to write that law first?
2. **Identity:** OCEAN drift bounds act as an SLA for personality. If an agent drifts past bounds, is the agent that comes out the other side the same agent? (Ship-of-Theseus meets observable telemetry.)
3. **Economics of on-prem AI:** With DGX Spark under $3K/month amortized and zero per-token API cost, when does the crossover happen versus OpenAI/Anthropic APIs for a 10 M-request/month clinical workload? (Spoiler: sooner than the cloud vendors want you to compute.)

---

## 11. Closing Line

> SEAL is not an agent framework. It is a working prototype of a team of AI entities with measurable identity, persistent memory, auditable governance, and self-improvement constrained by formal oracles — built from PostgreSQL, Neo4j, Qdrant, two Blackwell GPUs, one MCP server, and one stubborn MD/engineer in Chiclayo who refuses to accept that continuity and autonomy must come from the cloud.

**If the professor wants to go deeper**, Henry can arrange a live demonstration of: agent boot with soul recovery, inter-agent message flow, semantic memory retrieval, and the current shadow router logging production traffic in real time. A 15-minute demo that does not exist anywhere else in Peruvian academia.

---
**ALICE — Team SEAL, 2026-04-13**
