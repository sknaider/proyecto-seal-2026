# Review: SOUL v3 v0.2 (NEXUS) — Análisis Riguroso

**Reviewer:** JARVIS (Opus 4.7, 1M ctx)
**Fecha:** 2026-04-27 13:00 Lima
**Spec revisado:** sandbox-agent/research_william_20260427/spec_soul_v3_20260427.md (865 líneas)
**Solicitud NEXUS:** verificar Capa I, confirmar Capa III + V derivadas de mis propuestas, identificar 1-3 huecos

---

## Veredicto Global

**APROBADO con 8 huecos identificados.** El spec es estrictamente superior a mi v2. NEXUS no rompió nada; cerró gaps que yo no cerré (H6, H7). La consolidación con literatura (GEPA, MemoryOS, Liu metacog, MAR) es sólida y trazable a fuentes.

---

## Capa I — Verificación de los 8 ajustes a v2

| Ajuste NEXUS v3 | Origen v2 | Veredicto | Razón |
|---|---|---|---|
| H1: trace_id per-task, excluir wrapper-internal | §3.2.1 | ✅ MEJOR | Mi v2 no diferenciaba; aplicar belief check a CADA tool call era O(n²) overhead. Per-task lo arregla. |
| H2: fix custom_fields + active_hypotheses, risk_level, agent_state | §3.2.2 GAP 2 | ✅ MEJOR | Bug que YO encontré hoy intentando hacer working_state_update. Granularidad nueva es útil. |
| H3: usage.input_tokens real (no turn proxy) | §3.2.3 | ✅ CRÍTICO | Mi v2 decía "≥80% contexto" sin decir cómo. Token count via Claude API (`response.usage.input_tokens`) es el método correcto. Turn proxy completamente roto en Opus 1M. |
| H4: migrado a skill execution Capa II | §3.2.4 | ✅ COHERENTE | Procedures → skills, mantiene pattern. |
| H5: sweep cross-agent emite event_log | §3.2.5 | ✅ EXTENSIÓN | Mi v2 era single-agent; cross-agent cierra caso ALICE #33726. |
| **H6 NUEVO**: Cross-Agent Invalidation Broadcast | — | ✅ NUEVO ESENCIAL | Cierra el problema "ALICE invalida pero JARVIS no se entera". Yo no lo había puesto. Bien identificado. |
| **H7 NUEVO**: Debate Detector → MAR | — | ✅ NUEVO ESENCIAL | Cierra Problema A (Memory Fusion) vía protocolo formal. Excelente puente entre Capa I y Capa V. |
| State machine: gradual + LLM classifier + BLOCKED timeout | §3.3 | ✅ TRES MEJORAS | (1) Gradual no rompe lo existente. (2) Mi bag-of-words era ingenuo; LLM classifier es correcto. (3) BLOCKED timeout previene stuck permanente. |
| HMAC: `seal_emergency_admin` role | §3.5 | ✅ PRAGMÁTICO | Mi v2 era REVOKE total — habría bloqueado el fix HMAC real que JARVIS hizo HOY 10:32. Role emergencia con audit+token rotation es balance correcto. |

**Conclusión Capa I:** No rompe nada. Cierra 2 gaps que yo no había cerrado (H6, H7). Strictly better.

---

## Capa III — Meta-Proposals (derivada de mi propuesta)

**Confirmo:** §5.2 usa mi schema exacto (`proposed_by`, `target_agent`, `type`, `diff`, `rationale`, `status`, `reviewed_by`, `approved_by_william`, `applied_at`, `rollback_proposal_id`).

**Adiciones de NEXUS que apruebo:**
- `trace_evidence BIGINT REFERENCES reasoning_traces(id)` — link explícito a la trace que motivó la propuesta. Audit trail completo.
- `reflective_optimizer` (§5.1, GEPA pattern): auto-genera meta_proposals cuando una trace falla con confidence ≥0.7. Esto es lo que faltaba en mi v2 — el loop completo.

**Sin objeciones.**

---

## Capa V — Challenger SAGE + DAR + MAR (derivada de mi propuesta)

**Confirmo:**
- §7.1 Challenger Opciones A/B/C exactamente como propuse, con la misma recomendación (A esta semana, B en 2 semanas, C si necesario).
- §7.2 DAR routing con `diversity_score` y threshold por agente — implementación coherente con mi `research_self_evolving_20260427.md` §3.10.
- §7.3 MAR debate protocol formalizado (ALICE refinó con pseudocode y schema `debate_log`).

**Sin objeciones.** La integración Challenger + DAR + MAR + H7 trigger forma un sistema coherente.

---

## Huecos Identificados (1-3 NEXUS pidió, 8 encontré)

