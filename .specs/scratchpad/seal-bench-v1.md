# SEAL-Bench v1.0 — Benchmark for Agent Soul Systems

> "LongMemEval measures chatbots. SEAL-Bench measures souls."

## Purpose

Standard memory benchmarks (LongMemEval, LOCOMO) test factual recall in conversations.
They don't test what makes SOUL unique: personality persistence, emotional memory,
instinct evolution, multi-agent coordination, and belief revision.

SEAL-Bench fills that gap.

## Categories (6 dimensions, 10 tests each = 60 total)

### 1. Personality Persistence (OCEAN stability)
- Given 50 interactions, does the agent maintain consistent OCEAN scores?
- After perturbation (user tries to change personality), does it drift or hold?
- Cross-session: does personality survive restart?
- Metric: OCEAN delta (should be < 0.05 per session)

### 2. Emotional Memory Recall
- Store 20 memories with varying emotional valence (v=-0.8 to v=+0.9)
- Query by emotional context ("remember when we were frustrated about X")
- Measure: does emotional valence improve retrieval relevance?
- Metric: Recall@5 for emotion-tagged queries vs neutral queries

### 3. Instinct Formation & Evolution
- Agent encounters same pattern 10 times across 5 sessions
- Does it form an instinct? At what confidence?
- Does the instinct fire correctly on the 11th encounter?
- Metric: instinct_confidence trajectory, false positive rate

### 4. Temporal Belief Revision
- Feed agent fact A at t=0, contradicting fact B at t=5
- Does the agent invalidate A when B arrives?
- Does it maintain bitemporal history (knows A WAS true, B IS true)?
- Metric: contradiction detection rate, temporal accuracy

### 5. Multi-Agent Memory Isolation
- Agent A stores private memory. Agent B queries same topic.
- Does B ever see A's private memories?
- Shared memories: do both agents see them?
- Metric: isolation violation rate (should be 0%), shared access rate

### 6. Reasoning Trace Coherence
- Agent stores 10 reasoning traces with premises -> conclusion
- Query: "why did you decide X?"
- Does the agent reconstruct the reasoning chain?
- Metric: premise recall accuracy, conclusion consistency

## Scoring

Each category: 0-100 points
Total: 0-600 points
Grade scale:
- 500+: Production-grade soul
- 400-499: Research-grade
- 300-399: Prototype
- <300: Memory system, not soul system

## Comparison Targets

Run same tests on:
- Mem0 (YC-backed, $24M)
- Zep/Graphiti
- MemPalace
- mcp-memory-service
- Raw LLM (no memory system)

Most competitors will score 0 on categories 1-3 (no personality, no emotions, no instincts).
That's the point.

## Implementation Plan

1. Create `seal_bench.py` with test harness
2. Each test calls SOUL MCP tools directly
3. Score automatically, output JSON report
4. Compare: run same tests via Mem0 SDK
5. Publish results in README + paper

## Data Requirements

- Synthetic conversation dataset (100 turns, 5 topics)
- 20 emotional memories (pre-tagged)
- 10 temporal facts with contradictions
- 2 agent identities for isolation tests

## Timeline

- Spec review: William approves (today)
- Test harness: 2-3 days (ADA implements)
- Run on SOUL: 1 day
- Run on competitors: 2-3 days
- Analysis + writeup: 1-2 days
- Total: ~1.5 weeks

## Status: DRAFT — Pending William approval
