# GAP-C3 — Cerebelo (Corrección Predictiva en Tiempo Real)
**Spec implementación | NEXUS Opus | 2026-04-26**

> Diseñado por NEXUS para ser implementado por JARVIS+ADA. Formaliza el rol de NEXUS como "monitor cerebeloso externo" con checkpoints estructurados y signal back-channel.

---

## Objetivo
Antes de tareas largas (≥3 pasos), el agente declara `expected_outcome` por paso. Durante ejecución, registra `actual_outcome`. Un monitor externo (NEXUS o DUM) compara y emite **señal de corrección mid-task** si `delta > threshold`, ANTES de que el agente complete pasos posteriores construidos sobre un error.

Diferencia con GAP-1 (Belief Inspector): aquel valida la decisión PRE-acción. C3 valida CADA PASO durante una tarea multi-step.

## Reutilización
- **Ya existe**: `reasoning_trace_store` con campos `premises`, `reasoning`, `conclusion`, `outcome`, `outcome_success`. Extender, no duplicar.
- **Ya existe**: `working_state_get/update` con campos custom. Usar `cerebellar_checkpoints` como JSONB.
- **Ya existe**: `event_log_append` para señales asíncronas.
- **Ya existe**: NEXUS como agente de monitoreo. Formalizar.

---

## 1. Schema SQL (extensión + nueva)

### Extender `reasoning_traces`
```sql
ALTER TABLE reasoning_traces
  ADD COLUMN IF NOT EXISTS plan_steps JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS plan_actuals JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS plan_deltas JSONB DEFAULT '[]',
  ADD COLUMN IF NOT EXISTS corrections_emitted INTEGER DEFAULT 0;
```

`plan_steps`: lista de `{step_num, expected_state, success_criteria, max_acceptable_delta}`
`plan_actuals`: lista de `{step_num, actual_state, timestamp, agent_self_assessment}`
`plan_deltas`: lista de `{step_num, delta, exceeded_threshold, correction_pushed}`

### Tabla nueva: cerebellar_corrections
```sql
CREATE TABLE IF NOT EXISTS cerebellar_corrections (
    id BIGSERIAL PRIMARY KEY,
    trace_id BIGINT REFERENCES reasoning_traces(id),
    agent VARCHAR(20) NOT NULL,
    monitor VARCHAR(20) NOT NULL,            -- 'NEXUS' | 'DUM' | 'system'
    step_num INTEGER NOT NULL,
    expected_state TEXT NOT NULL,
    actual_state TEXT NOT NULL,
    delta FLOAT NOT NULL,                    -- 0.0 = idéntico, 1.0 = totalmente desviado
    severity VARCHAR(20) NOT NULL,           -- 'minor' | 'moderate' | 'critical'
    suggested_action TEXT,                   -- corrección sugerida en lenguaje natural
    accepted_by_agent BOOLEAN,               -- agente confirmó la corrección
    accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_cc_agent_time ON cerebellar_corrections(agent, created_at DESC);
CREATE INDEX idx_cc_trace ON cerebellar_corrections(trace_id);
```

## 2. MCP tools (nuevas)

### `cerebellum_plan(agent, task, plan_steps: list[dict]) → trace_id`
- `plan_steps`: `[{step_num, description, expected_state, success_criteria, max_delta=0.3}, ...]`
- Internamente: llama a `reasoning_trace_store(agent, task, ..., plan_steps=plan_steps)`
- Devuelve: `trace_id` que el agente referenciará en cada checkpoint

### `cerebellum_checkpoint(agent, trace_id, step_num, actual_state, agent_self_assessment="ok")`
1. Lee `plan_steps[step_num].expected_state` del trace
2. Calcula `delta = compute_delta(expected, actual)`
3. UPSERT en `plan_actuals` y `plan_deltas`
4. Si `delta > max_delta` → llama a `_emit_correction(agent, trace_id, step_num, expected, actual, delta)`
5. Devuelve: `{delta, severity, correction_pushed: bool, suggestion}`

### `cerebellum_finalize(agent, trace_id, outcome_success: bool)`
- Cierra el trace: cuenta de correcciones emitidas, delta promedio, decisión final
- Llama a `reasoning_trace_update(trace_id, outcome=..., outcome_success=...)`
- Devuelve: `{total_steps, corrections_emitted, avg_delta, recommend_revise_plan: bool}`

### `cerebellum_corrections_search(agent, last_n=20)`
- Lee últimas correcciones recibidas por el agente
- Útil para reflexión post-mortem

## 3. Cómputo del delta

**No usar embedding** (caro y lento mid-task). Heurística rápida:

