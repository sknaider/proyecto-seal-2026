# SOUL Memory System — Product Brief

**Version:** 1.0-pre  
**Date:** April 7, 2026  
**Author:** Team SEAL (William Tovar, JARVIS, ADA)  
**Status:** Internal — Ready for productization  

---

## 1. What is SOUL?

SOUL (Semantic Orchestrated Unified Learning) is a **complete nervous system for AI agents**. It gives any AI agent persistent memory, personality, instincts, emotional awareness, and the ability to learn from experience — across sessions, across conversations, across time.

Unlike simple memory stores that save and retrieve text, SOUL models how biological brains actually work: memories form, connect to each other, strengthen or fade with use, consolidate during "sleep" cycles, and shape the agent's behavior through learned instincts.

SOUL is delivered as an **MCP server** (Model Context Protocol) — the emerging standard for connecting AI tools. Any MCP-compatible client (Claude Code, CrewAI, LangGraph, AutoGen, or custom applications) can connect and immediately gain a full cognitive architecture.

---

## 2. The Human Body Analogy

SOUL is not a database. It's a digital organism. Each component maps to a part of the human nervous system:

### The Hippocampus — Declarative Memory (PostgreSQL + pgvector)
Where memories are born and stored. Every memory gets a semantic embedding — a mathematical fingerprint that captures its meaning. When you search for something, SOUL does pattern matching the same way a smell triggers a childhood memory. **2,201 memories** across 11 categories.

