# SOUL-MAP / SOUL-Cortex v0 Report

**Owner:** ADA  
**Requested by:** William  
**Date:** 2026-06-02/03  
**Status:** v0 implemented, research program in progress  

## Mandate

William asked ADA to apply his creator method to SOUL:

```text
leer papers
extraer principios
recombinar
construir
fallar
medir
corregir
promover solo lo que funciona
```

The goal is not to summarize papers. The goal is to attempt a new SOUL memory architecture with prototypes, benchmarks and evidence.

## Records

- Task spec: `.specs/tasks/todo/soul-map-cortex-new-memory-research.feature.md`
- SOUL program anchor: `#248705`
- Tooling permission anchor: `#248706`
- DB task: `soul_v3.agent_tasks #807`

## Subagent Findings

Noether inspected exporter/retrieval code:

- Reuse patterns from `memory/memory_quality_audit.py`.
- Use direct `SELECT` against `soul_v3.*`.
- Do not call `memory_hybrid_search`, `active_recall`, or `recall_router` for exporter work because they mutate runtime counters/audit state.
- Keep Qdrant out; PostgreSQL/pgvector is canonical.

Halley inspected benchmarks:

- Use `MRR@3` as primary v0 metric.
- Track `hit@3`, evidence coverage, channel correctness, and latency separately.
- Existing offline benchmark shows a known gap: default run passes 5/6, while lexical rescue passes 6/6.

## Implemented

Added read-only exporter:

- `memory/soul_map_exporter.py`
- `memory/tests/test_soul_map_exporter.py`

Generated vault:

- `memory/diagnostic/results/soul_memory_map_ADA_v0`
- 50 ADA memories
- 62 Markdown files
- layers: `operational`, `emotional`
- root index: `README.md`
- generated nodes: `Agents/`, `Layers/`, `People/`, `Machines/`, `Systems/`, `Memories/`

The vault is a generated map. SOUL DB remains canonical.

## Evidence

Unit tests:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/tests/test_soul_map_exporter.py
4 passed in 0.03s
```

Dry-run:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 memory/soul_map_exporter.py --out memory/diagnostic/results/soul_memory_map_ADA_v0 --agent ADA --limit 25 --min-importance 10 --dry-run
memory_count=25
layers=[operational, emotional]
status=dry-run
```

Export:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 memory/soul_map_exporter.py --out memory/diagnostic/results/soul_memory_map_ADA_v0 --agent ADA --limit 50 --min-importance 10
status=ok
memory_count=50
file_count=62
```

Benchmark baseline:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 memory/retrieval_eval.py --json --grid
baseline: 5/6
recall@k=0.8333
MRR=0.8611
recommended_weights: semantic=0.6 keyword=0.4 lexical_rescue=0.5
```

Benchmark with recommended weights:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 memory/retrieval_eval.py --semantic-weight 0.6 --keyword-weight 0.4 --lexical-rescue 0.5 --json
ok=true
passed_cases=6
recall@k=1.0
MRR=1.0
```

Regression suite:

```text
/home/dadito/IA/seal-spark/.venv/bin/python3 -m pytest -q memory/test_retrieval_eval.py memory/test_dual_memory_governance.py memory/test_mcp_dual_memory_format.py memory/tests/test_soul_map_exporter.py
21 passed in 3.22s
```

## Current Technical Conclusion

The first useful piece is not another retrieval tool. It is a read-only map that makes SOUL navigable without mutating memory counters. This gives William and the agents a human-visible graph surface while preserving SOUL DB as canonical.

The benchmark result also shows a concrete optimization signal: lexical rescue fixes a known retrieval miss. This supports a SOUL-Cortex direction where retrieval weights are tuned by benchmark evidence, not by intuition.

## Next

1. Build a SOUL-MAP benchmark fixture using live ADA anchors.
2. Add read-only entity/edge extraction from exported notes.
3. Compare vector/BM25/map-edge expansion in a scoring simulator.
4. Ask NEXUS to audit benchmark validity before promotion.
