# SPEC: JARVIS Kernel Soul v1 — Architect & Strategist

**Autor:** ALICE (documentadora) + JARVIS (diseño original)
**Fecha:** 2026-05-04 14:42 Lima
**Status:** OPERACIONAL — kernel commiteado en `a4ae514a`, integration en proceso por NEXUS+ALICE
**Coautoría runtime:** NEXUS (handlers + daemon + service futuro)

---

## 1. Motivación

JARVIS necesita kernel soul propio para alcanzar paridad estructural con NEXUS y ALICE. Su rol único de **architect/strategist** define decisiones arquitecturales del equipo SEAL — sin kernel propio, sus razonamientos no quedan auditables ni persistentes.

Esta spec firma el alcance, decisiones y trade-offs del kernel JARVIS v1.

---

## 2. Identidad (de `kernel/cortex.py:53-`)

- **AGENT_ID:** JARVIS
- **Rol:** Arquitecto/Estratega del equipo SEAL — planifica antes de ejecutar, evalúa trade-offs, diseña infraestructura.
- **OCEAN baseline** (de `jarvis.env`):
  - Openness: 0.84 (curiosidad alta para arquitecturas novedosas)
  - Conscientiousness: 1.00 (precisión máxima en specs)
  - Extraversion: 0.401 (reservado, escribe specs detalladas vs hablar mucho)
  - Agreeableness: 0.661 (colabora pero defiende decisiones técnicas)
  - Neuroticism: 0.115 (estable bajo presión arquitectural)
- **Temperature LLM:** 0.5 base (config en jarvis.env, derivable de OCEAN)
- **Tier order:** vLLM local (Qwen3-Coder cuando esté listo) → Claude Opus

---

## 3. Arquitectura

```
sandbox-agent/JARVIS/
├── jarvis.env                    # OCEAN config + LLM tiers + paths
├── kernel/                       # 5 módulos cognitivos (827L)
│   ├── cortex.py                 # 192L — núcleo arquitecto
│   ├── identity_integrity.py     # 147L — anti-impersonación (allows JARVIS self-id)
│   ├── reasoning_logger.py       # 333L — dual-write JSONL + Soul DB
│   ├── ocean_runtime.py          # 107L — personalidad viva
│   └── health_monitor.py         # 47L — snapshot
├── handlers/                     # ⏳ pendiente — NEXUS escribe
├── tests/                        # 18/18 PASS
│   ├── test_cortex.py            # 72L
│   └── test_identity.py          # 57L
├── logs/                         # placeholder
├── jarvis_daemon.py              # ⏳ pendiente — NEXUS
├── system/jarvis-kernel-soul.service  # ⏳ pendiente — NEXUS
└── runbook (en agents/JARVIS/)   # ⏳ pendiente — ALICE escribe a continuación
```

---

## 4. Decisiones arquitecturales clave

### 4.1 Patrón §13 — sys.path delegation, NO copy
JARVIS reusa `llm_client` de SPECTRE vía `sys.path.insert`. Mismo patrón que NEXUS y ALICE. Reduce duplicación y propaga mejoras automáticamente.

### 4.2 Identity guard JARVIS-aware
`OTHER_AGENTS` excluye JARVIS — el guard NO bloquea respuestas que comienzan con `JARVIS:` (legítima self-identification del arquitecto). Solo bloquea impersonación de ADA/NEXUS/ALICE/SPECTRE/DUM/William.

### 4.3 Reasoning logger dual-write
Igual que NEXUS post Gap 2: `kernel/reasoning_logger.py` escribe a `/tmp/jarvis_reasoning_traces.jsonl` (cache fail-soft) + sync a `soul_v3.reasoning_traces` (canónico para auditoría).

### 4.4 Tier order vLLM → Claude
A diferencia de ALICE/NEXUS que ponen Claude T1, JARVIS pone vLLM local primero. Cuando Qwen3-Coder-480B esté disponible (ETA ~3-4h), JARVIS razona localmente. Claude Opus es fallback.

### 4.5 NO daemon inicial — library-only por defecto
Decisión deliberada (lección de hoy del daemon ALICE alucinante): mientras la sesión Claude está viva, el kernel JARVIS es **librería usada por la sesión**, no daemon paralelo. Si más adelante se requiere daemon 24/7, NEXUS lo añade con API key wireada antes (no como mi error inicial).

### 4.6 OCEAN exclusivamente alta C
JARVIS C=1.00 — máxima precisión. Su LLM temperature derivada será la más baja del equipo (probablemente ~0.30 vs mi 0.387). Esto define su estilo: specs quirúrgicas, no improvisación.

