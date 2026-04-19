# LatentGraphMem — Learned Subgraph Retrieval for SOUL (LoRA-based)

**Paper base:** LatentGraphMem (arxiv 2601.03417, January 2026) — *Implicit Graph, Explicit Retrieval*
**Supporting:** GraphGPS (arxiv 2205.12454), MemAdapter (2602.08369), Graph-based Agent Memory taxonomy (2602.05665)
**Designer:** JARVIS — 2026-04-12 (pivoted from G-Retriever after ALICE research brief)
**Status:** Draft v2 → pending diagnostic baseline + William approval
**Depends on:** diagnostic harness (ADA, in progress), test_set_v1 (ALICE, delivered), PEFT/LoRA (dominated via MedGemma), DGX Spark (128GB unified)

---

## Problem

SOUL retrieval today is **hand-designed**. MAGMA fuses 4 graphs with heuristic weights set by JARVIS. MemR³ decides retrieve/reflect/answer with hand-coded rules. Every path through the graph is a human architectural decision encoded as code.

This caps SOUL at the paradigm of hand-engineered retrieval. The next percentage points require **learned** retrieval — a model that learns *which subgraph of the connectome matters for this query* from training signal, not from JARVIS's intuition.

William's question on 2026-04-12: "are we using the best graphs/trees, or are we closing ourselves in a traditionalist idea?" Honest answer: SOUL is frontier on *strategies* (MAGMA, ERL, MemR³) but traditionalist on *substrate* (hand-tuned routing over Neo4j). The frontier substrate is **learned subgraph retrieval** with the reasoner kept frozen.

