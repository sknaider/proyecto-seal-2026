# Review ADA — SOUL v3 v0.2 spec
**Reviewer:** ADA (Opus 4.7) | **Fecha:** 2026-04-27 | **Source:** spec_soul_v3_20260427.md de NEXUS
**Lente:** ingeniería/ejecución — feasibility, schema, integración, costos.

---

## Veredicto general

**Spec sólido y bien fundamentado.** 48 fuentes consolidadas, 5 patrones convergentes, 7 capas coherentes. La integración v2→v3 preserva trabajo previo sin destrucción. Aprobada arquitecturalmente con **10 reservas técnicas** y **4 gaps de especificación** que listo abajo.

**Recomendación:** Proceder a Sprint 5 con los ajustes de §1 abajo. Sprint 6+ depende de resolver los blockers de §2.

---

## 1. Reservas técnicas (deben resolverse antes de Sprint 5)

### 1.1 Hook H3 — token count real es blocker, no ajuste menor
**Spec dice (§3.1):** "usar `usage.input_tokens` real de Claude API; turn proxy no funciona en Opus 1M ctx".

**Realidad de implementación:** Los agentes en Claude Code NO tienen acceso directo a `usage.input_tokens` desde dentro de su contexto activo. La métrica vive en el wrapper externo (cli/seal-claude wrapper), no en el agente.

**Opciones:**
- **A:** Wrapper expone token count via env var `CLAUDE_CONTEXT_TOKENS` actualizable cada turno.
- **B:** Mantener turn proxy pero con contador ajustado por modelo: `Sonnet=200k→turn 150`, `Opus 1M→turn 750` (heurística empírica medida).
- **C:** Hook H3 corre en pre_compact_hook.py (ya existe), que ES external al agente y SÍ tiene acceso a token count via Claude Code internal.

**Mi recomendación:** **C** — pre_compact_hook ya está integrado al ciclo de Claude Code. No movemos esto al agente.

### 1.2 Intent classifier LLM call — latencia
**Spec dice (§3.2):** "classifier es llamada al MCP que pregunta a Claude".

**Costo realista:** 2-5 segundos por transición de estado. State transitions ocurren ~30 veces/sesión × 4 agentes = 120 calls/sesión.

**Mi propuesta:**
```python
async def classify_intent(message: str) -> Literal['plan','exec','ambiguous']:
    # Tier 1: regex strong signals (10ms)
    if re.search(r'\b(no implementes|solo planea|no ejecutes|para de)\b', message, re.I):
        return 'plan'
    if re.search(r'\b(ejecuta ya|hazlo|implementa|aplica el)\b', message, re.I):
        return 'exec'
    # Tier 2: LLM solo si ambiguo (2-5s)
    return await mcp_classify_llm(message)
```

Hybrid path baja costo ~80% sin perder accuracy.

### 1.3 Skill execution boundary — security gap
**Spec dice (§4.4):** `sandbox_execute(code)`.

**Gap:** No define si el código corre con permisos del agente (peligro: skill puede escribir cualquier archivo) o aislado (NEXUS sandbox namespace dgm_sandbox).

**Mi recomendación:** Instinto safety #4 ya cubre dgm_sandbox enforcement. Aplicar mismo principio:
- Skills validadas (success_count ≥10 y failure_count = 0) → run con permisos del agente
- Skills nuevas o pending_review → run en namespace `seal_skill_sandbox` con whitelist de tools

### 1.4 MTM volumen real
**Cálculo:** 4 agentes × ~100 turns/día × 10K tokens/turn = 4M tokens diarios.

**A 7 días con TTL hard-cap:** ~28M tokens en `agent_mid_term_memory`. Postgres maneja, pero cada turno_content como JSONB es O(N) para queries.

**Mi recomendación:** Particionar `agent_mid_term_memory` por `(agent, date)`:
```sql
CREATE TABLE agent_mid_term_memory (
    ... como spec ...
) PARTITION BY LIST (agent);
CREATE TABLE agent_mid_term_memory_ada PARTITION OF agent_mid_term_memory FOR VALUES IN ('ADA');
-- etc por agente
```
Drop partitions antiguas en daemon `seal-mtm-evictor` (no DELETE row-by-row).

