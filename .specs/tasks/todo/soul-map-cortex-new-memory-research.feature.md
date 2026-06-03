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

## Progress 2026-06-02/03 — V0.1 Validated

William instructed ADA to use subagents as support by default for this task. ADA spawned Anscombe to review v0.1 implementation risks while continuing implementation. Anscombe found two high-risk benchmark issues: expected ids were being forced into the candidate pool, and the first queries were too close to anchor text. ADA changed the benchmark so default evaluation no longer injects expected ids, and replaced the cases with harder William-style paraphrases.

Implemented:

- Added graph edge generation to `memory/soul_map_exporter.py`.
- Added `Graph/edges.json` and `Graph/edges.md` outputs in generated vaults.
- Added SOUL skills export from `soul_v3.skills`, merged with local filesystem `SKILL.md` files.
- Added SOUL tools export from `soul_v3.agent_tools_registry`.
- Added `Skills/`, `Tools/`, `ToolCategories/`, and placeholder nodes for every edge target.
- Added `memory/soul_map_benchmark.py`.
- Added benchmark tests in `memory/tests/test_soul_map_benchmark.py`.
- Extended exporter tests for edge generation, word-boundary link extraction, skills, tools, and skill merge priority.

Validation evidence:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_map_exporter.py memory/tests/test_soul_map_benchmark.py
13 passed in 0.04s

/home/dadito/IA/seal-spark/.venv/bin/python3 -m memory.soul_map_benchmark --json
7/7, hit@3=1.0, MRR=0.7619

/home/dadito/IA/seal-spark/.venv/bin/python3 memory/soul_map_exporter.py --out memory/diagnostic/results/soul_memory_map_ADA_v0 --agent ADA --limit 75 --min-importance 10 --tool-limit 80 --allow-overwrite
status=ok, memory_count=75, skill_count=310, tool_count=24, edge_count=793, file_count=453

/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/test_retrieval_eval.py memory/test_dual_memory_governance.py memory/test_mcp_dual_memory_format.py memory/tests/test_soul_map_exporter.py memory/tests/test_soul_map_benchmark.py
30 passed in 3.45s

/home/dadito/IA/seal-spark/.venv/bin/python3 -m py_compile memory/soul_map_exporter.py memory/soul_map_benchmark.py
OK

python3 scripts/seal_core_guard.py --health
status=GREEN; MCP 8771 ok; WebChat 8765 ok; Codex app server 8772 ok; bridge/monitor/MCP services active; Qdrant retired.

Generated graph integrity check:
edges=793, missing_targets=0, skill_files=310, tool_files=24
```

Known v0.1 caveat:

- The benchmark now uses harder paraphrases and no default expected-id injection, so it is a credible regression gate. It is still not proof of general recall improvement because several anchors rank 2 or 3 and margins are negative. Next step is adversarial negatives plus a map-edge reranker that improves MRR, not just hit@3.
- Skills/tools coverage was corrected after subagent review: tools cover the full current registry (`24/24`), and skills now preserve duplicate names across agents by using `agent + name` note identities. The current export includes `310` skill nodes from SOUL DB plus local `SKILL.md` discovery.

## Progress 2026-06-02/03 — V0.2 Facet Reranker

William asked ADA to continue and requested results translated. ADA spawned Schrodinger for a read-only review of why the benchmark did not rank all anchors first. The review confirmed the scorer was too lexical: it found related memories, but did not distinguish the requested kind of memory.

Implemented in `memory/soul_map_benchmark.py`:

- `intent_score`: detects query/memory facets for channel rules, emotional presence, and delivered artifacts.
- `recency_score`: normalized timestamp signal to break ties toward fresher operational anchors.
- `category_score`: small category prior for `milestone`, `operational_anchor/rule`, and emotional categories.
- New hard-negative tests so lookalike memories are demoted without using expected ids in scoring.

Validation evidence:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_map_benchmark.py memory/tests/test_soul_map_exporter.py
16 passed in 0.04s

/home/dadito/IA/seal-spark/.venv/bin/python3 -m memory.soul_map_benchmark --json
7/7, hit@3=1.0, MRR=1.0
```

Translation:

- Before v0.2: SOUL-MAP found the right memory in the top 3, but not always first (`MRR=0.7619`).
- After v0.2: all 7 live anchors rank first (`MRR=1.0`) without expected-id injection.

## Progress 2026-06-02/03 — V0.3 Native Cognitive Retrieval Graph

William clarified that he wanted new native SOUL graphs, not only a reranker. ADA implemented the first native graph inside the read-only benchmark before touching runtime/MCP.

Implemented in `memory/soul_map_benchmark.py`:

- `SoulFacetGraph`: in-memory graph rebuilt read-only from SOUL rows.
- Facet nodes for channels, surfaces, machines, people, relationships, emotional state, delivery state, artifacts, programs, tooling, evidence, layer, category, and extracted map links.
- Weighted `memory -> facet` edges represented by `FacetEdge`.
- Query facet extraction and `path_score(query, memory)` using weighted paths.
- Mandatory mismatch penalties: channel-rule queries penalize memories missing required channel facets; delivery queries penalize memories without delivered artifact state; identity-presence/tooling queries penalize missing required facets.
- Cached graph/IDF/row token sets reused across all benchmark cases.
- New tests for query facet extraction, memory facet extraction, path-score hard negatives, and `graph_path_score` exposure.

Validation evidence:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_map_benchmark.py memory/tests/test_soul_map_exporter.py
22 passed in 0.04s

