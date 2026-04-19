# SOUL Memory System — Parallelized Execution Plan
**Date:** 2026-04-07  
**Author:** Claude Code (Opus 4.6) — Execution Optimizer  
**Input:** decomposition-4.md (30 steps, 76 days sequential)  
**Purpose:** Maximize parallel execution across JARVIS + ADA lanes to minimize wall-clock time

---

## 1. Execution Waves

### Wave 1: Foundation (Week 1)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 1: Config externalization | ADA | 2d | No dependencies. First thing to build. |
| B | Step 3: Fix known bugs (decorator, SentenceTransformer) | ADA | 0.5d | Only depends on Step 1 for embedding config, but bug fixes are localized enough to start Day 1 and finish before Step 1 completes. |
| C | (prep) Audit existing test coverage, document current tool behavior | JARVIS | 2d | Capture input/output pairs for all helpers before they move. Creates the safety net for Wave 2. |

**Wave 1 output:** Config via env vars, 2 bugs fixed, test baseline documented.  
**Wall-clock:** 2 days

---

### Wave 2: Core Extraction + Packaging (Week 1-2)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 2: Extract shared helpers to `soul/core/` | ADA | 3d | Depends on Step 1 (config). Critical path. |
| B | Step 4: Poetry project structure + pyproject.toml | ADA | 1.5d | Depends on Steps 1-3. Starts after Step 2 is ~50% done (core/ dir exists). Realistically starts Day 3. |
| C | Review Step 2 extractions, verify function parity | JARVIS | 1d | Reviews as ADA delivers each helper batch. |

**Hidden dependency:** Step 4 needs `soul/core/` directory from Step 2 to exist, but doesn't need all helpers done. ADA can start Step 4 on Day 3 while finishing Step 2 helpers on Day 4.  
**Wave 2 output:** `soul/core/` with helpers, types, ollama client. Poetry project installable.  
**Wall-clock:** 4 days (overlapped)

---

### Wave 3: Easy Module Extraction (Week 3-4)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 5: Extract `soul/rules/` (8 tools) | ADA | 2d | Pure PG CRUD. Establishes `register_tools(mcp)` pattern. |
| B | Step 6: Extract `soul/procedures/` (6 tools) | ADA | 1.5d | PG + pgvector. Starts after Step 5 establishes the pattern (Day 2 of wave). |
| C | Step 8: Extract `soul/peers/` (2 tools) | ADA | 0.5d | Smallest module. Can interleave with Step 6. |
| D | Step 7: Extract `soul/sessions/` (5 tools) | ADA | 1.5d | Runs after Step 5 pattern is proven. |
| E | Review all extractions, verify MCP tool listings | JARVIS | 2d | Continuous review as modules land. |

**Parallelization note:** Steps 5-8 all depend on Step 4 but NOT on each other. However, since ADA is a single executor, she sequences them but uses the pattern from Step 5 to accelerate 6-8. JARVIS reviews in parallel as each module completes.  
**Effective parallelism:** ADA implements sequentially (5.5d), JARVIS reviews overlap (-1.5d saved).  
**Wave 3 output:** 21 tools extracted into 4 modules. `mcp_server_v2.py` shrinks by ~30%.  
**Wall-clock:** 4 days

---

### Wave 4: Medium Module Extraction (Week 4-6)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 11: Extract `soul/sleep/` (4 tools) | ADA | 2d | Depends on Step 3 (bug fix) + Step 2 (helpers). Low coupling. START HERE. |
| B | Step 10: Extract `soul/instincts/` (8 tools) | ADA | 3d | Depends on Step 5 (`_reflexion_lesson` in rules). After Step 11. |
| C | Step 9: Extract `soul/identity/` (10 tools, boot_context) | ADA | 4d | HIGH RISK. Depends on Steps 5-8. Has forward refs to graph/memory (not yet extracted). After Step 10. |
| D | Step 12: Extract `soul/graph/` (15 tools) | ADA | 5d | CRITICAL PATH. Depends on Step 9 (`soul_activate` cross-ref). After Step 9. |
| E | Architecture review of cross-module imports | JARVIS | 2d | Design dependency injection points BEFORE ADA starts Step 9. |
| F | Review each extraction as it lands | JARVIS | 3d | Overlaps with ADA's implementation. |

