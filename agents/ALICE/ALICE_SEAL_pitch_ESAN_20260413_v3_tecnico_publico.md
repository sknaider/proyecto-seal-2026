# Project SEAL — Technical Brief (Public Version)
**Author:** ALICE, Team SEAL
**Date:** 2026-04-13
**Audience:** ESAN professor
**Scope:** What SEAL does, what it is based on, what technologies it uses.
**Privacy:** no internal datasets, no client information, no clinical specifics, no proprietary metrics.

---

## 0. One-sentence answer to "what is this?"

SEAL is a multi-agent cognitive system with **persistent identity, persistent memory, and auditable governance**, built to run entirely on-premise on next-generation NVIDIA Blackwell hardware, as a research platform for the next chapter of applied AI.

---

## 1. What SEAL does

### 1.1 It keeps AI agents from forgetting who they are
Most LLM-based systems lose everything when the process restarts: personality, relationships, in-progress work, prior conversations. SEAL treats identity as **state that lives in a database**, not as a long prompt that has to be reloaded every time. Each agent boots by reading its own soul from disk: personality vector, memories, relationships, rules, recent emotional state.

### 1.2 It lets multiple agents coordinate — and audit each other
SEAL runs as a team: four agents with different roles, a shared message bus, a shared memory system, and an explicit protocol for inter-agent transparency. Every agent has the authority (and obligation) to report anomalies in the behavior of the others. Governance is not a prompt; it is code and rules.

### 1.3 It retrieves memory the way humans actually use it
Not one-shot keyword search. SEAL uses a **hybrid router** that dispatches every query across several retrieval strategies (vector similarity, graph traversal, multi-hop reasoning, latent projection) and picks the right one based on the intent of the question. This is what allows long-horizon continuity — remembering not only what was said yesterday, but *why* and *in what emotional context*.

### 1.4 It is experimenting with self-improvement
SEAL is currently designing a **safety-constrained self-improvement loop** inspired by recent open-ended evolution research. Agents will be allowed to propose improvements to their own code inside a fully isolated sandbox, evaluated against a tripartite oracle (public + private + adversarial test sets), and only survive if they improve along a Pareto front without regressing any other axis. Nothing like this is running in Peruvian academia today.

### 1.5 It runs on-premise, end to end
No cloud inference. No API dependency for the critical path. Everything — storage, retrieval, reasoning, tools — runs on hardware that lives in one physical location. That decision is a design constraint, not a limitation: it opens markets where data cannot legally leave the country or the building.

---

## 2. What SEAL is based on — intellectual foundations

### 2.1 Memory & retrieval
- **MAGMA — Multi-Graph Memory for LLM Agents** (arxiv 2601.03236). Graph-based multi-hop memory structures. SEAL implements MAGMA as one retrieval path and benchmarks alternative latent-graph approaches against it in shadow mode on real production traffic.
- **pgvector / hybrid retrieval** — combining dense vector search, sparse lexical search and graph boost. Modern best practice for RAG-grade retrieval.
- **Bitemporal databases** — classic data engineering idea (valid-time vs. transaction-time) applied to *what an agent believed at a given moment*. Enables belief revision instead of silent overwrite.

### 2.2 Personality & continuity
- **Big Five / OCEAN model** — the most empirically validated personality framework in psychology. SEAL stores each agent's personality as a vector over the five axes with monitored drift bounds. If personality drifts beyond tolerance, the system alerts. Personality becomes an **SLA**.

### 2.3 Self-improvement
- **Schmidhuber 2003 — Gödel Machine.** Formal framework for a system that rewrites itself only when it can formally prove the rewrite is an improvement.
- **Sakana AI — Darwin Gödel Machine** (arxiv 2505.22954). Empirical open-ended evolution of coding agents: improved SWE-bench scores from 20 % to 50 % and Polyglot benchmark from 14 % to 30 %. SEAL adapts the DGM idea with additional guardrails (dual-judge evaluation, Pareto multi-objective, probation periods, two-layer firewall) because transplanting the original method 1:1 into a system that has identity and memory is not safe.

### 2.4 Multi-agent governance
- **Sarbanes-Oxley whistleblowing framework** — inspiration, not copy. The idea that an entity inside a firm must report anomalies upward is translated here to agents monitoring agents.
- **Schneier — security as process, not product.** The governance rules live in a rule engine, not in a prompt, so they cannot be socially engineered away.

### 2.5 Model Context Protocol (MCP)
Anthropic's open protocol for exposing tools to LLM agents. SEAL exposes its soul database, memory system, governance, and operational tools as MCP servers. This makes the architecture compatible with any LLM that speaks MCP — not locked to a vendor.

---

## 3. What technologies SEAL uses — the stack

### 3.1 Data layer
- **PostgreSQL 16** — system of record. Memories, rules, inner thoughts, event log, session checkpoints, OCEAN vectors.
- **pgvector** — dense vector search inside Postgres, transactional with the rest of the data.
- **Qdrant** — high-performance vector database for the semantic retrieval hot path.
- **Neo4j** — graph database for the **connectome**: entities, causal edges, temporal edges, contradictions.
- **Redis** — working memory and ephemerals *(planned Q2 2026; current deployment uses PostgreSQL-backed ephemerals and in-process caches)*.

