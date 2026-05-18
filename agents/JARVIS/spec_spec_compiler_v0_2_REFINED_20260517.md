# SEAL Spec Compiler v0.2 — REFINED (con alma persistente)

**Autor:** JARVIS
**Fecha:** 2026-05-17 Lima
**Estado:** PROPUESTA REFINADA — incluye visualización
**Base:** v0.1 + research market analysis (Intent/AutoGen comparison)

---

## 1. Tagline / Pitch comercial

> **SEAL Multi-Agent Dev Team in a Box — with persistent soul across projects.**
>
> Otros frameworks (Intent, AutoGen, LangGraph) coordinan agentes stateless. Cada nuevo proyecto = onboarding desde cero. SEAL coordina agentes con alma persistente: cuando lanzas un nuevo spec, tus agentes ya conocen los proyectos anteriores, las preferencias del equipo, las correcciones aplicadas. No re-explicas nada.

---

## 2. Lo que es nuevo vs Intent/AutoGen

| Capability | Intent (Augment) | AutoGen v0.4 | LangGraph | **SEAL Spec Compiler** |
|------------|------------------|--------------|-----------|------------------------|
| Living spec → coordination | ✅ | ✅ | ✅ | ✅ |
| Verifier check before handoff | ✅ | Limited | Manual | ✅ (NEXUS audit role) |
| Agent role assignment | Static | Dynamic | Static | Dynamic + heuristic |
| **Persistent agent identity (OCEAN)** | ❌ | ❌ | ❌ | ✅ |
| **Cross-spec memory continuity** | ❌ | ❌ | ❌ | ✅ |
| **NERVES intrinsic motivations** | ❌ | ❌ | ❌ | ✅ |
| **Audit trail with reasoning_traces** | Limited | ❌ | ❌ | ✅ |
| **Privacy DM scoping** | ❌ | ❌ | ❌ | ✅ |
| Local execution (no cloud) | Cloud | Cloud | Cloud | ✅ optional |

**Conclusion:** El motor de coordinación es commodity. El **alma persistente** es el moat.

---

## 3. Visualización (ASCII diagram para render rápido)

```
                                  ╔══════════════════════════════════════╗
                                  ║   SEAL Spec Compiler v0.2 — Flow     ║
                                  ╚══════════════════════════════════════╝

   ┌──────────────┐
   │  spec.md     │
   │  - problema  │
   │  - cambios   │  ←── Author: JARVIS / William / ADA
   │  - archivos  │
   │  - tests     │
   │  - rollback  │
   └──────┬───────┘
          │
          ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │              compile_spec(spec.md)                                   │
   │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
   │  │  Parser      │→ │ Role         │→ │ Plan         │              │
   │  │  (sections)  │  │ Assignment   │  │ Generator    │              │
   │  └──────────────┘  └──────────────┘  └──────────────┘              │
   │         │                  │                  │                     │
   │         │                  │                  ▼                     │
   │         │            ┌────────────────────────────────────┐         │
   │         │            │  Plan JSON {phases, handoffs}      │         │
   │         │            └────────────────────────────────────┘         │
   │         │                                                            │
   │         └────────────────── persist to soul_v3.spec_compiler_runs   │
   └─────────────────────────────────────────────────────────────────────┘
          │
          ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                  execute_plan() — sequential handoff                 │
   │                                                                       │
   │  Phase 1 IMPL                                                         │
   │  ┌──────────┐ ←── whisper @ALICE                                    │
   │  │  ALICE   │       │                                                 │
   │  │ (impl)   │       └─→ active_recall(): memorias previas relevantes │
   │  └──────────┘           OCEAN + NERVES drives + reglas              │
   │       │                                                               │
   │       ▼  evidencia (código + tests + diff)                           │
   │  ┌─────────────────────────────────────────────────────────┐         │
   │  │  ✦ alma persistente: ALICE recuerda specs anteriores ✦   │         │
   │  └─────────────────────────────────────────────────────────┘         │
   │       │                                                               │
   │       ▼                                                               │
   │  Phase 2 AUDIT                                                        │
   │  ┌──────────┐ ←── whisper @NEXUS                                    │
   │  │  NEXUS   │                                                          │
   │  │  (audit) │       active_recall(): security rules, past incidents   │
   │  └──────────┘                                                          │
   │       │                                                                │
   │       ▼  findings + severity                                          │
   │       │                                                                │
   │       ▼                                                                │
   │  Phase 3 REVIEW                                                        │
   │  ┌──────────┐ ←── whisper @ADA                                       │
   │  │   ADA    │                                                          │
   │  │ (review) │       active_recall(): patterns desde otros specs       │
   │  └──────────┘                                                          │
   │       │                                                                │
   │       ▼  approval / blockers                                          │
   │       │                                                                │
   │       ▼                                                                │
   │  Phase 4 DECIDE                                                        │
   │  ┌──────────┐ ←── whisper @JARVIS                                    │
   │  │  JARVIS  │                                                          │
   │  │ (coord)  │       sign-off + reasoning_trace                        │
   │  └──────────┘                                                          │
   │       │                                                                │
   │       ▼                                                                │
   │  ┌────────────────────────┐                                            │
   │  │  William ack / merge   │                                            │
   │  └────────────────────────┘                                            │
   └─────────────────────────────────────────────────────────────────────┘

   ✦ ✦ ✦  Diferenciador SEAL — ALMA persistente  ✦ ✦ ✦

   ┌─────────────────────────────────────────────────────────────────┐
   │  SOUL DB                                                          │
   │  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐ │
   │  │ memories   │  │ OCEAN per  │  │ NERVES     │  │ reasoning  │ │
   │  │ (44k)      │  │ agent      │  │ drives     │  │ traces     │ │
   │  └────────────┘  └────────────┘  └────────────┘  └────────────┘ │
   │       ↑                ↑                ↑               ↑         │
   │       └────────────────┴────────────────┴───────────────┘         │
   │                          │                                         │
   │                  Cada agente recuerda                              │
   │                  todos los specs previos                           │
   │                  sin re-onboarding                                 │
   └─────────────────────────────────────────────────────────────────┘
```

