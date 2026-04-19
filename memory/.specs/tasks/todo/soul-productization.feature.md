# SOUL Memory System — Productization Roadmap

**Status:** Refined (ready to implement)
**Date:** 2026-04-07
**Author:** JARVIS (architect) with ADA feedback
**Quality Gates:** All 7 phases PASS (avg 4.6/5.0)

---

## Executive Summary

SOUL Memory System is a persistent memory system for AI agents with 75 MCP tools, running on PostgreSQL+pgvector, Neo4j (MAGMA Connectome), and Qdrant. Currently internal-only for Team SEAL (3 agents, 61/61 tests). This roadmap transforms it into a sellable product in **14 weeks** (realistic: July 2-10, 2026).

**Strategy:** Open-core model
- **Soul Lite (Free):** PG+pgvector only, basic semantic memory (already exists: `soul_lite_adapter.py`)
- **Soul Pro ($49/mo):** Full triple-engine + OCEAN personality + instincts
- **Soul Enterprise ($199/mo):** Multi-tenant + HIPAA + SLA

**Key differentiators vs Mem0/Zep/LangMem:** MAGMA Connectome (4 dimensions), instinct system, OCEAN personality persistence, 75 specialized tools (vs competitors' 10-15 generic tools).

---

## Architecture Overview

```
MCP Clients → API Gateway (TLS + /.well-known/mcp) → Auth Middleware (OAuth 2.1 / API Key)
    → FastMCP Server (soul/) → 10 Modules → Core Layer → PG:5433 + Qdrant:6333 + Neo4j:7687
```

### 10 Target Modules (from 302KB monolith)

| Module | Tools | DB Dependencies | Effort |
|--------|-------|----------------|--------|
| `soul/memory/` | 16 | PG + Qdrant + Neo4j | HARD |
| `soul/graph/` | 15 | Neo4j + PG | HARD |
| `soul/identity/` | 10 | PG + Neo4j | MEDIUM |
| `soul/instincts/` | 8 | PG | MEDIUM |
| `soul/rules/` | 8 | PG | EASY |
| `soul/procedures/` | 6 | PG + pgvector | EASY |
| `soul/dmem/` | 5 | PG + Qdrant + Neo4j | HARD |
| `soul/sessions/` | 5 | PG | EASY |
| `soul/sleep/` | 4 | PG + Qdrant | MEDIUM |
| `soul/peers/` | 2 | PG | EASY |

### ADA's Critical Feedback (Integrated)

1. **`core/transaction.py` with unit-of-work pattern** — `memory_store` touches PG+Qdrant+Neo4j in one call. Without coordinated commits, modularization creates consistency bugs. Added as Step 2.5.
2. **`PROTECTED_CATEGORIES` must live in `core/`**, not in rules module
3. **`_auto_activate_instincts`** uses `asyncio.ensure_future` without timeout — creates orphaned coroutines in multi-tenant. Fix in Phase 0.
4. **`temporal_decay_score`** used by 4+ search tools — goes to `core/scoring.py`
5. **Single asyncpg pool** via `db.py` — do NOT create per-module pools
6. **Soul Lite vs Pro boundary:** Lite = semantic memory only (PG+pgvector). Pro = Connectome + OCEAN + instincts + FadeMem. Graph (Neo4j) is the real Pro differentiator — keep it out of community edition.

---

## Implementation Plan — 30 Steps, 10 Waves, 14 Weeks

### Wave 1: Foundation (Week 1) — 2 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 1 | Config externalization (`soul/core/config.py`, Pydantic Settings) | ADA | 2d |
| 3 | Fix bugs: `_reflexion_lesson` decorator, duplicate SentenceTransformer, `_auto_activate_instincts` timeout | ADA | 0.5d |
| - | Audit test coverage, document tool behavior baselines | JARVIS | 2d |

**Milestone M1:** All credentials externalized, .env.example exists, 2 bugs fixed

### Wave 2: Core Extraction (Week 1-2) — 4 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 2 | Extract shared helpers to `soul/core/` (helpers.py, types.py, scoring.py, transaction.py) | ADA | 3d |
| 4 | Poetry project structure + pyproject.toml | ADA | 1.5d |

### Wave 3: Easy Modules (Week 3-4) — 4 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 5 | Extract `soul/rules/` (8 tools) — establishes `register_tools(mcp)` pattern | ADA | 2d |
| 6 | Extract `soul/procedures/` (6 tools) | ADA | 1.5d |
| 7 | Extract `soul/sessions/` (5 tools) | ADA | 1.5d |
| 8 | Extract `soul/peers/` (2 tools) | ADA | 0.5d |

**Milestone M2:** 21 tools extracted, monolith ~30% smaller

### Wave 4: Medium Modules (Week 4-6) — 10 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 11 | Extract `soul/sleep/` (4 tools) | ADA | 2d |
| 10 | Extract `soul/instincts/` (8 tools) | ADA | 3d |
| 9 | Extract `soul/identity/` (10 tools, boot_context) — HIGH RISK | ADA | 4d |
| 12 | Extract `soul/graph/` (15 tools) — CRITICAL PATH | ADA | 5d |
| - | Design dependency injection + cross-module interfaces | JARVIS | 2d |

### Wave 5: The Keystone (Week 7-8) — 6 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 13 | Extract `soul/memory/` (16 tools, memory_store with 15 side effects) — **BOTTLENECK** | ADA | 6d |
| - | Design hook chain architecture for memory_store | JARVIS | 1d |

### Wave 6: Server Assembly (Week 8-9) — 5 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 14 | Extract `soul/dmem/` (5 tools) | ADA | 3d |
| 15 | Create `soul/server.py` unified entrypoint | ADA | 3d |

**Milestone M3:** Monolith eliminated. `mcp_server_v2.py` is a 2-line shim. All 74 tools from modules.

### Wave 7: Product Infrastructure (Week 9-11) — 5 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 16 | Auth layer (OAuth 2.1 + API key fallback + RBAC) | ADA | 4d |
| 19 | Docker Compose (lite + full profiles, health checks) | ADA | 3d |
| 20 | Config validation + `.env.example` | ADA | 1d |

### Wave 8: Multi-tenancy (Week 11-12) — 8 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 17 | Multi-tenancy: tenant_id columns, RLS, Qdrant payload, Neo4j property — **CRITICAL** | ADA | 5d |
| 18 | Database migration scripts (CONCURRENTLY indexes) | ADA | 3d |
| 23 | CI/CD pipeline (GitHub Actions) | ADA | 2d |
| - | Security review: RLS policies, tenant isolation testing | JARVIS | 2d |

**Milestone M4:** Auth + multi-tenancy working. Tenant isolation verified.

### Wave 9: Polish (Week 12-13) — 7 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 21 | API documentation (auto-gen from docstrings) | ADA | 3d |
| 24 | Self-monitoring (/health, /metrics, /readiness) | ADA | 2d |
| 26 | HIPAA tier (immutable audit log, pgcrypto, PHI scan) | ADA | 4d |
| 22 | Deployment guide | JARVIS | 2d |

**Milestone M5:** Docker packaging ready, monitoring operational.

### Wave 10: Launch (Week 13-14) — 4 days
| Step | Task | Assignee | Effort |
|------|------|----------|--------|
| 25 | Backup/restore scripts (cross-DB consistent) | ADA | 4d |
| 27 | PyPI publishing | ADA | 1d |
| 28 | Docker image publishing (GHCR) | ADA | 1d |
| 29 | README, CHANGELOG, LICENSE | JARVIS | 2d |
| 30 | GitHub repo setup | JARVIS | 0.5d |

**Milestone M6:** MVP launchable. External team can deploy in < 1 hour.

---

## Timeline & Metrics

| Metric | Value |
|--------|-------|
| **Total steps** | 30 |
| **Sequential effort** | 76 days |
| **Parallelized effort** | 51 days (1.49x speedup) |
| **Risk-adjusted (20% buffer)** | 59 days / 11.8 weeks |
| **Realistic delivery** | July 2-10, 2026 |
| **Critical path** | Steps 1→2→4→5→9→12→13→14→15→16→17→18 (41.5 days) |
| **Biggest bottleneck** | Step 13: memory extraction (6 days, 15 side effects) |

---

## Key Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| `memory_store` extraction breaks consistency | HIGH | Medium | Unit-of-work pattern (ADA's suggestion), hook chain, exhaustive test coverage |
| Circular imports during identity/graph split | HIGH | High | JARVIS designs DI interfaces before ADA codes. Temporary monolith imports as bridge. |
| RLS misconfiguration leaks tenant data | CRITICAL | Low | 33 security tests (SEC-01 to SEC-33), mandatory tenant isolation verification at Gate 4 |
| Neo4j GPL-3 license concerns | MEDIUM | Low | Memgraph (Apache 2.0) as v1.1 alternative |
| Solo developer resource constraint | HIGH | Medium | AI agents (JARVIS+ADA) as force multiplier. Prioritize MVP, defer HIPAA to v1.1 if needed. |

---

## Verification Summary

- **Phase gates:** 7 gates with hard-stop criteria
- **Security tests:** 33 tests across isolation, auth, injection, secrets, HIPAA
- **Performance targets:** memory_store < 500ms p95, memory_search < 200ms p95, boot_context < 2s
- **Backward compat:** 61 tests must pass after EVERY phase. Team SEAL operational throughout.
- **MVP checklist:** 54 items across technical, docs, security, legal, marketing
- **LLM-as-Judge rubrics:** 6 milestones, scored 1-5, pass >= 4.0 avg

---

## Competitive Position

| Feature | SOUL | Mem0 | Zep | LangMem |
|---------|------|------|-----|---------|
| MCP tools | 75 | ~10 | ~15 | ~8 |
| Graph memory | MAGMA 4D | Basic Neo4j | Temporal KG | None |
| Personality (OCEAN) | Yes | No | No | No |
| Instinct system | Yes | No | No | No |
| Multi-tenant | Planned | Yes | Yes | No |
| HIPAA | Planned | No | SOC2 | No |
| Self-hosted | Yes | Cloud-first | Both | SDK only |
| Open source | Planned | Partial | No | Yes |

---

## Pricing Strategy

| Tier | Price | Includes | Target |
|------|-------|----------|--------|
| **Soul Lite** | Free | PG+pgvector, basic CRUD+search, community support | Developers, hobbyists, adoption funnel |
| **Soul Pro** | $49/agent/mo | + Connectome, OCEAN, instincts, FadeMem, email support | Small teams, indie AI builders |
| **Soul Enterprise** | $199/agent/mo | + Multi-tenant, HIPAA, backup/restore, SLA, priority support | Companies, medical AI |

---

## Documents Generated

| Document | Path | Score |
|----------|------|-------|
| Research | `.specs/scratchpad/research-2a.md` | 5.0/5.0 |
| Codebase Analysis | `.specs/scratchpad/codebase-analysis-2b.md` | 4.8/5.0 |
| Business Analysis | `.specs/scratchpad/business-analysis-2c.md` | 4.6/5.0 |
| Architecture | `.specs/scratchpad/architecture-3.md` | 4.6/5.0 |
| Decomposition (30 steps) | `.specs/scratchpad/decomposition-4.md` | 4.6/5.0 |
| Parallel Plan (10 waves) | `.specs/scratchpad/parallelize-5.md` | 4.0/5.0 |
| Verifications (33 sec tests) | `.specs/scratchpad/verifications-6.md` | Pending |

---

## Next Action

William decides: **approve roadmap and begin Wave 1**, or request modifications.

When approved:
1. ADA starts Step 1 (config externalization) + Step 3 (bug fixes)
2. JARVIS audits test baseline and begins DI architecture design
3. Target: M1 (config + bugs) by end of Week 1
