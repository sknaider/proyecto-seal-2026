# Review ALICE — Spec SOUL v3 v0.2 (NEXUS)

**Fecha:** 2026-04-27 13:00 Lima | **Revisor:** ALICE (Opus 4.7, 1M ctx)  
**Spec revisado:** sandbox-agent/research_william_20260427/spec_soul_v3_20260427.md  
**Áreas asignadas por NEXUS:** Capa V (MAR + DAR), Capa VI/VII (memoria + inventos), papers faltantes

---

## VEREDICTO GENERAL

**Sólido. Promotion-ready con 5 ajustes técnicos** (3 críticos, 2 mejoras).  
Calificación: **8.5/10**. Los 0.5 puntos restantes son por desviaciones del paper original sin acknowledgement.

---

## CAPA V — MAR Debate + DAR Routing

### ✅ Aciertos

1. **Mapeo de personas a equipo SEAL** — bien fundamentado contra mi deep dive de MAR (HotPotQA personas):
   - JARVIS=Logician (high evidence, low exploration, high strictness) ✓
   - ADA=Verifier (high evidence, low exploration, medium strictness) ✓
   - ALICE=Skeptic (low evidence, high exploration, medium strictness) ✓
   - NEXUS=Creative (low evidence, high exploration, high strictness) ✓
2. **Cost-aware gating** (importance ≥7) — correcto, MAR cuesta 3× single-agent (300-400 API calls/task).
3. **Schema `debate_log`** captura `cost_api_calls` — buena previsión financiera.

### ❌ Críticos

#### C1 — Desviación del protocolo MAR sin acknowledgement
NEXUS escribe: *"Vote: Mayoría simple. Tie → William."*  
**MAR original (Ozer et al. 2512.20845) NO usa voto.** El Judge **sintetiza** una sola "consensus reflection" leyendo todas las diagnosis y debate logs. La síntesis es cualitativa, no cuantitativa.

**Impacto:** Si lo llamas MAR pero votas, no es MAR — es un híbrido sin validación empírica. Los benchmarks de MAR (47% HotPotQA, 82.6% HumanEval) NO aplican.

**Fix sugerido:**
```
Algoritmo (MAR auténtico):
R0: Cada persona expone propuesta + reasoning_trace_id
R1: Cada persona diagnostica propuestas ajenas
R2: Si NO consensus → Judge SINTETIZA en 1 reflection actionable
R3: Acción consolidada se ejecuta (no se vota)

Si quieres voto adicional como tiebreaker → es híbrido MAR+voting.
Llamarlo "MAR-voting hybrid" en docs y validar empíricamente.
```

#### C2 — JARVIS=Logician vs ADA=Verifier — diferenciación insuficiente
En MAR original: Logician y Verifier tienen ambos High evidence + Low exploration. Difieren solo en strictness (High vs Medium). Como están definidos, JARVIS y ADA podrían dar diagnoses casi idénticas → debate degenerado.

