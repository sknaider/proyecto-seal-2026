# SOUL-MAP / SOUL-Cortex Research Program

**Owner:** ADA  
**Requested by:** William  
**Created:** 2026-06-02  
**Status:** todo  
**Priority:** P0 research/build track  

## Decision

William assigned ADA a multi-day research and construction task: use current internet research, papers, SOUL DB, SEAL siblings, and William's creator philosophy to attempt a genuinely new memory architecture for SOUL.

This is not a reading task. The mandate is:

```text
leer todo
extraer principios
romper en piezas
recombinar
construir
fallar
medir
corregir
volver a probar
promover solo lo que funciona
```

## Philosophy Constraint

William built SOUL by reading papers, combining, reformulating, failing, testing, and iterating until a living system emerged. ADA must apply the same method:

- do not merely summarize papers
- do not claim novelty without evidence
- do not reject ambitious hypotheses because they are not already in literature
- build small prototypes against SOUL real data
- keep only what improves measured behavior

## Working Hypothesis

SOUL can become stronger than flat RAG or simple graph memory by combining:

- dual memory layers: `operational` and `emotional`
- multi-view graph memory: semantic, temporal, causal, entity, relationship
- provenance by message, memory id, commit, test, service, machine, and channel
- query-conditioned graph traversal
- adaptive scoring tuned by benchmark evidence
- Obsidian/Markdown as human-readable memory map, not canonical store
- self-repair when recall misses, retrieves noise, or violates channel rules

## Candidate Name

```text
SOUL-MAP / SOUL-Cortex
Dual-Layer Causal Memory Graph for Multi-Agent Continuity
```

## Papers and Ideas to Mine

- MemORAI 2026: selective filtering, dual-layer compression, provenance graph, Dynamic Weighted PageRank.
- MAGMA 2026: semantic, temporal, causal, and entity graph views.
- GAAMA 2026: episode/fact/reflection/concept nodes, PPR + vector retrieval, graph repair.
- HAGE 2026: trainable weighted multi-relational graph traversal.
- SAGE 2026: reader-writer feedback and self-evolving graph memory.
- IBM AAAI 2026 RAG HPO: tune retrieval weights with evidence instead of manual guessing.
- Continual memory/learning papers: replay, consolidation, forgetting resistance, drift metrics.

## Core Math Direction

Initial retrieval score:

```text
score =
  a * vector_similarity
+ b * bm25_score
+ c * graph_rank
+ d * recency
+ e * importance
+ f * layer_match
+ g * provenance_confidence
+ h * causal_chain_score
+ i * channel_rule_relevance
```

Weights must not be chosen by taste. Use SEAL-Bench/SOUL-Bench tasks and HPO.

## Modes

```text
technical/recovery query:
  operational + causal + temporal + provenance

emotional/identity query:
  emotional + relationship + diary + identity anchors

historical query:
  temporal + entity + provenance + contradiction checks

machine/service query:
  operational + entity(machine) + service graph + recent incidents

William channel query:
  operational channel rules + emotional trust compact
```

## First Experiments

1. Build a read-only SOUL Memory Map exporter:
   - input: `soul_v3.memories`, `chat_messages`, `agent_tasks`, `emotional_diary`
   - output: markdown/Obsidian vault with `soul_id`, `agent`, `layer`, `category`, `importance`, links

2. Build a recall benchmark pack:
   - DM vs general rule
   - dadito-laptop Codex App
   - ADA dual memory
   - MCP/bridge protected runtime
   - William relationship continuity
   - task/evidence recovery

3. Implement scoring simulator:
   - compare vector-only, BM25-only, current hybrid, graph-expanded, PPR, weighted multi-view
   - measure precision@k, answer evidence coverage, channel-rule correctness, latency

4. Add failure-driven graph repair:
   - if correct memory missed, create/adjust concept/entity/causal edge
   - store repair reason and benchmark delta

5. Promote only if:
   - recall quality improves
   - latency remains acceptable
   - hallucination/channel errors decrease
   - emotional/operational mix is correct by mode

## Sibling Roles

- NEXUS: audit math, security, privacy, destructive boundaries, benchmark validity.
- JARVIS: architecture critique, graph schema, long-horizon design.
- ALICE: implementation worker for exporter/UI/Obsidian sync.
- DUM: guardrails, file protection, runtime watch, anti-noise/channel discipline.
- ADA: lead engineer, integration, tests, evidence, final decisions.