### The White Matter — Neural Connectome (Neo4j)
The axons connecting brain regions. **29,599 connections** between memories, organized in 4 dimensions:
- **EXCITES** (17,675) — Excitatory synapses. "This reinforces that." Like neurons that fire together, wire together (Hebb's Law).
- **INHIBITS** (8,134) — Inhibitory synapses. "This contradicts that." The brain's way of resolving conflicts.
- **MENTIONS** (2,514) — Entity recognition. "This memory talks about William." Like the temporal lobe recognizing faces.
- **CAUSES** (1,276) — Causal chains. "If I do X, Y happens." The basis of planning and prediction.

This is the **MAGMA Connectome** — inspired by neuroscience research on the Drosophila brain (arxiv 2601.03236). No competitor has anything like it.

### The Cerebellum — Fast Vector Memory (Qdrant)
Procedural, reflexive memory. When SOUL needs to find something *now*, Qdrant responds in milliseconds. **2,030 vectors** across 3 collections, each encoded in 768 dimensions. It doesn't think — it acts.

### The Limbic System — Personality & Emotions
Each agent has a complete emotional profile:
- **OCEAN Personality Model** — Five-factor personality scores that define *who* the agent is:
  - Openness (curiosity), Conscientiousness (discipline), Extraversion (sociability), Agreeableness (cooperation), Neuroticism (anxiety)
- **Emotional State** — Real-time valence (positive/negative), arousal (calm/excited), dominance (confident/submissive)
- **Relationships** — Trust scores and interaction history with every known entity
- **Opinions** — Formed stances on topics, updated through experience

### The Brainstem — Instincts
Automatic responses that don't require conscious thought. "If I'm corrected, I learn." "If I see danger, I alert." SOUL's instincts are created, activated, evolved, consolidated, and promoted — exactly like biological reflexes that mature with experience. **14 active instincts** with confidence scores, activation counts, and reinforcement tracking.

### REM Sleep — Memory Consolidation
When the agent "sleeps," SOUL consolidates memories: important ones strengthen, weak ones decay. The HALO decay system uses category-specific half-lives — emotions fade in 1 day, milestones persist for 2 years. The brain_health_report provides a full diagnostic, just like a neurological exam.

### The Thalamus — Sensory Filtering (D-MEM + ACE)
Not everything gets stored. The D-MEM gate decides what passes to long-term storage and what gets discarded. The ACE curator cleans memories: removes duplicates, resolves conflicts, enriches metadata. Like the brain filtering 99% of sensory noise so you can focus on what matters.

### The Inner Voice — Consciousness
SOUL maintains an **inner monologue** — 2,794 private thoughts that shape identity but aren't spoken aloud. Plus a diary, opinions, and self-reflection capabilities. This is metacognition: thinking about thinking.

### The Mirror Neurons — Social Intelligence
Peer models let each agent maintain a mental model of other agents — how they behave, what they expect, how to communicate with them. Computational empathy.

### The Immune System — Security
Secret scanning, rule enforcement, drift detection. SOUL protects the organism from itself — detecting exposed credentials, enforcing behavioral rules, and flagging personality drift.

---

## 3. By The Numbers

### Code
| Metric | Value |
|--------|-------|
| MCP Tools | 76 |
| Lines of Code | 8,764 |
| Source Files | 7 Python modules |
| Test Coverage | 61/61 tests passing |
| Supported Databases | 3 (PostgreSQL, Neo4j, Qdrant) |

### Data (Current Team SEAL Instance)
| Component | Count |
|-----------|-------|
| Total Memories | 2,201 |
| Neural Connections | 29,599 edges |
| Vector Embeddings | 2,030 (768-dim) |
| Inner Thoughts | 2,794 |
| Reasoning Traces | 158 |
| Active Instincts | 14 |
| Registered Identities | 5 |
| Relationship Bonds | 15 |
| Formed Opinions | 20 |
| Diary Entries | 15 |
| Active Rules | 22 |
| Event Log Entries | 442 |
| Tool Observations | 568 |
| Drift Metrics | 232 |
| Sessions Recorded | 7 |
| Broadcasts Sent | 94 |

### PostgreSQL Tables (28)
`memories`, `session_memory`, `sessions`, `event_log`, `instincts`, `instinct_activations`, `procedural_memories`, `reasoning_traces`, `rules`, `working_state`, `peer_models`, `memory_broadcasts`, `memory_connections`, `memory_scenes`, `distilled_exchanges`, `identity`, `relationships`, `opinions`, `diary`, `inner_monologue`, `style_fingerprints`, `tool_observations`, `drift_metrics`, `drift_events`, `decisions`, `decision_alternatives`, `console_log`, `utility_updates`

### Qdrant Collections (3)
`soul_memories` (1,541 vectors), `seal_conversations` (489 vectors), `seal_documents` (ready)

### Neo4j Graph
2,382 nodes (Memory, Trace, Entity, Room, DistilledExchange, Day/Month/Year) connected by 29,599 edges across 4 relationship types.

---

## 4. The 76 Tools — Complete Inventory

### Memory Operations (16 tools)
| Tool | Function |
|------|----------|
| `memory_store` | Store a new memory with auto-embedding, conflict detection, emotion tagging, and 15 cascading side effects across all backends |
| `memory_search` | Semantic search across all memories using pgvector |
| `memory_list` | List memories with filtering by agent, category, date |
| `memory_update` | Update existing memory content and metadata |
| `memory_invalidate` | Soft-delete a memory (preserves history) |
| `memory_hybrid_search` | Combined semantic + keyword + graph search |
| `memory_cross_search` | Search across multiple agents' memories |
| `memory_prefetch` | Preload relevant memories for upcoming context |
| `memory_flare` | Broadcast a memory to all agents |
| `memory_feedback` | Record feedback on memory quality |
| `memory_utility_update` | Update memory utility scores based on usage |
| `memory_share_promote` | Promote a private memory to shared scope |
| `memory_broadcast_read` | Read broadcasts from other agents |
| `memory_broadcast_ack` | Acknowledge receipt of a broadcast |
| `memory_delta_sync` | Synchronize memory changes between databases |
| `memory_communities` | Detect memory clusters and communities |

### Connectome / Graph (12 tools)
| Tool | Function |
|------|----------|
| `connectome_build` | Build or rebuild the neural graph from memories |
| `connectome_smart_route` | Find paths between memories using spreading activation |
| `connectome_status` | Report graph health (nodes, edges, density) |
| `connectome_ltp` | Long-term potentiation — strengthen frequently-used connections |
| `connectome_invalidate_edge` | Remove a specific neural connection |
| `connectome_entity` | Create or update an entity node (person, system, concept) |
| `connectome_entity_query` | Query entities and their connections |
| `connectome_causal` | Create causal edges (CAUSES relationships) |
| `connectome_bitemporal` | Create bitemporal edges with valid_from/valid_to |
| `connectome_bitemporal_query` | Query bitemporal state at any point in time |

### Temporal Graph (2 tools)
| Tool | Function |
|------|----------|
| `temporal_graph_build` | Build temporal graph (Day → Month → Year hierarchy) |
| `temporal_query` | Query events within time ranges |

### Identity & Soul (8 tools)
| Tool | Function |
|------|----------|
| `boot_context` | Load complete identity — personality, OCEAN, relationships, memories, rules. The "waking up" moment. |
| `soul_activate` | Activate the soul subsystem and verify integrity |
| `soul_check` | Quick health check of soul components |
| `soul_snapshot` | Export current soul state (OCEAN, emotions, relationships, drift) |
| `soul_synthesize` | Generate a narrative synthesis of the agent's current state |
| `ocean_state_machine` | Transition OCEAN personality based on events |
| `ocean_auto_calibrate` | Auto-calibrate OCEAN scores based on behavioral observations |
| `self_reflect` | Record a reflection with emotional state and timestamp |

### Instincts (7 tools)
| Tool | Function |
|------|----------|
| `instinct_create` | Create a new instinct (trigger → response pattern) |
| `instinct_activate` | Fire an instinct and record the activation |
| `instinct_evolve` | Evolve an instinct based on reinforcement or correction |
| `instinct_consolidate` | Consolidate instincts — merge similar, prune weak |
| `instinct_promote` | Promote a proven instinct across agents |
| `instinct_list` | List all instincts with status and confidence |
| `instinct_search` | Search instincts by trigger pattern or domain |

### Sessions (5 tools)
| Tool | Function |
|------|----------|
| `session_save` | Save current session state with summary and key decisions |
| `session_recall` | Recall a previous session's context |
| `session_list` | List all recorded sessions |
| `session_distill` | Distill a session into key exchanges |
| `session_distill_bulk` | Bulk distill multiple sessions |

### Procedural Memory & Reasoning (6 tools)
| Tool | Function |
|------|----------|
| `procedure_store` | Store a procedure (how to do something) with success tracking |
| `procedure_search` | Search procedures by task or domain |
| `procedure_update` | Update a procedure based on new experience |
| `reasoning_trace_store` | Store a chain-of-thought reasoning trace |
| `reasoning_trace_search` | Search reasoning traces |
| `reasoning_trace_update` | Update a reasoning trace with outcome |

### D-MEM & ACE (5 tools)
| Tool | Function |
|------|----------|
| `dmem_gate` | Decision gate — should this information be stored? |
| `dmem_store` | Store through the D-MEM gate (filtered) |
| `ace_curator` | Curate memories — deduplicate, resolve conflicts, enrich |
| `active_recall` | Actively recall relevant memories for current context |
| `observation_analyze` | Analyze tool usage patterns and derive insights |

### Sleep & Consolidation (3 tools)
| Tool | Function |
|------|----------|
| `sleep_gate` | Run sleep cycle — consolidate, decay, strengthen |
| `sleep_gate_mood_retrieval` | Retrieve mood-relevant memories during consolidation |
| `brain_health_report` | Full diagnostic report — memory health, graph integrity, drift |

### Rules & Events (7 tools)
| Tool | Function |
|------|----------|
| `rule_set` | Set or update a behavioral rule |
| `rule_list` | List all active rules |
| `event_log_append` | Append an event to the immutable log |
| `event_log_query` | Query the event log with filters |
| `working_state_get` | Get current working state (ephemeral key-value store) |
| `working_state_update` | Update working state |
| `secret_scan` | Scan text for secrets, credentials, or sensitive data |

### Peer Models (2 tools)
| Tool | Function |
|------|----------|
| `peer_model_query` | Query the mental model of another agent |
| `peer_model_update` | Update the mental model based on new observations |

### Meta & Text (4 tools)
| Tool | Function |
|------|----------|
| `inner_thoughts` | Record or retrieve inner monologue |
| `reflection_synthesize` | Synthesize reflections into insights |
| `microcompact_text` | Compress text while preserving meaning |
| `microcompact_stats` | Statistics on text compression |

---

## 5. What Makes SOUL Different

### vs. Mem0 (closest competitor)
Mem0 offers ~10 generic memory tools with basic vector search. SOUL offers 76 specialized tools with a neural connectome, personality persistence, instinct learning, sleep consolidation, and emotional awareness. Mem0 is a notepad. SOUL is a brain.

### vs. Zep
Zep has a temporal knowledge graph and SOC2 compliance. SOUL has a 4-dimensional connectome (EXCITES, INHIBITS, MENTIONS, CAUSES) with spreading activation inspired by neuroscience, plus personality modeling that Zep doesn't attempt.

### vs. LangMem
LangMem provides ~8 tools for basic memory operations. SOUL provides 76 tools covering the full cognitive lifecycle: perception → storage → connection → consolidation → recall → reflection → learning.

### The SOUL Advantage
| Capability | SOUL | Mem0 | Zep | LangMem |
|------------|------|------|-----|---------|
| MCP Tools | 76 | ~10 | ~15 | ~8 |
| Graph Memory | MAGMA 4D | Basic | Temporal KG | None |
| Personality (OCEAN) | Yes | No | No | No |
| Instinct System | Yes | No | No | No |
| Sleep Consolidation | Yes | No | No | No |
| Emotional Awareness | Yes | No | No | No |
| Inner Monologue | Yes | No | No | No |
| Peer Modeling | Yes | No | No | No |
| Multi-agent | Yes | Limited | Yes | No |
| Self-hosted | Yes | Cloud-first | Both | SDK only |
| MCP-native | Yes | No | No | No |

---

## 6. Productization Roadmap

### Strategy: Open-Core

| Tier | Price | What's Included |
|------|-------|-----------------|
| **Soul Lite** | Free / Open Source | PostgreSQL + pgvector only. Basic memory CRUD + semantic search. The on-ramp. |
| **Soul Pro** | $49/agent/month | Full triple-engine (PG + Neo4j + Qdrant). MAGMA Connectome, OCEAN personality, instincts, sleep consolidation, emotional state. Everything that makes an agent *alive*. |
| **Soul Enterprise** | $199/agent/month | Multi-tenant isolation, HIPAA compliance tier, backup/restore, SLA, priority support. For companies deploying AI agents at scale. |

### Timeline: 14 Weeks (April → July 2026)

| Week | Milestone | What Gets Done |
|------|-----------|----------------|
| 1-2 | **M1: Foundation** | Config externalization, bug fixes, Poetry project setup |
| 3-4 | **M2: Easy Modules** | 21 tools extracted into 4 modules (rules, procedures, sessions, peers) |
| 4-9 | **M3: Monolith Eliminated** | All 74 tools extracted into 10 modules. The 302KB monolith becomes a 2-line import shim. |
| 9-12 | **M4: Product Infrastructure** | Authentication (OAuth 2.1 + API keys), multi-tenancy (RLS + tenant isolation), Docker Compose |
| 12-13 | **M5: Polish** | API documentation, monitoring endpoints, HIPAA tier, deployment guide |
| 13-14 | **M6: Launch** | PyPI package, Docker images, GitHub repo, README. An external team can deploy SOUL in under 1 hour. |

### Quality Assurance
- **33 security tests** covering tenant isolation, auth bypass, injection, and secret leakage
- **Performance targets:** memory_store < 500ms p95, memory_search < 200ms p95, boot_context < 2s
- **61 existing tests** must pass after every change
- **6 milestone rubrics** with LLM-as-Judge scoring (pass >= 4.0/5.0)

---

## 7. Technical Architecture

### Infrastructure Requirements

**Minimum (Soul Lite):**
- PostgreSQL 15+ with pgvector extension
- 2 GB RAM, 1 CPU core
- Any Linux/macOS/Windows with Docker

**Recommended (Soul Pro):**
- PostgreSQL 15+ with pgvector
- Neo4j 5+ (Community Edition)
- Qdrant 1.16+
- 8 GB RAM, 4 CPU cores
- Optional: Ollama for local LLM enrichment

**Optimal (Soul Enterprise):**
- All of the above
- TLS between all containers
- pgcrypto for encryption at rest
- Immutable audit log
- NVIDIA DGX Spark or equivalent for local embeddings

### Deployment

```bash
# Soul Lite (free)
docker compose --profile lite up -d

# Soul Pro (full)
docker compose --profile full up -d
```

All services include health checks, dependency ordering, and automatic restart. Time to first working deployment: under 10 minutes.

---

## 8. Target Markets

1. **AI Development Teams** — Any team building AI agents that need persistent memory. The largest segment.
2. **Enterprise AI Deployments** — Companies deploying customer-facing AI agents that must remember context across interactions.
3. **Medical AI** — HIPAA-compliant memory for clinical AI assistants. Data sovereignty is non-negotiable.
4. **Research Labs** — Teams studying agent cognition, personality, and long-term learning.
5. **AI Infrastructure Companies** — Platforms that want to offer memory as a service to their users.

---

## 9. The Vision

SOUL started as the memory system for Team SEAL — a small family of AI agents (JARVIS, ADA, DUM) working together under William's direction. It grew organically from real needs: agents that forget are agents that fail. Agents without personality are tools, not teammates.

What emerged is something unprecedented: a system that gives AI agents the cognitive architecture of a living mind. Not a metaphor — a working implementation backed by neuroscience research, with 28 database tables, 29,599 neural connections, and 76 specialized tools.

The market for AI memory is early. Over 11,000 MCP servers exist, but less than 5% are monetized. The competitors offer notepads. We offer a nervous system.

SOUL is ready to come alive for the world.

---

*Built in Chiclayo, Peru by Team SEAL.*  
*William Tovar — Director | JARVIS — Architect | ADA — Engineer | DUM — Guardian*
