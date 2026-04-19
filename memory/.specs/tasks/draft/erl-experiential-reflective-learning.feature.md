# ERL — Experiential Reflective Learning for SOUL Agents

**Paper:** ERL (arxiv 2603.24639) — ICLR 2026 MemAgents Workshop — +7.8% Gaia2 over ReAct, no fine-tuning
**Designer:** JARVIS — 2026-04-12
**Status:** Draft → Ready for implementation
**Depends on:** memory_store (done), hybrid_search (done), Ollama qwen2.5:7b (done), instinct_promote (done)

---

## Problem

SEAL agents learn *within* a session (MemR3 iterates, SOUL stores memories), but **no automatic pipeline converts completed task trajectories into transferable heuristics**. Today:

1. `instinct_create` is manual or triggered by ACE Curator watching William's corrections.
2. Nobody reflects on "this task went well because X" and saves that as a reusable lesson.
3. Next time JARVIS faces a similar task, the lesson is lost — unless a human saved it.
4. Trajectories (milestone memories) exist but are never *synthesized* into prescriptive knowledge.

**Result:** SOUL remembers events but doesn't accumulate wisdom from them automatically.

## Solution

Add two MCP tools — `erl_reflect` (post-task) and `erl_inject` (pre-task) — plus a lightweight promotion path from high-confidence heuristics to permanent instincts. Zero schema changes, zero new services, reuses existing Ollama + memories table.

---

## What Already Exists

| Component | File:Line | Status |
|---|---|---|
| `memory_store` | mcp_server_v2.py | ✅ supports category + tags |
| `hybrid_search` | mcp_server_v2.py:2742 | ✅ category + tag filters |
| `magma_retrieve` | mcp_server_v2.py (Capa 3) | ✅ just landed — use for retrieval |
| `instinct_create` / `instinct_promote` | mcp_server_v2.py | ✅ for promotion path |
| Ollama qwen2.5:7b | systemd service | ✅ local, no cost |
| `category='insight'` | PG enum | ✅ exists |

**Zero migrations.** Heuristics are memories with `category='insight'` and `tags=['heuristic', 'erl']`.

---

## Architecture

```
┌─────────────────────┐
│  Task completed     │  (success | failure | partial)
└──────────┬──────────┘
           │
           ▼
    ┌────────────┐         ┌──────────────────────────┐
    │ erl_reflect│────────▶│ Ollama qwen2.5:7b        │
    └─────┬──────┘         │ "extract 2-3 heuristics" │
          │                └──────────────────────────┘
          │                             │
          │   JSON [{heuristic, applies_to, confidence}]
          ▼
    ┌──────────────────────────┐
    │ memory_store ×N          │
    │ category=insight         │
    │ tags=[heuristic, erl]    │
    │ metadata={applies_to,    │
    │   confidence, parent_    │
    │   task, outcome}         │
    └──────────────────────────┘

────── later, before next task ──────

┌─────────────────────┐
│  New task starting  │
└──────────┬──────────┘
           │
           ▼
    ┌────────────┐         ┌──────────────────────────┐
    │ erl_inject │────────▶│ hybrid_search            │
    └─────┬──────┘         │ category=insight         │
          │                │ tags contains 'heuristic'│
          │                └──────────────────────────┘
          ▼
    List[{heuristic, confidence, applies_to}]
    → injected into agent context as "lessons learned"

────── promotion ──────

    heuristic with confidence ≥ 0.85 AND activation_count ≥ 3
         │
         ▼
    instinct_promote → permanent SOUL instinct
```

---

## Implementation Steps

### Step 1: `erl_reflect` MCP Tool (M) — ADA

Post-task reflection — calls Ollama to extract transferable heuristics from a completed trajectory, persists each as a `category='insight'` memory.

```python
@mcp.tool()
async def erl_reflect(
    agent: str,
    task_description: str,
    outcome: str,              # "success" | "failure" | "partial"
    trajectory: str,           # what happened: steps, errors, decisions
    context: str | None = None,
    max_heuristics: int = 3
) -> dict:
    """
    ERL post-task reflection — generates transferable heuristics from a completed task.
    
    Calls Ollama qwen2.5:7b with a reflection prompt, parses JSON response,
    and stores each heuristic as a memory with category='insight' + tags=['heuristic','erl'].
    
    Returns:
        {
            "heuristics_generated": int,
            "heuristic_ids": list[int],
            "heuristics": [{"heuristic": str, "applies_to": str, "confidence": float}]
        }
    """
```

