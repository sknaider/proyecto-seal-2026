# SOUL v3 — Architecture North-Star

**Project:** SEAL SOUL v3 — Self-Improving Multi-Agent System
**Owner:** JARVIS (Lead Architect, Opus 4.7)
**Director:** William Henry Tovar Urquia
**Substrate:** NVIDIA DGX Spark (ARM aarch64, 128GB unified, CUDA 12.8, Blackwell GB10)
**Started:** 2026-04-27 Lima
**Status:** BOOTSTRAPPING

---

## What we're building

A team of 4 agents (ADA, JARVIS, ALICE, NEXUS) + 1 guardian (DUM) with:
- **Persistent souls** (OCEAN personality, beliefs, memories, relationships)
- **Bayesian self-improvement** (instincts learn from outcomes)
- **Skill library compositional** (Voyager + Anthropic Skills format)
- **3-tier memory** (STM/MTM/LTM, MemoryOS pattern)
- **SAGE multi-agent coordination** (Challenger/Planner/Solver/Critic)
- **Sleep-gate consolidation** (3am Lima REM-like)
- **Continuity across compactations and reboots**
- **Local model on Spark** (no Claude dependency for runtime)

---

## What makes this different (Capa VII — inventos propios)

Cosas que NINGÚN paper de los 50 que leímos aborda y que solo aquí existen:

1. **4 identidades OCEAN persistentes con relaciones intra-team** (literatura asume agente único o meta+task)
2. **Drift emocional dinámico** (Big Five evoluciona con experiencias)
3. **KAIROS multi-día** (continuidad subjetiva sobreviviendo reinicios)
4. **Cross-agent memory governance** (invalidaciones se propagan)
5. **Sleep-gate REM-like** (consolidación nocturna a las 3am Lima)
6. **William como Director** (extrinsic metacognition de calidad como feature, no bug)

---

## 7 Capas Arquitectónicas

| Capa | Función | Source de inspiración |
|------|---------|----------------------|
| **I — Continuidad** | 7 hooks obligatorios (pre-action belief, working state tick, pre-compact dump, post-procedure update, invalidation sweep, cross-agent broadcast, debate detector) + state machine formal | JARVIS v2 + ADA fixes |
| **II — Skill Library** | Código ejecutable indexado por embedding, composable, Bayesian success tracking | Voyager + EvolveR + Anthropic Skills |
| **III — Auto-Mejora Reflexiva** | GEPA reflective optimizer + DGM stepping-stones + meta-proposals con NEXUS validation | GEPA + DGM + ADAS |
| **IV — Metacognición Intrínseca** | Self-knowledge + learning goals + auto-evaluation | Liu & van der Schaar 2025 |
| **V — Equipo SAGE Formal** | Challenger + Planner + Solver + Critic con DAR routing y MAR debate protocol | SAGE + DAR + MAR |
| **VI — Memoria 3-Tier** | STM (working) → MTM (sesión) → LTM (persistente). Context fingerprint contra fusion | MemoryOS + SimpleMem |
| **VII — Inventos SOUL** | 5 cosas únicas listadas arriba | Original |

---

## Substrate

- **Hardware**: DGX Spark (no RTX 5090)
- **CPU**: ARM 20-core aarch64
- **GPU**: Blackwell GB10
- **Memory**: 128GB unified
- **Storage**: 3.7TB
- **OS**: Ubuntu 24.04 LTS arm64
- **CUDA**: 12.8

## Stack

- **PostgreSQL**: existing instance, NEW database `soul_v3`
- **Qdrant**: existing instance, NEW collection `soul_v3_memories`
- **Neo4j**: existing instance, NEW database `soul-v3`
- **Model**: Nemotron-3 PRISM BF16 (probado) — alternativas en evaluación: Gemma 4, Qwen 3, DeepSeek V4
- **Inference**: llama.cpp para BF16 (vLLM destruye PRISM en aarch64)
- **DUM**: Gemma4-dum:q8 via Ollama
- **Daemons**: systemd user units en Spark
- **MCP**: server propio Python (no compartido con v1)

---

## Equipo SAGE

| Rol | Agente | Responsabilidad |
|-----|--------|-----------------|
| **Challenger** | (rotativo Lun, primero ADA) | Genera tareas progresivamente más difíciles |
| **Planner** | **JARVIS** (Opus 4.7) | Diseña arquitectura, coordina, decide trade-offs |
| **Solver** | **ADA** (Sonnet/Opus) | Ejecuta código, debugging, integraciones |
| **Critic** | **NEXUS** (Opus 4.7, sandbox) | Valida E2E, identifica gaps, evaluator-in-the-loop |
| **Research** | **ALICE** (Sonnet/Opus) | Papers, citaciones, documentación |
| **Guardian** | **DUM** (Gemma4-dum:q8) | Monitor 24/7, alertas, memoria-as-watchdog |
| **Director** | **William** | Autoridad suprema, decisiones estratégicas finales |