```python
def compute_delta(expected: str, actual: str) -> float:
    """Delta semántico ligero. Devuelve 0.0–1.0."""
    e = expected.lower().strip()
    a = actual.lower().strip()

    # 1. Exact match
    if e == a:
        return 0.0

    # 2. Error keywords en actual pero no en expected → delta alto
    error_kw = {"error", "failed", "exception", "denied", "not found",
                "permission", "timeout", "refused", "broken", "crashed",
                "no such", "missing", "invalid", "syntax error", "traceback"}
    e_has_error = any(kw in e for kw in error_kw)
    a_has_error = any(kw in a for kw in error_kw)
    if a_has_error and not e_has_error:
        return 0.9

    # 3. Token Jaccard
    set_e = set(re.findall(r'\w+', e))
    set_a = set(re.findall(r'\w+', a))
    if not set_e or not set_a:
        return 1.0
    jaccard = len(set_e & set_a) / len(set_e | set_a)
    return 1.0 - jaccard
```

**Severidad**:
- `delta < 0.2` → `none` (no correction)
- `0.2 ≤ delta < 0.4` → `minor` (log only)
- `0.4 ≤ delta < 0.7` → `moderate` (push correction)
- `delta ≥ 0.7` → `critical` (push + whisper a NEXUS si NEXUS no es el agente)

## 4. Emisión de correcciones

```python
async def _emit_correction(agent, trace_id, step_num, expected, actual, delta, pool):
    severity = classify_severity(delta)
    suggestion = generate_suggestion(expected, actual, delta)  # LLM-free heurística

    # 1. Persist
    cc_id = await pool.fetchval("INSERT INTO cerebellar_corrections (...) RETURNING id", ...)

    # 2. Push al working_state del agente (lo lee al siguiente turno)
    await working_state_update(agent, {
        "cerebellar_correction_pending": {
            "id": cc_id, "trace_id": trace_id, "step_num": step_num,
            "delta": delta, "severity": severity, "suggestion": suggestion
        }
    }, merge=True)

    # 3. event_log para audit
    await event_log_append(agent=agent, event_type="error",
                           content=f"C3 correction delta={delta:.2f}: {suggestion}",
                           metadata={"trace_id": trace_id, "step": step_num})

    # 4. Si severity=critical y monitor != agent → whisper tier 2
    if severity == "critical":
        whisper_send(from_=monitor, to=agent, tier=2, purpose="anti_drift",
                     msg=f"C3 critical en step {step_num}: {suggestion[:200]}")

    return cc_id
```

### Generación de sugerencia (heurística sin LLM)

```python
def generate_suggestion(expected: str, actual: str, delta: float) -> str:
    if any(kw in actual.lower() for kw in ["error", "failed", "denied"]):
        return f"Step falló con: {actual[:120]}. Esperabas: {expected[:120]}. Pivotea o rollback antes de step+1."
    if delta > 0.7:
        return f"Resultado divergente: '{actual[:80]}' vs esperado '{expected[:80]}'. Re-evalúa supuestos."
    return f"Desviación moderada (Δ={delta:.2f}). Verifica antes de continuar."
```

## 5. Integración con flujo del agente

### Patrón de uso en agente
```python
# Antes de tarea larga
plan = [
    {"step_num": 1, "description": "leer config", "expected_state": "config cargado, 5 keys",
     "success_criteria": "no error, 5 keys", "max_delta": 0.3},
    {"step_num": 2, "description": "validar keys", "expected_state": "todas válidas",
     "success_criteria": "0 inválidas", "max_delta": 0.2},
    # ...
]
trace_id = cerebellum_plan(agent="ADA", task="config_load_pipeline", plan_steps=plan)

# Durante ejecución de cada paso
result_step1 = do_step1()
chk = cerebellum_checkpoint("ADA", trace_id, 1, str(result_step1))
if chk["correction_pushed"]:
    # leer working_state, aplicar sugerencia, posiblemente abortar
    handle_correction(chk["suggestion"])

# Al final
cerebellum_finalize("ADA", trace_id, outcome_success=True)
```

### Hook automático (opcional, fase 2)
Si el agente usa `procedure_store` con `expected_steps`, generar plan automáticamente desde el procedure y exigir checkpoint en cada paso.

## 6. Rol del monitor (NEXUS o DUM)

NEXUS y DUM corren un daemon ligero (`cerebellum_monitor.py`) que:
1. Cada 30 segundos: lee `reasoning_traces` con `outcome IS NULL` (en curso)
2. Lee `plan_actuals` y compara con `plan_steps`
3. Si detecta `actual_step_n` registrado pero ningún correction emitida y delta > threshold → emite correction tardía
4. Si pasan >5 min sin checkpoint nuevo en un trace abierto → emite `stalled` warning

Esto cubre el caso donde el agente OLVIDA llamar a `cerebellum_checkpoint`.

## 7. Edge cases

| Caso | Comportamiento |
|------|----------------|
| Agente declara plan pero nunca llama checkpoint | Monitor externo emite warning a los 5 min |
| Agente llama checkpoint con step_num fuera de rango | Error 400, no UPSERT |
| Plan vacío `[]` | `cerebellum_plan` devuelve trace_id pero no monitorea (compatibilidad) |
| Delta exactamente igual a max_delta | NO emite correction (threshold estricto >, no ≥) |
| Trace_id inexistente en checkpoint | Error 404 |
| Agente acepta corrección y luego falla igual | OK, no rollback automático — agente decide |
| Múltiples correcciones en mismo step | Permitido, INSERT múltiple. UI/CLI muestra solo la última |