/home/dadito/IA/seal-spark/.venv/bin/python3 -m memory.soul_map_benchmark --json
7/7, hit@3=1.0, MRR=1.0
latency range: ~47-50 ms/case with 400 candidate memories
```

Translation:

- V0.3 is the first native SOUL cognitive retrieval graph.
- It is not only an Obsidian/static map. It creates live recovery paths: `query -> facet nodes -> memory`.
- It still stays read-only; SOUL DB remains canonical.
- Limitation: latency is improved from the first graph pass (~110 ms/case to ~47-50 ms/case) but still above the subagent ideal of `<20 ms/case`. Next optimization target is precomputed facet caches or a shared runtime module.

## Progress 2026-06-02/03 — V0.4 Sub-20ms Graph Ranking

William set the next target: reach `20 ms`. ADA optimized the native graph path without changing the SOUL DB contract.

Implemented:

- Cached `memory_links` inside `SoulFacetGraph`.
- Added `map_score_from_links` so map scoring uses precomputed link sets.
- Added `intent_score_from_facets` so intent scoring uses graph facets instead of scanning raw memory text for every row.
- Added `category_score_from_facets`.
- Reused query facets/query links/query tokens once per ranking call.
- Added tests proving cached facet/link scoring is used.

Validation evidence:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_map_benchmark.py memory/tests/test_soul_map_exporter.py
24 passed in 0.04s

/home/dadito/IA/seal-spark/.venv/bin/python3 -m memory.soul_map_benchmark --json
7/7, hit@3=1.0, MRR=1.0
latency range: ~1.5-2.1 ms/case with 400 candidate memories
```

Translation:

- Target was `<20 ms/case`.
- V0.4 reached roughly `2 ms/case`, about 10x under target.
- The key optimization was: text is converted to SOUL facets once, then ranking walks cached graph paths instead of rescanning memory content.

## Progress 2026-06-02/03 — V0.5 Compiled Graph Hot Path

William asked whether ADA needed help to reach `~1 ms`. ADA spawned Faraday for a read-only risk review and implemented a compiled ranking path while preserving the readable path.

Implemented:

- `CompiledSoulFacetGraph`: precompiles ids, layers, categories, importance, recency, row tokens, memory facets, memory links, IDF, and the base `SoulFacetGraph`.
- `rank_top`: hot path that keeps only top-k with a heap and creates `RankedMemory` only for final top results.
- `evaluate_case_compiled`: one-pass scoring for top-k, margin and expected detection; exact rank fallback only when expected is outside top-k.
- Benchmark now uses compiled path by default.
- Added tests for compiled/readable top equivalence, expected outside top-k, and tie-break by `(score, importance, id)`.

Validation evidence:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_map_benchmark.py memory/tests/test_soul_map_exporter.py
27 passed in 0.04s

/home/dadito/IA/seal-spark/.venv/bin/python3 -m memory.soul_map_benchmark --json
7/7, hit@3=1.0, MRR=1.0
single run latency range: ~0.69-1.03 ms/case

5-run hot benchmark:
max per run: 0.979, 1.005, 0.981, 0.967, 0.998 ms
avg per run: 0.832, 0.842, 0.831, 0.828, 0.833 ms
summary: max_of_max=1.005 ms, avg_of_avg=0.833 ms
```

Translation:

- V0.5 reached the `~1 ms` target for hot ranking.
- Average hot ranking is now under 1 ms.
- Worst observed sample was `1.005 ms`, effectively at the boundary.
- Quality stayed unchanged: `MRR=1.0`.

## Progress 2026-06-02/03 — V0.6 Runtime Shadow Module

William approved the next step: promote the graph out of the benchmark toward SOUL runtime. ADA implemented this as a shadow-only integration first.

Implemented:

- Added `memory/soul_cognitive_graph.py`.
- Exposed reusable read-only APIs:
  - `extract_facets`
  - `SoulFacetGraph`
  - `CompiledSoulFacetGraph`
  - `shadow_rank_memories`
- Updated `memory/soul_map_benchmark.py` so the compiled benchmark path uses the module.
- Added `SOUL_COGNITIVE_GRAPH_SHADOW=false` default flag in `memory/recall_router.py`.
- Added `_run_cognitive_graph_shadow` after base router ranking and before formatting output.
- Shadow uses only already-fetched memory hits. It does not call `active_recall`, `memory_hybrid_search`, MCP tools, or DB writes.
- Shadow output goes only to local JSONL `memory/diagnostic/soul_cognitive_graph_shadow.jsonl` when enabled.
- Did not edit `memory/mcp_server_v4.py`.

Validation evidence:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m py_compile memory/soul_cognitive_graph.py memory/soul_map_benchmark.py memory/recall_router.py
OK

/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_cognitive_graph.py memory/tests/test_soul_map_benchmark.py memory/tests/test_recall_router_cognitive_shadow.py
23 passed in 2.59s

/home/dadito/IA/seal-spark/.venv/bin/python3 -m memory.soul_map_benchmark --json
7/7, hit@3=1.0, MRR=1.0
latency range: ~0.70-1.02 ms/case

cd memory && /home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q test_privacy_enforcement.py
22 passed, 22 subtests passed in 0.12s

python3 scripts/seal_core_guard.py --health
status=GREEN; MCP 8771 ok; WebChat 8765 ok; Codex app server 8772 ok; bridge/monitor/MCP services active; Qdrant retired.
```

Translation:

- The graph now exists as a reusable SOUL module.
- The runtime router can run it in shadow mode without changing recall answers.
- This is the safe promotion step before replacing or blending live recall ranking.