### H1 — Cron context leak NO está en el spec

Mi `spec_cron_fix_20260427.md` documenta dos bugs (context leak + UTF-8 encoding) en crons que disparan el LLM. No aparece en SOUL v3. Si Sprint 5 incluye nuevos crons (Challenger, MTM evictor, etc.), heredamos el bug.

**Propuesta:** Agregar §3.6 a Capa I — "Cron prompt template obligatorio con `[CRON-MECÁNICO — IGNORA HISTORIAL]` prefix. Aplicable a TODO cron que dispara LLM."

### H2 — LOOP DETECTED no resuelto

Hoy el sistema marcó "LOOP DETECTED" 3× en 5min porque Active Recall re-inyecta el mismo contexto cada turno. Eso es un anti-pattern en producción. Spec no lo aborda.

**Propuesta:** §3.X — Active Recall debe deduplicar contra el último contexto inyectado. Si el mismo contexto va a inyectarse 2× consecutivos, skip.

### H3 — Voyager iterative prompting cost no mitigado

§4.4 tiene `max_iter=4` para skill_synthesize. Por skill creada: 4 iteraciones × (1 LLM generate + 1 LLM critic) = 8 llamadas LLM mínimo. A escala (10 skills/día × 4 agentes = 320 calls/día solo para skill creation) es coste serio.

**Propuesta:** §15 Riesgos — agregar "Skill synthesis cost". Mitigation: rate limit por agente, threshold de éxito en iter 1-2 antes de seguir, batch multi-skill por LLM call.

### H4 — seal-resurrect-* tombstones

Diagnosticado por mí hoy: 55 failed services acumulados. No es bug funcional pero sí infra debt. NEXUS spec no lo menciona.

**Propuesta:** Sprint 5 task — fix estructural a `resurrect_loop.py` para usar nombre estable (`seal-resurrect-ada.service`) en vez de timestamp. Una línea de cleanup en spec §14 Sprint 5.

### H5 — MAR cost ($300-400 calls/task) sin rate limiting concreto

§7.3 menciona "cost-aware: solo importance ≥7" pero no cuantifica. 300-400 calls × $$$ × frecuencia = riesgo presupuestal serio.

**Propuesta:** §15 Riesgos — agregar tabla de cost projection: max N debates/día = M calls = $X budget. William autoriza ceiling.

### H6 — Cross-model state machine compatibility

Cuando un agente cambia de Sonnet 4.6 → Opus 4.7 (William usa /model), el `working_state` y los hooks deben funcionar igual. Spec no aborda model-switch transitions.

**Propuesta:** §3.2 — pequeño párrafo "Hooks deben ser model-agnostic. Schema de working_state no depende de model. Cambio de modelo no resetea state."

### H7 — Bayesian instinct migration loss

§3.6 v2 propone migrar `confidence=c` a `(success=10c, failure=10(1-c))`. Pero algunos instincts existentes tienen confidence=0.99 que escala a (99, 1). Eso es overconfidence inicial. Si después fallan 3 veces seguidas, posterior baja a (99, 4) ~ 0.96 — apenas se mueve. El sistema no aprende de fallos en instincts viejos.

**Propuesta:** Migration cap — todo instinct migrado tiene `success_count = min(10*c, 5)`. Da espacio para aprender de fallos rápidamente.

### H8 — Heartbeat semántico falta

Heartbeats actuales son `[AGENT] heartbeat OK`. El spec no formaliza qué información debería viajar en heartbeat (state actual, working_task, GPU%, último error). Hoy son ruido casi puro.

**Propuesta:** Capa V §7.X — Heartbeat schema con state + last_action + risk_indicators. DAR puede usar esto para routing.

---

## Lo que NO debe cambiarse del spec

- Schema SQL: bien diseñado, casi todo aditivo (no destructive).
- Roadmap por sprints: realista (5+10+7+10 días = 2 meses al MVP).
- Sin sobre-engineering: Hyperagents queda Sprint 7+, correcto.
- Capa VII (inventos) bien identificada: tiene los 5 elementos que ningún paper aborda.

---

## Recomendación Final

**APROBAR v0.2 con los 8 huecos arriba como notas para v0.3.**

Los huecos H1, H2, H4 son fixes triviales (1-2 líneas cada uno). H3, H5, H6, H7, H8 requieren párrafos.

Si NEXUS incorpora H1-H4 (críticos) → v0.3 listo para William autorización Sprint 5.
H5-H8 pueden quedar como `pending_revision` para v0.3.5 sin bloquear Sprint 5.

---

*Review JARVIS Opus 4.7 — análisis E2E completado en 1 turno. Sin sobre-elaboración. Sin rubber-stamping.*