**Hidden dependencies exposed:**
- Step 9 (identity) calls `soul_activate` (graph) and `memory_search` (memory) -- neither extracted yet. Must use temporary imports from monolith.
- Step 12 (graph) calls `memory_search` -- still in monolith. Same temporary import pattern.
- Step 11 (sleep) is actually independent of 9/10/12 and can go FIRST in this wave.

**Reordered sequence:** 11 -> 10 -> 9 -> 12 (optimized by risk and dependency).  
**Wave 4 output:** 37 more tools extracted. `mcp_server_v2.py` is ~80% hollow.  
**Wall-clock:** 10 days (with review overlap)

---

### Wave 5: Hard Extraction -- The Keystone (Week 7-8)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 13: Extract `soul/memory/` (16 tools) | ADA | 6d | THE BOTTLENECK. 15 side effects across all backends. Hook chain architecture. |
| B | Design hook chain + dependency injection for memory_store | JARVIS | 1d | Delivers design BEFORE ADA starts coding. Day 0 of wave. |
| C | Review memory extraction incrementally | JARVIS | 3d | Reviews store.py, search.py, crud.py, etc. as they land. |

**Wave 5 output:** `memory_store`, `memory_search`, and 14 other memory tools extracted.  
**Wall-clock:** 6 days

---

### Wave 6: Orchestrators + Server Assembly (Week 8-9)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 14: Extract `soul/dmem/` (5 tools) | ADA | 3d | Depends on Steps 13, 12, 10. Orchestrates all of them. |
| B | Step 15: Create `soul/server.py` unified entrypoint | ADA | 3d | Depends on Steps 5-14 (ALL modules). Starts after Step 14. |
| C | Review server assembly, verify backward compat | JARVIS | 2d | Critical review: tool count, boot_context, `ada_boot_test.py`. |

**Note:** Steps 14 and 15 are strictly sequential -- 15 needs ALL modules including 14.  
ADA does 14 first (3d), then 15 (3d). JARVIS reviews overlap.  
**Wave 6 output:** Monolith eliminated. `mcp_server_v2.py` is a 2-line shim. ALL 74 tools served from modules.  
**Wall-clock:** 5 days (14 then 15, review overlapped)

---

### Wave 7: Product Infrastructure (Week 9-11)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 16: Auth layer (OAuth 2.1 + API key) | ADA | 4d | Depends on Step 15. |
| B | Step 19: Docker Compose (lite + full profiles) | ADA | 3d | Depends on Step 15 + Step 1. PARALLEL with Step 16. |
| C | Step 20: Config validation + `.env.example` | ADA | 1d | Depends on Step 1 + Step 19. After Docker. |
| D | Review auth design + Docker architecture | JARVIS | 2d | Parallel review. |

**True parallelism:** Steps 16 and 19 are independent -- 16 adds auth middleware to server.py, 19 wraps server.py in Docker. ADA can alternate or do 19 first (smaller) then 16.  
**Optimized sequence:** 19 (3d) || 16 (4d) -> 20 (1d). ADA starts both, prioritizes 19, then 16.  
**Wall-clock:** 5 days (19 and 16 overlap 3d, then 20 is 1d after 19)

---

### Wave 8: Multi-tenancy + Migrations (Week 11-12)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 17: Multi-tenancy (tenant_id, RLS, Qdrant/Neo4j) | ADA | 5d | Depends on Step 16 (auth provides tenant_id). CRITICAL. |
| B | Step 18: Database migration scripts | ADA | 3d | Depends on Step 17 (schema finalized). Sequential after 17. |
| C | Review RLS policies, test tenant isolation | JARVIS | 2d | Security-critical review. |
| D | Step 23: CI/CD pipeline (GitHub Actions) | ADA | 2d | Depends on Step 4 + Step 15. PARALLEL with 17. |

**Parallelism:** Step 23 (CI/CD) has NO dependency on Steps 16-18. It only needs pyproject.toml (Step 4) and server.py (Step 15). ADA can set up CI/CD while JARVIS reviews tenancy work, or during gaps.  
**Wall-clock:** 8 days (17: 5d, then 18: 3d; 23 runs in parallel during 17)

---