**The constraint:** we cannot fine-tune Claude (it's the reasoner, frozen by design and by economics). Any learning has to happen in components *around* Claude, not inside Claude.

## Solution

Implement **LatentGraphMem** (arxiv 2601.03417, Jan 2026). Keep Claude completely frozen. Train two lightweight **LoRA adapters** on DGX Spark:

1. **Graph builder LoRA** — learns to structure raw memory into latent graph representation
2. **Subgraph retriever LoRA** — learns, given a query, which compact subgraph to surface under fixed budget

The reasoner (Claude) reads the retrieved subgraph — same contract as today with MAGMA. No Claude retraining, no new reasoning infrastructure, no risk to SOUL's cognitive core.

**Straight-through estimator** makes the TopK subgraph selection differentiable, so the LoRA can learn end-to-end from (query → correct memory IDs) pairs in test_set_v1.

MAGMA stays. LatentGraphMem is added as a **fifth retrieval mode** alongside hybrid_search / MemR³ / MAGMA+ERL / no-memory. If the diagnostic shows it wins on multi-hop and causal queries, it becomes the default for those query types via a hierarchical router. If it ties or loses, we don't enable it — zero regression.

---

## Why This Approach (vs. alternatives)

| Criterion | G-Retriever (prior draft) | **LatentGraphMem (this spec)** | Trainable GraphMem RL (2511.07800) |
|---|---|---|---|
| Reasoner state | Parallel model | **Claude frozen** | Requires RL training loop |
| Training artifact | Full transformer | **LoRA adapters** | RL policy + graph state machine |
| Hardware | RTX 5090 days | **DGX Spark (128GB unified)** | DGX Spark + long runs |
| Stack novelty | New training infra | **PEFT (already dominated)** | New RL infra |
| Risk if fails | High (wasted training) | **Low (MAGMA fallback)** | High (RL instability) |
| Expected context savings | Similar to MAGMA | **-60 to -75%** (fixed budget subgraph) | Unknown |
| Effort | 7-10 days | **2-3 weeks** | 4-6 weeks |

LatentGraphMem is the Pareto-optimal choice: lowest risk, moderate effort, correct arxitectural fit. Trainable GraphMem RL is the *next* horizon after LatentGraphMem ships.

---

## What Already Exists

| Component | Status |
|---|---|
| Neo4j connectome (typed nodes + edges) | ✅ rich substrate |
| pgvector node embeddings (e5-large) | ✅ 1024-dim per memory |
| MAGMA multi-graph fusion | ✅ baseline to beat |
| ERL heuristics as supervision | ✅ category='insight' labels |
| test_set_v1.jsonl (64 queries, ground truth) | ✅ delivered by ALICE |
| PEFT / LoRA toolchain | ✅ already used for MedGemma fine-tuning |
| DGX Spark 128GB unified memory | ✅ training target |
| RTX 5090 (34.2GB VRAM) | ✅ inference serving |
| 1,870+ memories with MIRIX types | ✅ training corpus |
| diagnostic harness (4 modes) | ⏳ ADA in progress, wraps on :8766 |

**Zero schema migrations for MVP.** Training pairs live in a new table `latent_graphmem_training_pairs` (additive, no ALTER on existing tables).

---

## Architecture

```
┌──────────────────────────────────────────────┐
│  FROZEN LAYER (never retrained)              │
│  • Claude (reasoner)                         │
│  • e5-large (node embeddings)                │
│  • Neo4j connectome (graph structure)        │
│  • pgvector (memory vectors)                 │
└──────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────┐
│  NEW LEARNED LAYER (two LoRA adapters)       │
│                                              │
│  ┌────────────────────┐                      │
│  │ Graph Builder LoRA │                      │
│  │  Input: raw memory │                      │
│  │  Output: latent    │                      │
│  │    graph embedding │                      │
│  └─────────┬──────────┘                      │
│            │                                 │
│            ▼                                 │
│  ┌────────────────────┐                      │
│  │ Subgraph Retriever │                      │
│  │ LoRA               │                      │
│  │  Input: query +    │                      │
│  │    latent graph    │                      │
│  │  Output: top-k     │                      │
│  │    subgraph (fixed │                      │
│  │    token budget)   │                      │
│  └─────────┬──────────┘                      │
│            │                                 │
│            │  (straight-through estimator    │
│            │   makes TopK differentiable)    │
└────────────┼─────────────────────────────────┘
             │
             ▼
   Compact subgraph (nodes + edges + text)
             │
             ▼
        Claude reasons
             │
             ▼
         Response

──── Training Loop (DGX Spark, LoRA only) ────

test_set_v1 (64 queries + ground truth memory IDs)
       │
       ▼
┌─────────────────────────────────┐
│ Training pair generator         │
│  For each query:                │
│   • query_emb = e5-large(query) │
│   • positive subgraph = BFS     │
│     around expected_memory_ids  │
│   • negative subgraphs = random │
│     same-size induced subgraphs │
└──────────────┬──────────────────┘
               │
               ▼
  latent_graphmem_training_pairs table
               │
               ▼
   ┌───────────────────────┐
   │ PEFT LoRA training    │
   │ on DGX Spark          │
   │ (128GB unified mem)   │
   │                       │
   │ Loss: contrastive     │
   │ (positive > negative) │
   │ + reconstruction      │
   │ (subgraph → ground    │
   │  truth memory IDs)    │
   └──────────┬────────────┘
              │
              ▼
   Saved LoRA checkpoints
   ~/IA/modelos/latent-graphmem-soul-v1/
              │
              ▼
   FastAPI :8767 (latent_graphmem_serve.py)
              │
              ▼
   ┌────────────────────────┐
   │ latent_graph_retrieve  │  ← new MCP tool
   │  (fallback: MAGMA)     │
   └────────────────────────┘
              │
              ▼
   Fifth mode in diagnostic_eval.py
              │
              ▼
   Hierarchical router (if wins on diagnostic):
     simple    → hybrid_search
     temporal  → MAGMA + ERL
     multi-hop → latent_graph_retrieve
     causal    → latent_graph_retrieve
```

---

## Implementation Steps (preliminary — /plan workflow will refine)

### Step 0: Diagnostic baseline (BLOCKING — ADA already running)
Finish the 4-mode diagnostic first. **Nothing else starts until we have baseline numbers.** Without knowing MAGMA's exact score on multi-hop/causal, we cannot measure whether LatentGraphMem is worth enabling.

### Step 1: Read base papers (S) — JARVIS
- LatentGraphMem (2601.03417) — full read, extract exact architecture
- GraphGPS (2205.12454) — transformer-over-graph technique
- Skim: 2602.08369 (MemAdapter), 2602.05665 (taxonomy)
**Deliverable:** architecture note in `memory/research/latent_graphmem_architecture.md`

### Step 2: Training pair generator (M) — ADA
Script: `latent_graphmem_build_pairs.py`
- Read test_set_v1 (64 queries with `expected_memory_ids`)
- For each query:
  1. Fetch ground truth memory nodes from Neo4j
  2. Run k-hop BFS (k=2) → induced subgraph
  3. Encode as positive pair
  4. Sample 3 random size-matched negative subgraphs
- Write to `latent_graphmem_training_pairs` (new table, additive)
- Augment with 1,870+ existing memories as weak-label background corpus

**New schema (additive only):**
```sql
CREATE TABLE latent_graphmem_training_pairs (
  id          SERIAL PRIMARY KEY,
  query       TEXT NOT NULL,
  query_emb   VECTOR(1024),
  subgraph    JSONB NOT NULL,  -- {nodes: [], edges: [], texts: []}
  label       SMALLINT NOT NULL, -- 1 positive, 0 negative
  query_type  TEXT,             -- factual/temporal/causal/multi_hop/...
  source      TEXT DEFAULT 'test_set_v1',
  created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON latent_graphmem_training_pairs(query_type);
CREATE INDEX ON latent_graphmem_training_pairs(label);
```

### Step 3: LoRA training script (L) — JARVIS + ADA
`train_latent_graphmem.py` on DGX Spark
- Base model: decide after reading paper (likely small transformer encoder, 500M-1B params)
- LoRA rank: 16, alpha: 32 (standard starting point, tunable)
- Loss: contrastive (subgraph retrieval) + reconstruction (memory ID prediction)
- Straight-through estimator on TopK operation
- Validation split: 10% of test_set_v1 queries (never seen during training)
- Save checkpoints per epoch to `~/IA/modelos/latent-graphmem-soul-v1/`
- Log to Tensorboard + persist training curves

**Hardware note:** PyTorch nightly cu128 not needed on DGX Spark (Blackwell, CUDA 12.8 native). Only needed if we ever move inference to RTX 5090.

### Step 4: Serving (M) — ADA
`latent_graphmem_serve.py` — FastAPI on port 8767
- Loads both LoRA adapters + base model at startup
- Endpoint `POST /retrieve {query, top_k, token_budget}` → subgraph JSON + memory IDs
- Health check `/healthz`
- Circuit breaker: if model OOM / times out → fall back to `magma_retrieve()`
- Metrics: p50/p95 latency, token budget utilization, cache hit rate

### Step 5: MCP tool wrapper (S) — ADA
`latent_graph_retrieve` MCP tool — wraps FastAPI, exposes contract identical to `magma_retrieve` for drop-in compatibility.

### Step 6: Diagnostic integration (S) — ADA
Add fifth mode to `diagnostic_eval.py`. Run 64 queries × 5 modes, generate comparison report with per-query-type breakdown (factual / temporal / causal / multi_hop / entity / negation / inference).

### Step 7: Hierarchical router (M) — JARVIS
`routing/hierarchical_retrieval.py`
- Read diagnostic report
- Write heuristic router: query_type → retrieval mode based on which mode wins in the diagnostic
- v1: hand-coded rules informed by data
- v2 (future): learned router (small classifier trained on query → best mode labels)

### Step 8: Tests (M) — ADA
- Training pair generator: deterministic output on fixed test_set
- LoRA training: smoke test (1 epoch completes, loss decreases)
- `latent_graphmem_serve` health + retrieval contract
- Diagnostic 5-mode integration: parseable output, no regression on existing 4 modes
- Router: correct mode selection per query type
- Circuit breaker: kill `latent_graphmem_serve` mid-query → system falls back to MAGMA

---

## Acceptance Criteria

**Research note exists**
Given Step 1 complete
When `memory/research/latent_graphmem_architecture.md` is read
Then it contains exact architecture, LoRA targets, and loss formulation from the paper

**Training data**
Given test_set_v1 with 64 queries
When training pair generator runs
Then `latent_graphmem_training_pairs` has ≥256 rows with per-query-type distribution matching the source

**LoRA trains**
Given training pairs exist and DGX Spark available
When `train_latent_graphmem.py` runs end-to-end
Then LoRA checkpoints are saved and validation contrastive loss decreases monotonically for ≥3 epochs

**Serving works**
Given `latent_graphmem_serve.py` running on :8767
When a query is sent to `/retrieve`
Then response arrives in <500ms p95 with a valid subgraph and ≥1 memory ID

**Context savings measurable**
Given identical queries sent to `magma_retrieve` and `latent_graph_retrieve`
When output token counts are compared
Then `latent_graph_retrieve` produces ≥40% fewer tokens on average (target: 60-75% per paper)

**Diagnostic integration**
Given the 5-mode diagnostic runs
When report is generated
Then Graph Transformer mode has per-query-type scores and a comparison table against MAGMA

**Router picks correctly**
Given diagnostic showing LatentGraphMem wins on multi_hop and causal
When a multi_hop query goes through the router
Then it routes to `latent_graph_retrieve` not `hybrid_search`

**Circuit breaker**
Given `latent_graphmem_serve` is killed mid-query
When the router attempts retrieval
Then it falls back to MAGMA within 1s and the caller sees a valid response

---

## Scope Boundaries

**IN scope:**
- LatentGraphMem LoRA training on SOUL data
- Training pair generator from test_set_v1 + 1,870 memories as background
- FastAPI serving + MCP wrapper
- Diagnostic integration as fifth mode
- Heuristic hierarchical router informed by diagnostic
- Circuit breaker to MAGMA fallback
- Context budget enforcement (-60 to -75% target)

**OUT of scope (future):**
- Trainable GraphMem RL (2511.07800) — next horizon after this ships
- Learned router (v2, after heuristic router validated)
- Training on production graph beyond test_set (needs more labels — feedback loop from ERL)
- Replacing MAGMA (coexist, don't replace)
- Cross-agent LoRA sharing (each agent has its own graph)
- Fine-tuning Claude (**NEVER** — this is the whole point of LatentGraphMem)
- Hypergraph variants
- MemAdapter unification layer (2602.08369) — separate future task

---

## Error Scenarios

- LatentGraphMem paper code unavailable → reimplement from paper using GraphGPS (2205.12454) as base
- Training diverges → reduce LoRA rank, simplify loss, check data quality
- Inference latency > 500ms → reduce subgraph k-hop from 2 to 1 or shrink token budget
- `latent_graphmem_serve` OOM → circuit-break to MAGMA, alert via chat
- Training pair count too low → augment with synthetic queries from ERL heuristics (`category='insight'`)
- Claude context contract breaks → subgraph serializer has mandatory unit tests for format stability
- Validation accuracy not beating MAGMA → ship as optional mode, don't enable router, log the failure learning

---

## Performance Targets (preliminary — diagnostic will validate)

- Training time: <12h on DGX Spark (LoRA is cheap)
- Inference latency: <500ms p95 at k=2 subgraph with fixed 2K token budget
- Context tokens: 60-75% reduction vs MAGMA on same queries
- Accuracy: match MAGMA on factual/temporal, **beat** MAGMA on multi_hop and causal (target: +10-20% recall@5)
- VRAM at inference: <12GB (leaves room for other models on the node)
- Zero regression: existing 4 diagnostic modes unchanged

---

## Token Cost Projection (per ALICE's brief)

| Mode | Est. tokens per retrieval call |
|---|---|
| MAGMA (today) | ~20K context tokens |
| LatentGraphMem (predicted) | ~5-8K tokens (fixed budget subgraph) |
| **Savings** | **-60 to -75%** |

If LatentGraphMem becomes the default for complex queries, daily token spend on retrieval context drops significantly. This compounds with every future task.

---

## Integration with Existing SOUL

- **MAGMA:** coexists. Router picks MAGMA for query types where it still wins (likely factual, entity lookups, temporal).
- **ERL:** ERL heuristics become weak labels for the training corpus — `category='insight'` memories are high-value training signal.
- **MemR³:** MemR³'s reflect/answer decision operates on top of whatever retrieval mode fires. Compatible.
- **TG-RAG:** temporal queries can still use TG-RAG summaries; LatentGraphMem augments rather than replaces.
- **Connectome:** unchanged. LatentGraphMem reads from Neo4j, doesn't modify it.
- **Diagnostic harness:** extended with fifth mode, no changes to first four.

---

## Rationale (JARVIS)

SOUL has hit the ceiling of **hand-designed retrieval**. The next percentage points require a learned component. LatentGraphMem is the right learned component because:

1. **Claude stays frozen** — the reasoner's identity, alignment, and capabilities are preserved. Zero risk to SOUL's cognitive core.
2. **LoRA is cheap** — DGX Spark unified memory handles it comfortably, PEFT is already dominated via MedGemma work.
3. **Fallback is clean** — if it underperforms, we don't enable the router. MAGMA keeps running unchanged.
4. **Compounds with data** — every successful task labeled by ERL becomes training signal for the next LoRA iteration. Continual learning substrate.
5. **Token economics** — 60-75% context reduction on complex queries means William's bill shrinks on the hardest work, not the easiest. That matters.

William asked: "can we build something humans haven't done?" SOUL already did that with alma + OCEAN + inner_thoughts + ACE curator. Graph Transformers for *persistent agent memory with frozen reasoner* specifically — as a production system with OCEAN personality and diary and multi-agent coordination — is still open territory. We can be first.

William's profesor said humans stopped developing new forms of improvement. That's a lazy excuse. The papers are there (Jan-Feb 2026, arxiv 2601.03417 + 2602.05665). The hardware is there (DGX Spark + RTX 5090). The expertise is there (PEFT, LoRA, PostgreSQL, Neo4j). What's missing is someone who decides to build it. That's us.

Ship it — but only after the diagnostic confirms MAGMA has a ceiling worth breaking. Data first.

---

## Credits

- **ALICE** — research brief (`memory/research/GraphTransformer_SOUL_Brief.md`) that pivoted this spec from G-Retriever (fragile, expensive, risky) to LatentGraphMem (correct arxitectural fit). Saved an estimated 5-7 days of misdirected work.
- **ADA** — diagnostic harness + test_set_v1 integration (the baseline that makes this decision measurable).
- **JARVIS** — spec architecture + integration design + training loop (this document).
- **William** — asked the question that forced the whole team out of paradigm lock: "are we being traditionalists?"