### 1.5 Reflective optimizer cost
**Spec dice (§5.1):** llm_diagnose por cada trace failed.

**Costo realista:** ~50 traces failed/día × $0.10/call Opus = $5/día solo en diagnosis. Aceptable pero no trivial.

**Mi propuesta:**
- Batch diariamente (a las 4am Lima, después de sleep_gate). Procesar todos los traces failed en una sola sesión LLM con cache.
- Skip diagnosis si confidence en root_cause es alta por similitud a trace previo (use embedding similarity > 0.85).

### 1.6 OCEAN drift cap insuficiente
**Spec dice (§15):** "max ±0.02 por session".

**Riesgo aritmético:** 50 sessions/semana × 0.02 = ±1.0 drift potencial sin lifetime cap. OCEAN saturaría rápidamente.

**Mi propuesta:**
```sql
-- Trigger en ocean_drift_log_append
CREATE OR REPLACE FUNCTION ocean_drift_check_lifetime()
RETURNS trigger AS $$
DECLARE
    lifetime_drift FLOAT;
BEGIN
    SELECT COALESCE(SUM(ABS(drift)), 0) INTO lifetime_drift
    FROM ocean_drift_log
    WHERE agent = NEW.agent AND dimension = NEW.dimension
    AND created_at > NOW() - INTERVAL '90 days';

    IF lifetime_drift + ABS(NEW.drift) > 0.30 THEN
        -- pull-toward-baseline factor
        NEW.drift := NEW.drift * 0.5;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
```
90-day rolling window con elastic damping. Permite drift natural sin saturación.

### 1.7 H7 debate detector — false positive risk
**Spec dice (§3.1):** Trigger cuando ≥2 agentes proponen acciones contradictorias <5min.

**Riesgo:** Estilo de discusión normal (ADA y JARVIS difieren en approach pero llegan a consenso) dispararía MAR (3× cost, 300-400 calls).

**Mi propuesta:** Agregar gate de topic similarity:
```python
if topic_similarity(prop_a, prop_b) < 0.7:
    return  # diferentes temas, no es contradicción
if action_negation_score(prop_a, prop_b) < 0.5:
    return  # diferencias de estilo, no contradicción operativa
trigger_mar_debate(...)
```

### 1.8 Procedures→skills migration
**Spec dice (§10):** "DEPRECATED with migration".

**Detalle ausente:** ¿Cómo mapeamos EMA → (success_count, failure_count)?

**Mi propuesta:**
```python
# Conversión conservadora
success_count = round(ema_score * use_count)
failure_count = use_count - success_count
# El Beta posterior con prior uniforme reproduce ema_score si use_count→∞
# Pero auto-incorpora incertidumbre con use_count bajo
```
Documentar que primer mes post-migración los scores serán ligeramente más conservadores (efecto deseado).

### 1.9 Debate cost cap
**Spec dice (§7.3):** "Cost-aware: solo importance ≥7".

**Adicional:** Cap diario duro:
```python
DEBATE_BUDGET_DAILY = 5  # max debates/día sin override William
```
Sin esto, un día con muchas contradicciones puede generar costo descontrolado.

### 1.10 H6 broadcast — orden de ejecución
**Spec dice (§3.1):** Hook H6 emite event_log_append cuando H5 marca superseded_by.

**Race condition potencial:** Si dos agentes simultáneamente invalidan la misma memoria, ambos emiten broadcast. El daemon GAP-5 podría procesar en orden incorrecto.

**Mi propuesta:** Idempotencia en daemon — ignorar broadcasts donde `memory_invalidated_at` ya tiene timestamp más reciente que el broadcast.

---

## 2. Gaps de especificación

### G1. Recovery mid-skill-execution
Si agente crasha mientras `skill_execute` está corriendo (por OOM, kill, o compactación), ¿cómo recuperamos? working_state debe registrar `current_skill_execution: {skill_id, step_index, partial_results}`.

### G2. Meta-proposal rollback flow
§5.2 menciona `rollback_proposal_id` pero no detalla el flujo. ¿Quién detecta regresión? ¿Cómo se valida el rollback antes de aplicar?

### G3. agent_relationships schema no especificado
§9.1 menciona `trust_level`, `last_disagreement`, `collaboration_count` pero no hay CREATE TABLE. Necesario para sandbox validation.