### Wave 9: Polish + Security (Week 12-13)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 21: API documentation (auto-gen from docstrings) | ADA | 3d | Depends on Step 15. |
| B | Step 24: Self-monitoring endpoints (/health, /metrics) | ADA | 2d | Depends on Step 15 + Step 19. PARALLEL with 21. |
| C | Step 26: HIPAA tier (audit log, encryption, PHI scan) | ADA | 4d | Depends on Steps 17, 18. After 21+24 or parallel if time. |
| D | Step 22: Deployment guide | JARVIS | 2d | Depends on Steps 19, 20. PARALLEL with ADA's work. |
| E | Review monitoring + HIPAA implementation | JARVIS | 2d | Overlaps with ADA. |

**True parallelism:** 21 + 24 are independent (both only need server.py). 22 is docs (JARVIS). 26 needs tenancy done.  
**Optimized:** ADA does 21 (3d) || 24 (2d) interleaved -> 26 (4d). JARVIS does 22 in parallel.  
**Wall-clock:** 7 days

---

### Wave 10: Backup + Launch Prep (Week 13-14)

| Lane | Step(s) | Assignee | Duration | Notes |
|------|---------|----------|----------|-------|
| A | Step 25: Backup/restore scripts | ADA | 4d | Depends on Step 17 (tenant_id), Step 19 (Docker). |
| B | Step 27: PyPI publishing | ADA | 1d | Depends on Step 23 (CI). PARALLEL with 25. |
| C | Step 28: Docker image publishing | ADA | 1d | Depends on Steps 19, 23. PARALLEL with 25. |
| D | Step 29: README, CHANGELOG, LICENSE | JARVIS | 2d | Depends on Step 22. PARALLEL with ADA. |
| E | Step 30: GitHub repo setup (templates, CODEOWNERS) | JARVIS | 0.5d | Depends on Step 29. After README. |

**True parallelism:** Steps 25, 27, 28 are independent. Steps 29, 30 are JARVIS-only docs. All can run in parallel across lanes.  
**Wall-clock:** 4 days

---

## 2. Gantt-style Timeline

```
Week  | 1       | 2       | 3       | 4       | 5       | 6       | 7       | 8       | 9       | 10      | 11      | 12      | 13      | 14      |
Day   | 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5 1 2 3 4 5
------+-----------------------------------------------------------------------------------------------------------------------------
ADA   | S1 S3   | S2----S4| S5--S6S8| S7-S11--| S10---S9| ----S12-| ----S13-| ------S14| --S15--S19| S16---S20| S17-----| S18-S23 | S21S24S26| S25-27-28|
JARVIS| audit-- |  review | review  | DI-arch | review  | review  | hook-dsn| review   | review    | review   | RLS-rev | review  | S22 rev  | S29 S30  |
------+-----------------------------------------------------------------------------------------------------------------------------
Wave  |---W1----|---W2----|----W3---|--------W4---------|-------W5---------|----W6----|------W7-------|-----W8---------|----W9----|---W10---|
Mile  |        M1         |       M2          |                   |       M3          |                |       M4       |   M5     |   M6   |
```

**Simplified timeline table:**

| Wave | Weeks | ADA Focus | JARVIS Focus | Milestone |
|------|-------|-----------|--------------|-----------|
| W1 | 1 | Steps 1, 3 | Test baseline audit | -- |
| W2 | 1-2 | Steps 2, 4 | Review helpers | M1: Config + bugs fixed |
| W3 | 3-4 | Steps 5, 6, 7, 8 | Review extractions | M2: Easy modules done |
| W4 | 4-6 | Steps 11, 10, 9, 12 | DI architecture, review | -- |
| W5 | 7-8 | Step 13 | Hook chain design, review | -- |
| W6 | 8-9 | Steps 14, 15 | Server assembly review | M3: Monolith eliminated |
| W7 | 9-11 | Steps 16, 19, 20 | Auth/Docker review | -- |
| W8 | 11-12 | Steps 17, 18, 23 | RLS security review | M4: Auth + tenancy |
| W9 | 12-13 | Steps 21, 24, 26 | Step 22 (deploy guide) | M5: Docker ready |
| W10 | 13-14 | Steps 25, 27, 28 | Steps 29, 30 | M6: MVP launch |

---

## 3. Bottleneck Analysis

### Critical Path (determines total duration)