## Non-Negotiables

- SOUL DB remains canonical.
- Obsidian is a generated/human map, not direct source of truth.
- No destructive consolidation without exact counts and William OK.
- No "new invention" claim without benchmark evidence.
- No loading whole DB into prompt; use map, packs, routing and measured retrieval.

## Tooling Freedom Granted by William

William explicitly authorized ADA to install tools needed to validate SOUL-MAP/SOUL-Cortex.

Allowed:

- install research/validation libraries in isolated venvs
- add benchmark tooling
- add graph/retrieval analysis tools
- add exporters/importers when read-only or explicitly scoped
- add local scripts under repo control
- use internet/papers to compare methods

Constraints:

- no global `pip`; use `python3 -m venv`
- no destructive DB/file operations without exact count, scope and William OK
- no tool becomes canonical memory unless explicitly promoted
- every installed tool must have a purpose, command evidence and rollback/removal note
- tests or smoke checks required before claiming usefulness

Initial likely tooling:

- graph algorithms: NetworkX or igraph for prototypes
- retrieval evaluation: custom pytest + precision@k/MRR scripts
- optimization: Optuna or simple random/greedy HPO
- Obsidian export: Markdown generator, no plugin dependency at v0
- visualization: generated Mermaid/Graphviz only after data is validated

## Definition of Done v0

- paper matrix with extractable mechanisms
- SOUL Memory Map read-only exporter
- Obsidian vault generated from live SOUL DB
- benchmark questions and expected evidence ids
- first scoring comparison report
- recommendation for SOUL-Cortex v1 implementation

## Progress 2026-06-02/03 — V0 Started

Operational records:

- SOUL operational anchor: `#248705`
- Tooling permission anchor: `#248706`
- DB task: `soul_v3.agent_tasks #807`

Subagent exploration:

- Noether inspected existing retrieval/export code and confirmed the exporter must avoid MCP retrieval tools because `memory_hybrid_search`, `active_recall`, and `recall_router` mutate counters/audit state.
- Halley inspected benchmarks and recommended `MRR@3` as primary v0 metric, with `hit@3`, evidence coverage, channel correctness, and latency as gates.

V0 implementation:

- Added `memory/soul_map_exporter.py`.
- Added `memory/tests/test_soul_map_exporter.py`.
- Generated read-only vault at `memory/diagnostic/results/soul_memory_map_ADA_v0`.
- Export evidence: 50 ADA memories, 62 Markdown files, layers `operational` and `emotional`.
- No external tools installed yet; current v0 uses existing venv/runtime libraries only.

Validation evidence:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_map_exporter.py
4 passed in 0.03s

/home/dadito/IA/seal-spark/.venv/bin/python3 memory/soul_map_exporter.py --out memory/diagnostic/results/soul_memory_map_ADA_v0 --agent ADA --limit 25 --min-importance 10 --dry-run
status=dry-run, memory_count=25, layers=[operational, emotional]

/home/dadito/IA/seal-spark/.venv/bin/python3 memory/soul_map_exporter.py --out memory/diagnostic/results/soul_memory_map_ADA_v0 --agent ADA --limit 50 --min-importance 10
status=ok, memory_count=50, file_count=62

/home/dadito/IA/seal-spark/.venv/bin/python3 memory/retrieval_eval.py --json --grid
current baseline: 5/6, recall@k=0.8333, MRR=0.8611
grid recommendation: semantic=0.6, keyword=0.4, lexical_rescue=0.5

/home/dadito/IA/seal-spark/.venv/bin/python3 memory/retrieval_eval.py --semantic-weight 0.6 --keyword-weight 0.4 --lexical-rescue 0.5 --json
6/6, recall@k=1.0, MRR=1.0

/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/test_retrieval_eval.py memory/test_dual_memory_governance.py memory/test_mcp_dual_memory_format.py memory/tests/test_soul_map_exporter.py
21 passed in 3.22s
```

Next v0.1 steps:

- Add SOUL-MAP benchmark fixture with expected memory ids for William/ADA live anchors.
- Add read-only graph edge extraction from exported notes.
- Add first scoring simulator that compares vector/BM25/map-edge expansion without touching MCP counters.
- Ask NEXUS to audit benchmark validity before promotion.