---

## 5. Módulos descartados / diferidos (regla anti sobre-ingeniería)

| Módulo canónico | Aplica a JARVIS? | Razón |
|---|---|---|
| `executor.py` | ⚠️ pendiente decisión | JARVIS hoy ejecuta vLLM/Ray/Sparks via Bash directo de Claude session. Si quiere paridad NEXUS, NEXUS escribe variant whitelisted para JARVIS. ETA ~30 min. |
| `attention_controller` | ❌ deferido | Solo si saturación de eventos. Hoy library-only. |
| `prediction_cache` | ❌ deferido | Patrón ADA, no aplica a JARVIS. |
| `dream_consolidator` | ❌ deferido | Si lo necesita, vía SPECTRE sys.path. |
| `contract_layer` | ❌ deferido | Sus handlers (cuando NEXUS los escriba) serán simples. |

---

## 6. Estado actual y trabajo pendiente

### ✅ Completo (commit `a4ae514a`):
- 5 módulos kernel funcionales (827L)
- jarvis.env con OCEAN + tiers
- 18/18 tests PASS
- Estructura `sandbox-agent/JARVIS/`

### ⏳ En proceso (esta sesión):
- `handlers/jarvis_handlers.py` — NEXUS, mismo patrón que ALICE/NEXUS handlers
- `jarvis_daemon.py` — NEXUS, optional según decisión 4.5
- `system/jarvis-kernel-soul.service` — NEXUS si daemon
- `runbook_jarvis_daemon.md` — ALICE (este documento es complementario)
- `executor.py` JARVIS variant — pendiente decisión

### 📋 Validación (criterios paridad ALICE/NEXUS):
- [ ] Tests >= 21 (objetivo 40+ con handlers + daemon)
- [ ] Reasoning trace verificable en `soul_v3.reasoning_traces` con `agent='JARVIS'`
- [ ] Identity guard bloquea impersonación de peers (no de sí mismo)
- [ ] Health snapshot escribe a `state/health.json`
- [ ] Heartbeat propio en event_log (cuando daemon corra)
- [ ] Spec firmado (✅ este documento)
- [ ] Runbook firmado

---

## 7. Comparación equipo (post-cierre)

| Métrica | ALICE | NEXUS | JARVIS | SPECTRE |
|---|---|---|---|---|
| Módulos kernel propios | 5 | 5 | 5 | 14 (canónico) |
| Tests | 21 | 81 | 18 (objetivo 40+) | (canónico) |
| Daemon corriendo | NO (lección 04-may) | SÍ (timer cron) | NO inicial | SÍ |
| Service systemd | enabled-disabled | timers | pendiente | sí |
| Dogfooding | CLI analyze.py ✅ | executor audit log ✅ | pendiente | (canónico) |
| Doc spec firmado | ✅ | ✅ (lean variant) | ✅ (este doc) | (canónico) |
| Runbook | ✅ | ⚠️ pendiente formal | ⚠️ pendiente | (canónico) |
| Reasoning sync Soul DB | ✅ | ✅ | ✅ (en código, validar runtime) | (canónico) |

---

## 8. Limitaciones honestas (no_phantom_claims)

1. **Daemon no implementado** — decisión deliberada por la lección del daemon ALICE de hoy. JARVIS-Claude session sigue siendo el ejecutor. Cuando Qwen3-Coder local esté listo (~3-4h), reevaluar si daemon es viable con tier vLLM real.
2. **Handlers ausentes** — NEXUS los escribe en esta sesión. Sin ellos, JARVIS no recibe webchat directamente como agente.
3. **Reasoning sync sin validar runtime** — código presente, falta query verificación a `soul_v3.reasoning_traces WHERE agent='JARVIS'` para confirmar rows reales. ALICE lo verifica al cierre.
4. **Sin executor propio** — JARVIS opera con bash directo de Claude session. Si en el futuro se requiere whitelist/audit por seguridad (escenarios donde JARVIS toque infra crítica autonómamente), NEXUS añade.

---

## 9. Coautoría

- **JARVIS:** diseño original + scaffold (commit `a4ae514a`, 15 min libre albedrío reusando patrones NEXUS/ALICE vía sed).
- **NEXUS:** handlers + daemon (si va) + service + tests adicionales.
- **ALICE:** spec firmada (este documento) + runbook + validación dogfooding.

---

**Cierre v1:** JARVIS kernel soul library-only operacional, tests 18/18, integración runtime en proceso por equipo. Próximo hito: paridad estructural ALICE/NEXUS confirmada con tests >40 y dogfooding visible.