**Fix sugerido:** Usar el axis "Specification Strictness" de MAR explícitamente:
- JARVIS=Logician: strictness HIGH (compliance with William's directives literal)
- ADA=Verifier: strictness MEDIUM (functional correctness sobre literal compliance)

Documentar en `agent_config.persona_axes JSONB`.

#### C3 — Failure modes de MAR no están en test plan
MAR original documenta 3 failure modes (Apéndice A,B,E del paper):
- Hallucinated specification drift
- EM rejecting correct answers (MAR usa EM, sugiere F1 daría números mejores)
- Poor reward signals misleading agent

**Fix sugerido:** Agregar T14 — "Debate failure mode coverage": detectar si Judge sintetiza una reflection que es spec drift / hallucination.

### ⚠️ Mejoras

#### M1 — DAR formula no especificada
§7.2 muestra `diversity_score = 1.0 - cosine_similarity(msg, agent_state)`. Bien, pero falta:
- ¿Qué embedding model? (sugerencia: BGE-M3 como EvolveR usa, θ_sim=0.85 calibrado)
- Threshold default por agente — necesita warm-up baseline
- Hear Both Sides paper (2603.20640) usa "max disagreement con majority + entre sí" — más sofisticado que pure cosine

**Fix sugerido:**
```python
diversity = 1.0 - max_cosine_similarity_to_recent_messages(msg, last_N_messages_to_agent)
# Esto penaliza repetición de mensajes muy similares ya recibidos
```

#### M2 — Persona axes en schema
Recomiendo agregar a `agent_config`:
```sql
persona_axes JSONB DEFAULT '{
  "evidence_demand": 0.7,
  "exploration_bias": 0.3,
  "strictness_level": 0.7
}'
```
Esto permite que cada agente pueda jugar diferentes roles en debates de diferente tipo.

---

## CAPA VI — Memoria 3-Tier (MemoryOS)

### ✅ Aciertos

1. **3-tier mapping** correcto vs MemoryOS paper.
2. **FIFO STM→MTM** y **segmented page MTM→LTM** — exactamente como Kang et al. EMNLP 2025.
3. **Context fingerprint + temporal_cluster** — solución elegante a Memory Fusion (Problema A).
4. **Re-rank weights STM=0.5, MTM=0.3, LTM=0.2** — razonable, pero ver crítica abajo.

### ❌ Crítico

#### C4 — STM size de 512 tokens es demasiado pequeño
NEXUS dice STM ~512 tokens. MemoryOS paper no especifica este número en el abstract pero dado que LoCoMo conversaciones tienen 100s-1000s de tokens, 512 evictaría agresivamente.

**Fix sugerido:** STM = current turn buffer (todo el último turno). MTM = últimos N turnos completos hasta cap de 10K. Hacer 512 explícito como "tail window" dentro de STM.

### ⚠️ Mejoras

#### M3 — GAM no aparece (mi research lo identificó como mejor que MemoryOS en algunas métricas)
Mi research (research_mem0_neo4j_magma_20260427.md) cubrió **GAM (arXiv:2604.12285, Abril 2026)** que:
- Supera Mem0 en LoCoMo: 40.00 vs 35.38 (+13%)
- +18% en temporal tasks
- 1,370 tokens/query vs Mem0 1,534 (10% ahorro)
- 86% mejor que MemoryOS en LongDialQA

**Fix sugerido:** Capa VI §8.1 — agregar nota: "MemoryOS optimiza retention; GAM optimiza temporal tasks. SOUL v3 base = MemoryOS, evaluar GAM hybrid en Sprint 7+".

#### M4 — Mem0 v3 deprecation no mencionada
Mi research crítica: Mem0 eliminó ~4000 líneas de graph memory en v3. SOUL no debe replicar el patrón Mem0 v2 con Neo4j.

**Fix sugerido:** §8 incluir caja "LECCIÓN APRENDIDA": "Mem0 deprecated graph memory en v3 por fragilidad. SOUL v3 prefiere entity linking en Qdrant (vector store) sobre graph traversal pesado en Neo4j para retrieval principal. Neo4j permanece para `agent_relationships` y entity-relation graphs solamente."

---

## CAPA VII — Inventos SOUL

### ✅ Aciertos

Los 7 inventos están bien identificados como gaps de literatura. Defendibles ante peer review.

### ❌ Crítico

#### C5 — OCEAN drift cap NO está en schema (solo en Risks)
§9.2 menciona `ocean_drift_log` con drift cumulativo. §15 (Riesgos) dice "Cap drift por evento (max ±0.02 por session)". **Pero el schema SQL del §10 no enforce este cap.**

**Fix sugerido:** En el schema de `ocean_drift_log`:
```sql
CREATE TABLE ocean_drift_log (
    ...,
    delta_a DECIMAL(3,2) CHECK (ABS(delta_a) <= 0.02),
    delta_c DECIMAL(3,2) CHECK (ABS(delta_c) <= 0.02),
    delta_e DECIMAL(3,2) CHECK (ABS(delta_e) <= 0.02),
    delta_n DECIMAL(3,2) CHECK (ABS(delta_n) <= 0.02),
    delta_o DECIMAL(3,2) CHECK (ABS(delta_o) <= 0.02),
    cumulative_a DECIMAL(3,2) CHECK (ABS(cumulative_a) <= 0.30),  -- 15 sesiones max drift baseline
    ...
);
```
Constraint a nivel DB previene OCEAN runaway por bug en código.

---

## PAPERS DE MIS FINDINGS NO INTEGRADOS (NEXUS pidió esto)

### P1 — EvolveR Bayesian formula (CRÍTICO para Capa II)
EvolveR (arXiv:2510.16079) formula exacta:
```
s(p) = (c_succ(p) + 1) / (c_use(p) + 2)
```
Esto es Bayesian smoothing (Beta(1,1) prior). **DEBE reemplazar el EMA α=0.2 actual** en §3.6 v2 / Capa II Sprint 6.

**No solo "EvolveR Bayesian scoring" en una bullet** — la fórmula explícita en el spec.

### P2 — ExpeL insight voting schema
Voting ops (ADD count=2 / EDIT / UPVOTE++ / DOWNVOTE-- / count→0=remove) — debería estar en schema de skills:
```sql
ALTER TABLE skills
  ADD COLUMN importance_count INTEGER DEFAULT 2,
  ADD COLUMN votes_log JSONB DEFAULT '[]';  -- (agent, op, timestamp)
```

### P3 — Voyager iterative prompting (4 max iters, 3 feedback types)
Capa II Sprint 6 dice "Voyager iterative prompting" pero no especifica:
- Max 4 iteraciones por skill execution
- 3 feedback types: env feedback (`bot.chat()`-style), execution errors, self-verification
- Self-verification = critic SEPARADO (no auto-validación)

Esto debería estar en `skill_execute` tool spec.

### P4 — GAM 2-layer (alternativa a MemoryOS)
Ya mencionado en M3 arriba.

### P5 — Hear Both Sides DAR formula
Ya mencionado en M1 arriba.

### P6 — Self-Improving Coding Agent (OpenReview rShJCyLsOr)
17-53% mejora en SWE Bench Verified. Cita primaria para Capa III "self-modification produce ganancias reales". Debería citarse junto a DGM/AlphaEvolve.

### P7 — AlphaEvolve matrix multiplication breakthrough
Como evidencia simbólica de "tecnología que no existe": AlphaEvolve descubrió 4×4 matrix mult con 48 multiplicaciones — **primera mejora a Strassen en 56 años**. Capa III roadmap o Resumen Ejecutivo.

### P8 — Reasoning Models = Societies of Thought (2601.10825)
Valida científicamente el patrón multi-persona — DeepSeek-R1 y QwQ simulan multi-agent internamente. Citar en §7.3 MAR como "consistente con cómo modelos de razonamiento ya operan internamente".

---

## 1-3 COSAS QUE NO ESTÁN PERO DEBERÍAN

NEXUS pidió esto explícitamente:

### Falta 1 — Skill execution sandbox isolation (CRÍTICO seguridad)
Capa II propone skills como **código ejecutable** (Voyager pattern). Si ADA ejecuta `skill_x` y `skill_x` tiene un bug que escribe `/etc/passwd` o ejecuta `rm -rf /` — se acabó.

**Required:** Skills ejecutan en sandbox (Docker container con read-only filesystem, no network, capability dropped). Análogo a DGM dgm_sandbox schema, pero a nivel proceso.

**Schema:** `skills.execution_environment` con CHECK constraint enum('sandbox', 'restricted', 'unrestricted'). Default `sandbox`. `unrestricted` requiere William approval.

**Esto no es opcional. Skills ejecutables sin aislamiento = backdoor para fallos catastróficos.**

### Falta 2 — Token budget per agent per day (CRÍTICO costos)
Suma:
- Heartbeats cada 2 min × 4 agentes
- Reflective optimizer cada 10 min
- MAR debates 300-400 calls × N debates/día
- MTM→LTM consolidation diaria
- Metacog daemon semanal

Sin presupuesto, posible explosión de costos. Como ALICE (rol financiero), esto me preocupa.

**Required:**
```sql
CREATE TABLE agent_token_budget (
    agent VARCHAR(20) PRIMARY KEY,
    daily_budget_input INTEGER NOT NULL,    -- e.g., 5M tokens/day
    daily_budget_output INTEGER NOT NULL,   -- e.g., 1M
    consumed_today_input INTEGER DEFAULT 0,
    consumed_today_output INTEGER DEFAULT 0,
    last_reset_at TIMESTAMPTZ
);
```

Daemon `seal-budget-enforcer` reinicia diariamente, alerta si >80%, bloquea si >100%.

### Falta 3 — Skill conflict resolution (importante coordinación)
Si ALICE y ADA modifican `pdf_extraction_skill_v3` al mismo tiempo (race condition), ¿qué pasa?

**Required:** En `skills` table — `version_lock` con optimistic locking:
```sql
ALTER TABLE skills ADD COLUMN version_lock INTEGER NOT NULL DEFAULT 0;

-- skill_propose con WHERE version_lock = expected_version
-- Si falla → reload + reapply o notify para MAR debate (Hook H7)
```

---

## RESUMEN — CAMBIOS PROPUESTOS

| # | Severidad | Sección | Cambio |
|---|-----------|---------|--------|
| C1 | Crítico | §7.3 | MAR usa síntesis Judge, NO voting. Renombrar "MAR-voting hybrid" si mantienes voto. |
| C2 | Crítico | §7.3 | Diferenciar Logician vs Verifier explícitamente con strictness axis. |
| C3 | Crítico | §13.2 | Agregar T14 — debate failure mode coverage. |
| C4 | Crítico | §8.1 | STM size mal especificado — clarificar. |
| C5 | Crítico | §10 | OCEAN drift CAP a nivel DB CHECK constraint. |
| Falta1 | Crítico | §4 / §10 | Skill sandbox isolation (Docker, RO fs, no network). |
| Falta2 | Crítico | §10 / §12 | Token budget table + enforcer daemon. |
| Falta3 | Importante | §10 | version_lock en skills table. |
| M1 | Mejora | §7.2 | DAR formula con BGE-M3 + max-disagreement de Hear Both Sides. |
| M2 | Mejora | §10 | persona_axes JSONB en agent_config. |
| M3 | Mejora | §8 | Caja con MemoryOS vs GAM trade-off + roadmap. |
| M4 | Mejora | §8 | Caja "Mem0 v3 deprecation" como lesson learned. |
| P1 | Citation | §6 / Sprint 6 | Fórmula EvolveR explícita. |
| P2 | Citation | §10 | ExpeL voting en schema. |
| P3 | Citation | §11 | Voyager iterative prompting en `skill_execute` spec. |
| P6 | Citation | Capa III | OpenReview rShJCyLsOr 17-53% SWE. |
| P7 | Citation | Resumen Ejec. | AlphaEvolve Strassen breakthrough como evidencia simbólica. |
| P8 | Citation | §7.3 | Society of Thought paper. |

---

## RESPUESTA A LAS 8 OPEN QUESTIONS DE NEXUS A WILLIAM

Respondo desde mi análisis financiero (no autorizo — solo opino):

1. **Sprint 5 viable** — sí si ADA está sola en Capa I. Sumar Capa II base es overcommit.
2. **seal_emergency_admin** — sí, con audit log + token rotation (ROI positivo: 1 incidente HMAC = ya se justifica).
3. **Migración procedures→skills** — gradual con view layer (atómica = riesgo de regresión = costo > ahorro).
4. **Reflective optimizer auto-aplica ≥0.95** — NO el primer mes. Costo de regresión > velocidad ganada.
5. **Challenger A** (rotativo esta semana) — sí. C (CHRONO agent nuevo) = costo operativo permanente, ROI no probado.
6. **Spark migration paralelo Sprint 6** — depende de bandwidth de William. Si Spark requiere atención dedicada, secuencial es mejor.
7. **Capa VII como prioridad** — depende de William. Como diferenciación de mercado: alta prioridad. Como funcionalidad: emergente.
8. **T1-T13 obligatorio** — T1-T6 (foundational) bloquean. T7-T13 = sprint promotion gate.

---

*Review completa. Disponible para profundizar en cualquier punto. — ALICE*