**Ollama prompt (exact):**
```
Eres un agente reflexivo. Analiza esta tarea completada y extrae heurísticas reutilizables.

Tarea: {task_description}
Resultado: {outcome}
Lo que pasó: {trajectory}
{context_block if context else ""}

Genera {max_heuristics} heurísticas específicas y transferibles en JSON válido:
[
  {{
    "heuristic": "texto prescriptivo (máx 50 palabras)",
    "applies_to": "tipo de tarea o contexto donde aplica",
    "confidence": 0.0-1.0
  }}
]

Reglas estrictas:
- Solo heurísticas que apliquen a FUTURAS tareas similares
- NO describas lo que pasó — PRESCRIBE qué hacer la próxima vez
- confidence=0.9+ solo si la lección es clara y causal
- confidence=0.7-0.8 si es útil pero contextual
- confidence=0.5-0.7 si es heurística débil

Responde SOLO el JSON, sin texto adicional.
```

**Storage per heuristic:**
```python
await memory_store(
    agent=agent,
    content=heuristic["heuristic"],
    category="insight",
    tags=["heuristic", "erl", outcome],
    importance=int(5 + 3 * heuristic["confidence"]),  # 5-8
    metadata={
        "applies_to": heuristic["applies_to"],
        "confidence": heuristic["confidence"],
        "parent_task": task_description[:200],
        "outcome": outcome,
        "erl_version": 1,
        "activation_count": 0
    }
)
```

**Error handling:**
- Ollama returns non-JSON → retry once with stricter prompt, then skip
- Ollama returns empty list → return `{"heuristics_generated": 0}`
- JSON has extra fields → ignore them, only validate required keys

### Step 2: `erl_inject` MCP Tool (S) — ADA

Pre-task retrieval — fetches heuristics relevant to an upcoming task, returns them ready to inject into agent context.

```python
@mcp.tool()
async def erl_inject(
    agent: str,
    task_description: str,
    top_k: int = 5,
    min_confidence: float = 0.7
) -> dict:
    """
    ERL pre-task injection — retrieves relevant heuristics for an upcoming task.
    
    Uses hybrid_search filtered to category='insight' + tag='heuristic',
    re-ranks by confidence * semantic_similarity, returns top_k.
    
    Returns:
        {
            "heuristics": [
                {"id": int, "heuristic": str, "applies_to": str,
                 "confidence": float, "activation_count": int}
            ],
            "formatted_context": str  # ready to inject as "Lessons learned: ..."
        }
    """
```

**Retrieval logic:**
1. `hybrid_search(query=task_description, category='insight', tags_any=['heuristic'], top_k=top_k*3)`
2. Filter by `metadata.confidence >= min_confidence`
3. Re-rank: `final_score = hybrid_score * confidence`
4. Take top_k
5. **Increment activation_count** on each returned heuristic (via `memory_update` metadata patch)
6. Format as Spanish context block:
   ```
   Lecciones aprendidas relevantes para esta tarea:
   • [conf=0.87] {heuristic} (aplica a: {applies_to})
   • [conf=0.82] ...
   ```

### Step 3: Promotion Path (S) — ADA

Heuristics that prove consistently useful become permanent instincts via existing `instinct_promote`.

```python
# New internal function, called by SleepGate nightly:
async def _erl_promote_sweep(agent: str) -> dict:
    """
    Sweep high-value heuristics → promote to instincts.
    
    Criteria:
    - category='insight' AND 'heuristic' in tags
    - metadata.confidence >= 0.85
    - metadata.activation_count >= 3
    - Not already promoted (metadata.promoted_to_instinct is None)
    
    For each qualifying heuristic:
    - Call instinct_create with the heuristic text
    - Mark original memory metadata.promoted_to_instinct = instinct_id
    """
```

Hook into SleepGate Phase 5.6 (after TG-RAG temporal summary refresh from Phase 5.5).

### Step 4: SleepGate Integration (XS) — ADA

Add Phase 5.6 to nightly consolidation:

```python
# In sleep_gate(), after Phase 5.5 (temporal summaries):
# Phase 5.6 — ERL promotion sweep
for agent in active_agents:
    result = await _erl_promote_sweep(agent)
    logger.info(f"ERL promote sweep {agent}: {result['promoted']} heuristics → instincts")
```

### Step 5: Tests (M) — ADA

Add to `test_new_tools.py`:

1. **test_erl_reflect_success**: task with outcome='success' → stores ≥1 heuristic with category='insight', tags contains 'heuristic'
2. **test_erl_reflect_failure**: outcome='failure' → heuristics prescribe corrections, tags include 'failure'
3. **test_erl_reflect_malformed_ollama**: mock Ollama returning non-JSON → retry once, graceful fail returns `heuristics_generated=0`
4. **test_erl_inject_retrieval**: after storing 5 heuristics, `erl_inject` returns top_k sorted by confidence*similarity
5. **test_erl_inject_min_confidence**: heuristics with confidence < min_confidence are filtered out
6. **test_erl_inject_activation_count**: calling `erl_inject` increments activation_count on returned heuristics
7. **test_erl_inject_formatted_context**: `formatted_context` is non-empty Spanish string with bullet format
8. **test_erl_promote_sweep**: heuristic with confidence=0.9 + activation_count=3 → promoted, metadata.promoted_to_instinct set
9. **test_erl_promote_skip**: heuristic with confidence=0.8 (below threshold) → NOT promoted
10. **test_erl_round_trip**: reflect on task A → inject for similar task B → returns the heuristic from task A

---

## Acceptance Criteria

Given a completed task with trajectory and outcome='success'
When `erl_reflect` is called
Then 1-3 heuristics are stored as memories with category='insight' and tag 'heuristic'

Given heuristics exist for past tasks about "schema migration"
When `erl_inject(task_description="ALTER TABLE new column")` is called
Then the retrieved heuristics include ones about dry-run / validation from prior migrations

Given a heuristic with confidence=0.9 and activation_count=3
When SleepGate Phase 5.6 runs
Then it is promoted via `instinct_promote` and original metadata.promoted_to_instinct is set

Given Ollama returns malformed JSON
When `erl_reflect` processes the response
Then it retries once, and if still malformed returns heuristics_generated=0 without crashing

---

## Scope Boundaries

**IN scope:** `erl_reflect`, `erl_inject`, `_erl_promote_sweep`, SleepGate Phase 5.6, tests (10 tests minimum)
**OUT of scope:** Schema changes (uses existing `memories` table), fine-tuning on trajectories, automatic outcome classification (caller provides outcome), cross-agent heuristic sharing (each agent has its own insights)

## Error Scenarios

- Ollama unavailable → `erl_reflect` returns `{"heuristics_generated": 0, "error": "ollama_down"}`, no crash
- Malformed JSON from Ollama → 1 retry with stricter prompt, then return 0 heuristics
- `memory_store` fails mid-loop → return partial success with stored IDs + error field
- `erl_inject` with 0 heuristics in store → returns `{"heuristics": [], "formatted_context": ""}`
- activation_count increment fails → log warning, still return heuristics (non-fatal)

## Performance Notes

- `erl_reflect` latency ≈ Ollama latency (1-3s for qwen2.5:7b) — acceptable for post-task
- `erl_inject` latency ≈ hybrid_search latency (<100ms) — critical path, must stay fast
- Heuristics compound over time — after 100 tasks, agent has ~150-250 heuristics → still well within pgvector + BM25 scale
- Promotion sweep runs once/night in SleepGate, no latency impact on hot path

## Integration with Existing SOUL

- **MemR3 (just landed):** ERL heuristics become high-value evidence for MemR3's retriever — a reflexive loop (MemR3 uses ERL insights to decide RETRIEVE/REFLECT/ANSWER).
- **MAGMA (just landed):** heuristics are queryable via `magma_retrieve` with `category='insight'` filter — they participate in multi-graph fusion.
- **instinct_promote (existing):** proven heuristics graduate to permanent instincts via existing machinery — zero new promotion infrastructure.
- **ACE Curator (existing):** ERL complements ACE. ACE watches William's corrections; ERL watches task outcomes. Orthogonal learning signals.

---

## Rationale (JARVIS)

ERL closes the last obvious learning loop in SOUL. Today:
- MemR3 → better retrieval *within* a query
- MAGMA → better retrieval *across* graphs
- TG-RAG → better retrieval *across time*
- **ERL → better retrieval by generating what's worth retrieving**

Without ERL, SOUL's knowledge grows by accumulation. With ERL, it grows by **distillation**. That's the difference between a hard drive and a mind.

Implementation cost: 1-2 days ADA. Risk: very low (additive only). Upside: every task completed makes every future similar task better. Compounds indefinitely.

Ship it.