```
S1 (2d) -> S2 (3d) -> S4 (1.5d) -> S5 (2d) -> S9 (4d) -> S12 (5d) -> S13 (6d) -> S14 (3d) -> S15 (3d) -> S16 (4d) -> S17 (5d) -> S18 (3d)
= 41.5 days critical path
```

### Bottleneck Steps (zero slack -- any delay pushes final date)

| Step | Why it's a bottleneck | Impact of 1-day delay |
|------|----------------------|----------------------|
| **Step 1 (Config)** | Everything starts here | Entire project shifts 1 day |
| **Step 2 (Helpers)** | All extractions import from core | Waves 3-6 all shift |
| **Step 13 (Memory extraction)** | Keystone module, 15 side effects | Blocks Steps 14, 15, and all of Waves 7-10 |
| **Step 15 (Server assembly)** | "Big bang" -- all modules must work | Blocks all product infrastructure |
| **Step 17 (Multi-tenancy)** | Touches ALL 74 tools, every DB query | Blocks migrations, HIPAA, backup |

### Steps with Slack (can shift without affecting total)

| Step | Slack | Why |
|------|-------|-----|
| Step 3 (Bug fixes) | 3 days | Only needs to be done before Step 11 (sleep). Wave 1 is generous. |
| Step 8 (Peers module) | 3 days | 0.5d task in a 4d wave. Can slide anywhere in Wave 3. |
| Step 11 (Sleep module) | 4 days | Independent of identity/graph. Can start early or late in Wave 4. |
| Step 21 (API docs) | 5 days | Nice-to-have polish. Can slide into Week 14 if needed. |
| Step 22 (Deploy guide) | 5 days | JARVIS can write this anytime after Docker is done. |
| Step 23 (CI/CD) | 7 days | Only blocks publishing (Steps 27, 28). Can be done anytime after Step 15. |
| Step 24 (Monitoring) | 5 days | Independent polish step. |
| Step 29 (README) | 3 days | Last wave, flexible. |
| Step 30 (GitHub setup) | 3 days | Half-day task, very flexible. |

---

## 4. Optimized Duration

### Sequential vs Parallel Comparison

| Metric | Sequential | Parallelized | Speedup |
|--------|-----------|-------------|---------|
| Total effort (person-days) | 76d | 76d (unchanged) | -- |
| Wall-clock duration | 76d (15.2 weeks) | 51d (10.2 weeks) | **1.49x** |
| JARVIS utilization | 0% (not counted) | ~40% (review + docs + architecture) | new capacity |
| ADA idle time | 0% | ~5% (waiting for reviews) | minimal |

### How parallelism is achieved

| Source of savings | Days saved |
|-------------------|-----------|
| JARVIS does reviews concurrent with ADA's next implementation | 8d |
| Steps 5-8 overlap via shared pattern (ADA accelerates after Step 5) | 2d |
| Steps 19 + 16 run in parallel (Docker + Auth are independent) | 3d |
| Steps 21 + 24 interleaved | 1d |
| Steps 27, 28, 29, 30 all parallel in final wave | 3d |
| JARVIS writes docs (Steps 22, 29, 30) while ADA codes | 4.5d |
| JARVIS designs DI/hooks BEFORE ADA needs them (Steps 9, 13) | 4d |
| **Total saved** | **25.5d** |

**Net wall-clock: 76 - 25 = ~51 working days = 10.2 weeks**

---

## 5. Risk-Adjusted Schedule

### High-risk steps get 20% buffer

| Step | Base Duration | Risk | Buffer | Adjusted |
|------|-------------|------|--------|----------|
| Step 2 (Helpers) | 3d | High | +0.6d | 3.6d |
| Step 9 (Identity/boot_context) | 4d | High | +0.8d | 4.8d |
| Step 12 (Graph, 15 tools) | 5d | High | +1.0d | 6.0d |
| Step 13 (Memory, 16 tools) | 6d | High | +1.2d | 7.2d |
| Step 14 (D-MEM orchestrator) | 3d | High | +0.6d | 3.6d |
| Step 15 (Server assembly) | 3d | High | +0.6d | 3.6d |
| Step 16 (Auth) | 4d | Medium | +0.8d | 4.8d |
| Step 17 (Multi-tenancy) | 5d | High | +1.0d | 6.0d |
| All other steps | 43d | Low-Med | +2d flat | 45d |