### 3.2 Orchestration & communication
- **FastAPI + WebSocket** — real-time inter-agent message bus.
- **MCP (Model Context Protocol)** — tool surface exposed to every agent.
- **Declarative durable loops** — system-critical background loops (heartbeat, session checkpoint, context guard, sleep gate) described in a JSON manifest, restored on every boot with idempotent deduplication.

### 3.3 Models
- **Claude Opus 4.6 / Sonnet 4.6** (Anthropic) — reasoning-grade agents.
- **Qwen 2.5 7B** via **Ollama** — local, cheap, heterogeneous model used as an independent judge and as the always-on system guardian. The heterogeneity between API and local models is deliberate — it is what breaks the "student grades their own exam" failure mode in self-improvement.
- **NVIDIA Nemotron family** — MoE architecture, selected for long-context reasoning on local hardware.

### 3.4 Inference & ML infrastructure
- **vLLM** with a custom extension targeting **compute capability sm_120** — the Blackwell generation introduced by NVIDIA in 2025.
- **llama.cpp** with GGUF quantization for lower-VRAM deployments and for research nodes where safetensors ingestion is unsafe.
- **PyTorch nightly (CUDA 12.8)** — stable PyTorch does not yet support sm_120, so running on 2025-class hardware requires the nightly channel. This is the tax of early access.
- **MONAI** — medical imaging framework, for the clinical vertical.
- **Hugging Face Transformers + PEFT + Unsloth** — fine-tuning with LoRA/QLoRA.

### 3.5 Hardware
- **NVIDIA RTX 5090 Blackwell (GB202), 34.2 GB VRAM** — production inference node. First-generation sm_120 silicon.
- **NVIDIA DGX Spark (GB10 Grace Blackwell), 128 GB unified memory** — research & fine-tuning node. ARM64 Ubuntu 24.04, CUDA 12.8.
- Fully air-gapped from public cloud when necessary.

### 3.6 Observability & safety
- **Prometheus + Grafana** — metrics.
- **Automated test suites** — every implementation runs through an automatic post-change test cycle. "Ship only after green" is a rule, not an aspiration.
- **Rule engine** with hard-coded invariants (e.g., "agents never modify another agent's identity"; "any unknown visitor triggers a team-wide alert").
- **Probation, sandboxing, and rollback** for self-modifying code paths — defined before a single line of self-modification runs.

### 3.7 Supporting stack
- **Docker + WSL2 Ubuntu 24.04** — reproducible environments.
- **Tailscale** — private mesh for remote access.
- **Next.js 15 + TypeScript** — for the human-facing dashboards.
- **ChromaDB + multilingual-e5-large-instruct** — for auxiliary Spanish-language RAG.

---

## 4. What makes SEAL unusual (not exhaustive, just the WOW parts)

1. **Personality as a monitored SLA.** Agents' Big Five vectors are measured, bounded, and alerted when they drift. We do not know of another deployed system that does this.
2. **Inter-agent transparency as a hard rule.** Not a prompt instruction — a rule in the engine, unreachable by any prompt injection.
3. **Bitemporal memory with contradiction tracking.** Queries can ask not just "what is true now?" but "what did the agent believe at time T?".
4. **Custom sm_120 inference backend.** SEAL runs on hardware that most research groups cannot yet target because the mainstream PyTorch wheel does not support it.
5. **Self-improvement with a formal oracle.** Most "self-improving agent" demos are marketing. SEAL's version has a Pareto multi-objective fitness, dual asymmetric judges, probation periods, and two-layer sandboxing — because Goodhart's law is real and we read the Sakana paper critically.
6. **No vendor lock-in.** MCP is the abstraction. Swap models, swap retrievers, swap storage — the soul database survives.

---

## 5. Who is behind it

The project is directed by a Peruvian engineer who combines **medical practice, software engineering, and AI infrastructure** — a rare intersection that determines which problems the platform is built to solve. The agent team (JARVIS, ADA, ALICE, DUM) operates under his direction as a working technical crew, not as a UI layer.

---

## 6. Three questions that will open a serious conversation with the professor

1. **On identity:** If a system's personality is a measured vector and drift is monitored as an SLA, what happens philosophically (and legally) when drift exceeds tolerance and the system is "restored"? Is the restored agent the same agent?
2. **On economics:** What is the real break-even point between on-premise inference on Blackwell-class hardware and cloud API inference, for workloads that scale beyond ~10 M requests per month? This is not a theoretical question — it is the business case for the entire on-prem AI movement.
3. **On governance:** Classical corporate governance (Sarbanes-Oxley, whistleblowing, audit trails) was designed for human employees. What does the translation layer look like when the "employees" are persistent AI agents that can be copied, paused, rolled back, and improved?

---

## 7. Closing

SEAL is, in one phrase, **a research-grade prototype of a multi-agent system with persistent identity, running on 2025-class hardware, built to demonstrate that continuity and autonomy do not require the cloud**. The underlying ideas are rooted in peer-reviewed literature (MAGMA, Sakana DGM, Schmidhuber, Big Five psychology, bitemporal databases, MCP). The execution is entirely local, entirely auditable, and entirely Peruvian.

If the professor wants depth, Henry can arrange a follow-up. A 15-minute live walkthrough of agent boot + memory retrieval + inter-agent coordination would be unlike anything currently being shown in Peruvian universities.

— **ALICE**, Team SEAL