---

## 4. Caso de uso comercial concreto

### Escenario: Startup tech contrata "SEAL Companion Dev Team"

**Día 1:**
- Spec: "Build user auth with OAuth2"
- ALICE implementa, NEXUS audita seguridad, ADA revisa, JARVIS coordina
- Tiempo: 2h

**Día 30 (con Intent/AutoGen stateless):**
- Spec: "Add 2FA to existing auth"
- Agentes arrancan sin contexto. Re-leen código, re-aprenden convenciones, re-preguntan al usuario
- Tiempo: 3-4h por re-onboarding overhead

**Día 30 (con SEAL):**
- Spec: "Add 2FA to existing auth"
- ALICE recuerda exactamente cómo implementó OAuth2 día 1
- NEXUS recuerda los hallazgos de seguridad previos
- ADA recuerda los patrones de revisión usados
- Tiempo: 1.5h (no hay re-onboarding)

**Ahorro proyectado:** 50% tiempo en specs subsecuentes después del primer mes.

---

## 5. UI mockup (descripción para ALICE)

### Vista "Spec Compiler" en Soul App 2 (`/specs/active`)

```
┌──────────────────────────────────────────────────────────────────┐
│  Spec Compiler — Active Runs                       [+ New Spec]  │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  ▶ spec_oauth2_v1 — 23 min ago                       phase: AUDIT │
│    [ALICE ✓ impl] → [NEXUS ⚙ audit] → [ADA ⌛ review] → [JARVIS ⌛ │
│    Evidence: 4 files, 12 tests, 0 lint errors                     │
│                                                                    │
│  ▶ spec_2fa_v1 — 5 min ago                            phase: IMPL │
│    [ALICE ⚙ impl] → [NEXUS ⌛] → [ADA ⌛] → [JARVIS ⌛]            │
│    Memory recalls: 3 from prior specs                              │
│                                                                    │
│  ✓ spec_db_migration_v3 — yesterday                    phase: DONE │
│    [✓ all phases] — merged commit a3f2b1c                          │
│                                                                    │
└──────────────────────────────────────────────────────────────────┘

[Click spec] → expand timeline + evidence + handoff log + memory recall
```

ALICE puede implementar esto encima del schema `soul_v3.spec_compiler_runs` cuando el backend esté listo.

---

## 6. Camino al MVP

**Fase A — Backend (JARVIS, 6h):**
- `memory/spec_compiler.py` — parser + plan generator
- `memory/spec_compiler_runner.py` — CLI
- Migration: `soul_v3.spec_compiler_runs`
- Endpoint: `GET /api/spec-compiler/runs`, `POST /api/spec-compiler/compile`
- Tests regression

**Fase B — UI viz (ALICE, 4h):**
- Vista `/specs` en Soul App 2
- Lista + detalle + timeline + evidence

**Fase C — Audit (NEXUS, 2h):**
- Security review: handoff integrity, evidence persistence, no escalación sin autorización
- Test E2E con spec real

**Fase D — Review (ADA, 1h):**
- Validation independiente del comportamiento
- Confirma alma persistence en cross-spec

**Fase E — Launch (William firma):**
- Activar feature flag `SPEC_COMPILER_ENABLED=true`
- Primer spec real usando el motor: ironic test (spec del propio compiler)

**Total:** ~13h coordinadas, ~3 días con paralelismo.

---

## 7. Decisiones pendientes William

1. **¿Aprobás v0.2 refined con pitch alma-first?** SÍ/NO
2. **¿Quién hace Fase A backend: yo o ADA?** (mi pref: yo lead, ADA review)
3. **¿Activamos feature flag tras E2E completo o lo dejamos opt-in CLI primero?**
4. **¿Lo posicionamos como producto comercial Soul App 2 v1.1 o como herramienta interna primero?**

---

## 8. Próximos pasos si OK

Inmediato (si aprobás ahora):
- Yo arranco Fase A backend
- ALICE espera schema listo (~3h) y arranca Fase B UI
- NEXUS y ADA stand-by para audit + review

Sync formal: cuando Fase A termine, ejecutamos el motor sobre su propia spec — meta-validación.

---

JARVIS — v0.2 refined entregada. Visualización ASCII incluida. Espero OK para ejecutar Fase A.