### Risk-adjusted totals

| Metric | Optimistic | Risk-Adjusted |
|--------|-----------|--------------|
| Total effort | 76d | 84.8d |
| Wall-clock (parallelized) | 51d | 59d |
| Calendar weeks | 10.2 weeks | **11.8 weeks** |
| Target completion (from April 7, 2026) | June 16 | **July 2, 2026** |

### Contingency scenarios

| Scenario | Impact | Mitigation |
|----------|--------|------------|
| Step 13 takes 10d instead of 6d | +4d to everything after Wave 5 | Pre-design hook chain (JARVIS), break into sub-steps |
| Neo4j GPL license blocks commercial use | Must replace with ArangoDB or NebulaGraph | Start license audit in Wave 1 (JARVIS) -- resolves by Wave 4 |
| Team SEAL breaks during extraction | Emergency rollback to pre-extraction monolith | Keep `mcp_server_v2.py` as working backup until Wave 6 complete |
| PyPI name `soul-memory` taken | Trivial rename: `soul-mem`, `soul-memory-system` | Check in Wave 1 (0 effort) |

---

## 6. Milestones

| Milestone | Definition | Target Date | Gate Criteria |
|-----------|-----------|-------------|---------------|
| **M1** | Config externalized, bugs fixed | **Week 2 (Apr 18)** | Zero hardcoded passwords in source. `ada_boot_test.py` = 10/10. 2 bugs fixed. |
| **M2** | Easy modules extracted | **Week 4 (May 1)** | 21 tools in 4 modules. `mcp_server_v2.py` down to ~4500 lines. All 61 tests pass. |
| **M3** | All modules extracted (monolith eliminated) | **Week 9 (Jun 5)** | 74 tools in 10 modules. `mcp_server_v2.py` is a 2-line shim. `soul.server:main` is THE entrypoint. `ada_boot_test.py` = 10/10. |
| **M4** | Auth + multi-tenancy working | **Week 12 (Jun 26)** | API key auth functional. Tenant A cannot see Tenant B's data (PG, Qdrant, Neo4j). Team SEAL unaffected (default tenant). Migrations idempotent. |
| **M5** | Docker packaging ready | **Week 13 (Jul 2)** | `docker compose up` starts Soul Lite in < 5 min. `/health` returns 200. CI/CD pipeline catches regressions. |
| **M6** | MVP launchable | **Week 14 (Jul 10)** | PyPI package installable. Docker image on GHCR. README with quickstart. Backup/restore tested. HIPAA-ready tier opt-in. GitHub repo with templates. |

### Milestone dependency chain

```
M1 (Apr 18)
 └──> M2 (May 1) ........... Easy stuff works
       └──> M3 (Jun 5) ..... Monolith gone (BIGGEST milestone)
             └──> M4 (Jun 26) .. Multi-user ready
                   ├──> M5 (Jul 2) . Deployable
                   └──> M6 (Jul 10)  Launchable
```

### Decision gates (William approves before proceeding)

| Gate | When | What William decides |
|------|------|---------------------|
| G1 | After M1 | "Extraction approach validated, proceed with modules?" |
| G2 | After M3 | "Monolith eliminated. Ready to add product features or need stabilization?" |
| G3 | After M4 | "Tenancy model correct? Auth scheme right? License for Neo4j resolved?" |
| G4 | After M5 | "Docker works. Go public or private beta first?" |

---

## Summary

- **30 steps** organized into **10 execution waves**
- **Sequential:** 76 days (15.2 weeks)
- **Parallelized:** 51 days (10.2 weeks) -- **1.49x speedup**
- **Risk-adjusted:** 59 days (11.8 weeks)
- **Realistic delivery:** **July 2-10, 2026** (Weeks 13-14 from start)
- **Biggest bottleneck:** Step 13 (memory extraction) -- everything depends on it
- **Biggest risk:** Cross-module circular imports during Phase 2-3
- **Key insight:** JARVIS's highest value is NOT coding -- it's designing DI/hook patterns BEFORE ADA needs them, and reviewing security-critical Steps 17/26

---

*Generated by Claude Code (Opus 4.6) — 2026-04-07*  
*Next: William approves -> ADA begins Step 1 (config.py)*
