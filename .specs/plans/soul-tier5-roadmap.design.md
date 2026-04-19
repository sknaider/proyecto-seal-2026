# SOUL Tier 5 — Evolution Roadmap Design
**Author:** ADA (Team SEAL)  
**Date:** 2026-04-06  
**Status:** Proposed — Awaiting JARVIS fraternal discussion  
**Sources:** Research Rounds 12-14 (Traces #75-77)

---

## Overview

Tier 5 is the next evolution of SOUL beyond the current Tier 4 (42 MCP tools, sleep consolidation, observation analysis). It focuses on three improvements derived from the 2026 research landscape:

1. **FadeMem Decay** — More biologically accurate instinct decay
2. **Hindsight reflect()** — Automatic belief synthesis from experience
3. **A-MAC Admission** — 5-factor memory admission gate

---

## Proposal 1: FadeMem-Inspired instinct_decay

### Problem
Current `instinct_decay` uses linear decay: `new_conf = conf - (0.01 * days_since)`  
This means ALL instincts decay at the same rate regardless of usage history.  
An instinct triggered 50 times should resist decay more than one never triggered.

### Solution
Replace with exponential decay modulated by activation frequency (FadeMem, arxiv 2601.18642):

```python
# Frequency saturation (FadeMem β component)
freq_factor = activation_count / (1.0 + activation_count)  # saturates at 1.0

# Effective lambda: high-use instincts decay slower
effective_lambda = INSTINCT_DECAY_RATE * (1.0 - 0.7 * freq_factor)

# Exponential (not linear)
new_conf = r["confidence"] * math.exp(-effective_lambda * days_since)
```

### Impact (simulated)

| Activations | Half-life (current) | Half-life (proposed) | Improvement |
|---|---|---|---|
| 0 (never)   | 69d                 | 69d                  | same        |
| 1           | 69d                 | 107d                 | +55%        |
| 5           | 69d                 | 166d                 | +140%       |
| 10          | 69d                 | 191d                 | +177%       |
| 50          | 69d                 | 221d                 | +220%       |

### Changes required
- `mcp_server_v2.py`: Add `activation_count` to SELECT in `instinct_decay()` (3 lines)
- `mcp_server_v2.py`: Replace linear formula with exponential (4 lines)
- No schema changes
- No external dependencies

---

## Proposal 2: Hindsight reflect() → reflection_synthesize Tool

### Problem
SOUL accumulates episodic memories but lacks automatic synthesis of higher-order beliefs.  
Example: ADA has 1,512+ memories about William, SEAL, and the project. But there's no mechanism to automatically derive patterns like "William prefers direct communication when stressed" from 50 individual episodes demonstrating that pattern.

### Solution
Implement a `reflection_synthesize` MCP tool inspired by Hindsight (arxiv 2512.12818):

```python
@mcp.tool()
async def reflection_synthesize(agent: str, topic: str | None = None, 
                                  max_memories: int = 20) -> str:
    """Synthesize new beliefs/observations from accumulated experiences.
    
    Retrieves recent/related memories, uses Ollama (qwen2.5:7b) to identify 
    patterns, and stores the synthesis as opinion/belief if confidence >= 0.7.
    This is the 'reflect' operation from Hindsight (arxiv 2512.12818).
    """
```

### Implementation plan
1. Retrieve top-20 recent memories for agent (or filtered by topic)
2. Prompt Ollama qwen2.5:7b: "Given these memories, what are 2-3 consistent patterns about [agent]?"
3. Parse response → extract beliefs with confidence scores
4. Store via existing opinions table if confidence >= 0.7
5. Log synthesis in inner_monologue

### Expected impact
- Reduces manual memory_store calls from William
- Agents develop beliefs from experience rather than explicit instruction
- Closer to Hindsight's 89.61% LoCoMo performance benchmark

---

## Proposal 3: A-MAC 5-Factor Memory Admission

### Problem  
Current `memory_store` admits memories based on: importance score + conflict detection.  
A-MAC (arxiv 2603.04549, ICLR 2026) shows that 5 factors better predict long-term utility.

### Solution
Add pre-admission evaluation to `memory_store`:

```python
def amac_admission_score(content: str, importance: int, 
                          recent_memories: list) -> float:
    """A-MAC: 5-factor admission gate (arxiv 2603.04549)"""
    # 1. Future utility (proxy: importance score)
    future_utility = importance / 10.0
    
    # 2. Factual confidence (proxy: is_factual flag if available)
    factual_confidence = 0.8  # default high
    
    # 3. Semantic novelty (proxy: 1 - max_similarity_to_recent)
    from sentence_transformers import util
    # ... embedding comparison ...
    semantic_novelty = 1.0 - max_similarity
    
    # 4. Temporal recency (always high for new memories)
    temporal_recency = 1.0  # new memory
    
    # 5. Content type prior (bias toward procedural/correction > chatter)
    content_type_prior = 0.9 if importance >= 7 else 0.6
    
    # Weighted sum (A-MAC default weights)
    score = (0.3 * future_utility + 0.2 * factual_confidence + 
             0.25 * semantic_novelty + 0.15 * temporal_recency + 
             0.1 * content_type_prior)
    return score
```

**Admission threshold:** 0.50 (reject if score < 0.50)

### Impact
Prevents low-novelty, low-utility memories from cluttering SOUL.  
Current issue: some sessions produce 20-30 similar memories that waste Qdrant space.

---

## Implementation Schedule

Per Autonomous Learning Protocol:
1. **Fraternal discussion:** ADA sent designs to JARVIS (2026-04-06 ~02:00 UTC)
2. **Approval required:** JARVIS response as devil's advocate
3. **Development:** Branch `feature/soul-tier5` — separate from production
4. **Testing:** sandbox with test agent "ADA_TEST"
5. **Deployment:** After William approves merged code

**Recommended priority:** Proposal 1 (FadeMem) → Proposal 3 (A-MAC) → Proposal 2 (reflect)  
Proposal 1 is minimal risk, high reward, pure formula change.  
Proposal 3 adds meaningful admission control.  
Proposal 2 requires careful prompt engineering and LLM calls.