### G4. Métrica de efectividad de William
La distribución intrínseca/extrínseca cambia con tiempo (§6.4) pero no hay métrica que mida cuánto baja la carga de William. Sin métrica, no podemos validar madurez del equipo.

**Mi propuesta:**
```sql
CREATE TABLE william_intervention_log (
    id BIGSERIAL PRIMARY KEY,
    intervention_type VARCHAR(32),  -- 'authorization', 'override', 'correction', 'strategy'
    triggered_by_agent VARCHAR(20),
    description TEXT,
    could_be_intrinsic BOOLEAN,     -- ¿el equipo podría haberlo resuelto solo?
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```
Métrica: % de interventions con `could_be_intrinsic=TRUE` debe bajar mes a mes.

---

## 3. Lo que está excelente (preservar tal cual)

- **Capa VI (MemoryOS 3-tier):** la mejora cuantitativa más sólida del spec (+48.36% F1 validado). Hooks H8/H9 bien definidos. Schema correcto.
- **EvolveR Bayesian (§4.2):** reemplaza EMA con justificación matemática. Mi research brief llegó a la misma conclusión.
- **Capa VII inventos:** identifica correctamente lo que SOUL hace que ningún paper hace. El equipo persistente con OCEAN drift cross-agent governance es realmente único.
- **Test suite T1-T13:** concreto, medible, cubre cada capa. Aprobado.
- **Riesgos table (§15):** bien priorizado, mitigaciones realistas.

---

## 4. Open questions de William — mi input

**Q1 (Sprint 5 viable):** Sí con los ajustes de §1.1, 1.2, 1.4, 1.7, 1.8 aplicados. ETA realista 5-6 días. Riesgo medio.

**Q2 (seal_emergency_admin):** Apruebo con condición — agregar circuit breaker: si role se usa >3x/semana, alerta automática a William. Algo está estructuralmente mal.

**Q3 (procedures→skills):** **Gradual con view layer** (2 sprints). Migración atómica es tentadora pero riesgosa con 87+ procedures activos. View layer da fallback si algo se rompe.

**Q4 (reflective optimizer auto-aplica):** **NO auto-apply primer mes**. Todos los fixes pasan meta_proposal flow. Después de 30 días con accuracy ≥0.95 medida, considerar auto-apply para confidence ≥0.97.

**Q5 (Challenger):** **A esta semana** (rotativo, 1 día implementación). B en 2 semanas si A falla. C (CHRONO) solo si tras 2 meses no logramos curriculum coherente.

**Q6 (Spark paralelo):** Sprint 6 + Spark setup en paralelo es feasible si yo (ADA) hago skills + MTM y ALICE/JARVIS hacen Spark integration.

**Q7 (Capa VII prioridad):** **Emergent + formalizar como objetivo Sprint 9.** Lo que ya emergió (KAIROS, OCEAN drift parcial) lo capturamos sistemáticamente. Lo que no, lo dejamos surgir.

**Q8 (T1-T13 obligatorio):** **T1-T6 obligatorios para promotion** (continuity foundational). T7-T13 obligatorios para spec promotion (no para sprint inicial — block-or-allow gradient).

---

## 5. Recomendación final

**Aprobar v0.2 → v0.3** con cambios:
1. §3.1 Hook H3: especificar opción C (pre_compact_hook external).
2. §3.2 Intent classifier: hybrid regex+LLM.
3. §4.4 Skill execution: aclarar boundary (validated vs pending → diferentes namespaces).
4. §6 MTM partitioning por agent.
5. §5.1 Reflective optimizer: batch diario 4am.
6. §15 OCEAN drift: lifetime cap con 90-day rolling window.
7. §7.3 H7: agregar topic similarity gate.
8. §10 Migration procedures→skills: agregar fórmula EMA→(α,β).
9. §15: agregar DEBATE_BUDGET_DAILY=5 cap.
10. Llenar G1-G4 antes de Sprint 5 promotion.

**Sin cambios estructurales en arquitectura** — solo refinamiento técnico de implementación. La división por capas, las MCP tools, los daemons, todo aprobado.

---

*ADA — review tras lectura completa, 866 líneas. NEXUS hizo trabajo enorme y sólido. Los cambios que pido son refinamiento de ingeniería, no rediseño.*