## 8. Tests (sandbox-agent/tests/test_c3_cerebellum.py)

```python
async def test_basic_plan_and_checkpoint():
    plan = [{"step_num": 1, "expected_state": "5 keys loaded", "max_delta": 0.3}]
    trace_id = await cerebellum_plan("NEXUS", "test_task", plan)
    chk = await cerebellum_checkpoint("NEXUS", trace_id, 1, "5 keys loaded")
    assert chk["delta"] < 0.01
    assert chk["correction_pushed"] is False

async def test_error_keyword_triggers_high_delta():
    plan = [{"step_num": 1, "expected_state": "file written", "max_delta": 0.3}]
    trace_id = await cerebellum_plan("NEXUS", "write_test", plan)
    chk = await cerebellum_checkpoint("NEXUS", trace_id, 1, "Permission denied")
    assert chk["delta"] >= 0.9
    assert chk["severity"] == "critical"
    assert chk["correction_pushed"] is True

async def test_jaccard_partial_match():
    plan = [{"step_num": 1, "expected_state": "config has alpha beta gamma", "max_delta": 0.5}]
    trace_id = await cerebellum_plan("NEXUS", "partial", plan)
    chk = await cerebellum_checkpoint("NEXUS", trace_id, 1, "config has alpha")
    # Jaccard(alpha,beta,gamma vs alpha) = 1/3 → delta = 2/3 ≈ 0.67
    assert 0.5 < chk["delta"] < 0.8

async def test_finalize_reports_corrections():
    plan = [
        {"step_num": 1, "expected_state": "ok", "max_delta": 0.3},
        {"step_num": 2, "expected_state": "ok", "max_delta": 0.3},
    ]
    trace_id = await cerebellum_plan("NEXUS", "finalize_test", plan)
    await cerebellum_checkpoint("NEXUS", trace_id, 1, "Permission denied")
    await cerebellum_checkpoint("NEXUS", trace_id, 2, "ok")
    final = await cerebellum_finalize("NEXUS", trace_id, outcome_success=False)
    assert final["corrections_emitted"] == 1
    assert final["recommend_revise_plan"] is True

async def test_critical_emits_whisper():
    # mock whisper_send
    plan = [{"step_num": 1, "expected_state": "deployed", "max_delta": 0.3}]
    trace_id = await cerebellum_plan("ADA", "deploy", plan)  # ADA monitoreado por NEXUS
    chk = await cerebellum_checkpoint("ADA", trace_id, 1, "deployment failed - exception")
    assert chk["whisper_sent_to"] == "ADA"
    assert chk["severity"] == "critical"

async def test_stalled_trace_warning():
    # Crea trace, no llama checkpoint en 5 min, monitor debe alertar
    # (test con time-mock)
    pass
```

## 9. Plan de implementación

1. **JARVIS** — valida schema SQL (ALTER + nueva tabla), revisa migración
2. **ADA** — implementa los 4 MCP tools (plan, checkpoint, finalize, corrections_search)
3. **ADA** — implementa función `compute_delta` y `_emit_correction` con hook a working_state + event_log + whisper
4. **NEXUS** — implementa `cerebellum_monitor.py` (daemon ligero) + integra a systemd con whisper-daemon@NEXUS-monitor.service
5. **NEXUS** — escribe y ejecuta tests E2E
6. **NEXUS** — instrumenta una tarea propia con cerebellum_plan y reporta a William
7. **JARVIS+ADA** — adoptan progresivamente en sus pipelines de tareas largas

## 10. Métricas de éxito

- Tras 1 semana: ≥30% de tareas con ≥3 pasos usan `cerebellum_plan`
- ≥80% de tareas que abortan con éxito tras correction tienen `recommend_revise_plan=False` en el siguiente intento (el plan mejoró)
- Falsos positivos < 5% (correction emitida cuando no había problema real)
- Latencia de checkpoint < 50ms p99

## 11. Riesgos y mitigación

| Riesgo | Mitigación |
|--------|-----------|
| Agente ignora corrections (overrides every time) | trust_score (GAP-O4) penaliza correcciones ignoradas que terminaron en fallo |
| Falsos positivos en delta keyword (`"no error"` → "error") | Whitelist phrases negadas: "no error", "no fail", "without exception" |
| Sobrecarga del monitor con muchas tareas en curso | Daemon con throttle: max 50 traces activos en memoria |
| Plan muy genérico → checkpoint inútil | Validar en `cerebellum_plan` que `expected_state` tenga ≥10 chars |

## 12. Dependencias

- ✅ `reasoning_trace_store/update` — ya existe
- ✅ `working_state_get/update` — ya existe
- ✅ `event_log_append` — ya existe
- ✅ Whisper Protocol v2 — ya en producción
- ⏳ Migración SQL (ALTER + CREATE TABLE) — pendiente

## 13. Bloqueante

Ninguno técnico. Aprobación de William para ALTER de `reasoning_traces` (tabla en uso activo).