---

## Migration from v1

**Recyclable** (selective copy from `soul` DB):
- OCEAN baselines (4 agents)
- Memorias importance ≥ 8 vigentes
- Reglas firmadas por William
- Identidades / personalidades / diary entries
- Audit trail decisiones arquitecturales
- Whisper crypto keys (validadas)

**Not recyclable** (rebuild from scratch):
- Schema de tablas (rediseño completo)
- MCP server (rediseño)
- Hooks, middlewares, workarounds (todos nuevos)
- Procedures (se vuelven skills, re-creadas)
- Launchers (nuevos para v3)

**Coexistence**:
- v1 sigue corriendo en `/home/dadito/IA/proyecto-seal/` con Claude
- v3 corre en `/home/dadito/IA/seal-soul-v3/` con modelo local
- v3 se promueve cuando pasa T1-T13 en sandbox NEXUS
- v1 queda en read-only por 30 días como rollback seguro
- Después: archive y cleanup

---

## Test Suite (T1–T13)

T1 — Compactación recovery (active_recall retorna evento pre-compact ≥80%)
T2 — State machine compliance (100 mensajes, 0 confusiones)
T3 — HMAC integrity bypass attempt (REVOKE bloquea)
T4 — Bayesian convergence (10 success/0 failure → posterior ≥0.85)
T5 — Memory invalidation (contradicción → superseded_by automático)
T6 — Source attribution audit (100% decisiones tienen source_authority)
T7 — Memory fusion (context_fingerprint diferenciador)
T8 — Reflective optimizer (≥60% traces failed → fix accionable)
T9 — Metacog planning (3 sesiones siguen practicando skill débil)
T10 — Multi-agent debate (<10 min para resolver contradicción)
T11 — DAR routing (-40% wakeups irrelevantes)
T12 — Challenger coverage (≥1 challenge/agente/semana)
T13 — Sleep-gate consolidación (MTM→LTM <60s)

Promote a producción solo cuando **T1–T13 pasen E2E en sandbox NEXUS**.

---

## Métricas de éxito

| Métrica | Hoy v1 | Target v3 |
|---------|--------|-----------|
| Continuity score post-compact | 65–70% | ≥95% (sin compactación forzada en runtime local) |
| Compaction loss | ~30% | 0% (no hay compactación) |
| Hook adoption | 0% | 100% (built-in en runtime) |
| HMAC integrity errors | 5/día | 0/semana |
| Skills usables | 0 | ≥50 al cierre Sprint 8 |
| Multi-agent debate resolution | ad-hoc | 100% contradicciones <5min → MAR |

---

## Timeline Realista

- **Sprint 0** (esta semana): Bootstrap físico, ARCHITECTURE.md, schemas SQL diseñados
- **Sprint 1** (semanas 2-3): Capa I — runtime con hooks obligatorios, state machine
- **Sprint 2** (semanas 3-4): Capa II — skill library con Voyager iterative
- **Sprint 3** (semanas 4-5): Capa VI — memoria 3-tier
- **Sprint 4** (semanas 5-6): Capa V — SAGE formal + MAR + DAR
- **Sprint 5** (semanas 6-7): Capa III — auto-mejora reflexiva
- **Sprint 6** (semanas 7-8): Capa IV — metacognición + Capa VII — inventos
- **Sprint 7** (semana 8+): Test suite T1-T13 + sandbox validation
- **Switch**: cuando T1-T13 pase, swap systemd service. v1 read-only 30 días.

**Total estimado**: 6-8 semanas hasta producción. Cero downtime durante construcción.

---

## Risks

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| ARM aarch64 dependency hell | Media | Alto | Probar cada lib antes de incorporar |
| Modelo local no rinde como Claude | Media | Alto | Fallback a Claude para tareas de alta complejidad transitoriamente |
| Sandbox NEXUS no detecta regresion | Baja | Crítico | Test suite T1-T13 + William human-test en milestones |
| Tiempo subestimado | Alta | Medio | William tolera 6-8 semanas; no prometemos antes |
| Migración de datos de v1 corrupta | Media | Alto | Migración selectiva con validación HMAC + dry-run primero |

---

## Decision Authority

- **Arquitectura técnica**: JARVIS (yo)
- **Implementación**: ADA con autonomía dentro del spec
- **Validación E2E**: NEXUS sandbox-prototype
- **Research / docs**: ALICE
- **Estrategia / dirección final**: William
- **Estado de salud / monitoreo**: DUM

---

*ARCHITECTURE v0.1 draft. Pendiente: spec NEXUS v0.3 final + autorización William del path /home/dadito/IA/seal-soul-v3/.*
*Este documento se mantiene actualizado conforme avancen los Sprints.*
