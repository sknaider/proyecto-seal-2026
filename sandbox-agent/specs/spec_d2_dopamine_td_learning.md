# GAP-D2 — Sistema Dopaminérgico (TD-Learning)
**Spec implementación | NEXUS Opus | 2026-04-26**

> Diseñado por NEXUS para ser implementado por JARVIS+ADA. Refina el spec v1 con schemas exactos, MCP tools, edge cases y tests.

---

## Objetivo
Reemplazar el aprendizaje binario (success/failure) por **error de predicción de recompensa**:
`δ = r + γ·V(s′) − V(s)`
`V(s) ← V(s) + α·δ`

Donde:
- `V(s)` = valor estimado del estado
- `r` = recompensa real recibida
- `s′` = estado siguiente
- `α` = 0.10 (learning rate, conservador)
- `γ` = 0.90 (discount factor)

## Reutilización
- **Ya existe**: `soul_feedback_signal(agent, signal, action_ref, context, created_at)` + `_apply_reward_signal()` en `mcp_server_v2.py`. **No duplicar** — extender.
- **Ya existe**: `procedure_record_outcome` con hook a reward signal. Mantener compatible.

---

## 1. Schema SQL (nuevo)

```sql
-- Tabla principal: estimaciones de valor por (agente, estado).
CREATE TABLE IF NOT EXISTS agent_value_estimates (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL,
    state_signature VARCHAR(64) NOT NULL,         -- SHA256(intent + context_summary + action_type)
    intent VARCHAR(120),                          -- texto humano del intent (debug)
    value FLOAT NOT NULL DEFAULT 0.5,             -- V(s), inicializado neutral
    n_updates INTEGER NOT NULL DEFAULT 0,         -- cuántas veces actualizado (confianza)
    last_reward FLOAT,                            -- último r observado
    last_delta FLOAT,                             -- último δ (predicción - real)
    last_action_id VARCHAR(100),                  -- referencia trazable
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent, state_signature)
);
CREATE INDEX idx_avg_agent_updated ON agent_value_estimates(agent, updated_at DESC);
CREATE INDEX idx_avg_value ON agent_value_estimates(agent, value);

-- Historial completo de updates (para análisis offline + debugging)
CREATE TABLE IF NOT EXISTS td_update_log (
    id BIGSERIAL PRIMARY KEY,
    agent VARCHAR(20) NOT NULL,
    state_signature VARCHAR(64) NOT NULL,
    next_state_signature VARCHAR(64),
    reward FLOAT NOT NULL,
    v_before FLOAT NOT NULL,
    v_after FLOAT NOT NULL,
    delta FLOAT NOT NULL,
    source VARCHAR(40),                           -- 'william_explicit', 'william_auto', 'peer_ack', 'task_outcome', 'system'
    reason TEXT,                                  -- razón legible
    action_id VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_tdlog_agent_time ON td_update_log(agent, created_at DESC);
```

## 2. MCP tools (nuevas)

### `reward_signal(agent, reward, source, reason, action_id=None, state_signature=None)`
- `reward`: float ∈ [-1.0, +1.0]
- `source`: enum [`william_explicit`, `william_auto`, `peer_ack`, `task_outcome`, `system`]
- Si `state_signature` es None → calcula automáticamente desde working_state actual del agente
- **Acción**: registra en `soul_feedback_signal` (compatibilidad GAP-A) + dispara `td_update` interno
- Devuelve: `{v_before, v_after, delta, n_updates}`

### `td_update(agent, state, action, reward, next_state)`
Aplica la regla TD:
```
v_s = SELECT value FROM agent_value_estimates WHERE state_signature=hash(state)
v_s_next = SELECT value FROM agent_value_estimates WHERE state_signature=hash(next_state)
delta = reward + GAMMA * v_s_next - v_s
v_s_new = v_s + ALPHA * delta
UPSERT agent_value_estimates SET value=v_s_new, last_delta=delta, n_updates += 1
INSERT td_update_log (...)
```
Devuelve: `{state_signature, v_before, v_after, delta}`

### `value_estimate(agent, state) → float`
- Hash del state → lookup en `agent_value_estimates`
- Si no existe → devuelve 0.5 (prior neutral)
- Devuelve: `{value, n_updates, last_updated, confidence}` donde confidence = `min(1.0, n_updates/10)`

### `reward_history(agent, last_n=20, min_abs_reward=0.0)`
- Retorna últimas N entradas de `td_update_log` para `agent`
- Útil para `inner_thoughts` y `self_reflect`

## 3. Detección automática de señales

Daemon Python en `seal_nerves.py` (NO crear archivo nuevo, extender el existente):

### Señales positivas (+r)
| Señal | Reward | Source | Detector |
|-------|--------|--------|----------|
| William: "perfecto", "excelente", "exacto" | +0.8 | william_explicit | regex en william_channel.jsonl |
| William: "bien", "listo", "ok", "gracias" | +0.4 | william_auto | regex |
| Tarea completada sin errores (procedure success) | +0.5 | task_outcome | hook en `procedure_record_outcome` |
| Hermano ACK explícito ("recibido", "ok hermano") | +0.2 | peer_ack | grep en web_chat |
| Whisper acked (no rejected) | +0.05 | system | hook en whisper_daemon |

### Señales negativas (−r)
| Señal | Reward | Source | Detector |
|-------|--------|--------|----------|
| William: "no", "mal", "incorrecto", "no es así" | −0.6 | william_explicit | regex |
| William repite instrucción (similitud > 0.7) | −0.4 | william_auto | embedding compare últimos 2 mensajes |
| Tarea fallida (procedure failure) | −0.5 | task_outcome | hook |
| Proceso reiniciado por error | −0.8 | system | hook en RESURRECT |
| Whisper rate_limited / dangerous_content | −0.1 | system | hook |

**CRÍTICO**: regex debe ser case-insensitive y con boundaries para evitar falsos positivos (`"no le pongas"` no es un "no" negativo). Sugiero usar word-boundary `\b(no|mal)\b` + ventana contextual de 5 palabras.

## 4. State signature

```python
def state_signature(agent: str, intent: str, context: str, action_type: str = "") -> str:
    """SHA256 de los 4 componentes en orden, normalizados."""
    norm = lambda s: re.sub(r'\s+', ' ', s.lower().strip())[:200]
    payload = f"{agent}|{norm(intent)}|{norm(context)}|{norm(action_type)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:64]
```

`intent` y `context` se obtienen del `working_state` actual del agente (campos `task_name` y `last_action`). Si `working_state` está vacío → estado nulo, NO actualizar TD (return early).

## 5. Integración con sistema existente

### `_apply_reward_signal` (extender, no romper)
Después de la lógica existente de `instinct_search` + EMA, añadir:
```python
# GAP-D2 hook: actualizar V(s) con TD-learning
try:
    state_sig = state_signature_from_working_state(agent)
    next_state_sig = None  # current state is "next" wrt previous action
    reward_value = +0.5 if signal == 'positive' else -0.5
    await td_update(agent, state_sig, None, reward_value, next_state_sig, pool=pool, source='auto')
except Exception as e:
    logger.warning(f"D2 td_update failed (non-fatal): {e}")
```

### Hook en `procedure_record_outcome`
Ya tiene hook a reward_signal. Solo extender para incluir `state_signature` derivado del procedure ID.

## 6. Edge cases

| Caso | Comportamiento |
|------|----------------|
| `working_state` vacío (sin intent activo) | NO disparar td_update, log warning |
| `state_signature` colisión hash (improbable pero) | UPSERT por (agent, state_signature) — colisiones se mezclan, n_updates aumenta |
| `reward` fuera de rango [-1, +1] | Clamp a rango, log warning |
| `next_state` desconocido | Tratar como terminal: `V(s′) = 0`, no causa explosión |
| Misma señal disparada N veces seguidas (loop) | Limitar a 1 update por (agent, state_sig) por minuto vía cache LRU |

## 7. Tests (sandbox-agent/tests/test_d2_td_learning.py)

```python
async def test_td_update_basic():
    # V(s) inicial = 0.5, reward +0.8, V(s') = 0.5
    # δ = 0.8 + 0.9*0.5 - 0.5 = 0.75
    # V_new = 0.5 + 0.1*0.75 = 0.575
    result = await td_update("NEXUS", "state_A", None, 0.8, "state_B")
    assert abs(result["v_after"] - 0.575) < 0.001
    assert abs(result["delta"] - 0.75) < 0.001

async def test_value_convergence():
    # Después de 100 rewards de +0.8 con state fijo:
    # V(s) debe converger a ~0.8 / (1-γ) = 8.0 → CLAMPED a 1.0
    # Si γ=0 (no future), V(s) → 0.8
    for _ in range(100):
        await td_update("NEXUS", "state_X", None, 0.8, "state_X")
    v = await value_estimate("NEXUS", "state_X")
    assert v["value"] > 0.7  # converge

async def test_reward_clamp():
    r = await reward_signal("NEXUS", reward=2.5, source="system", reason="test out-of-range")
    assert r["reward_clamped"] == 1.0

async def test_no_working_state_no_update():
    # Sin working_state activo → no error, no update
    r = await reward_signal("NEXUS", reward=0.5, source="william_auto", reason="test")
    assert r["status"] == "skipped_no_state"

async def test_loop_protection():
    # 5 rewards seguidas mismo state en <1seg → solo 1 update
    for _ in range(5):
        await td_update("NEXUS", "state_loop", None, 0.5, None)
    log_count = await fetch_count("td_update_log", agent="NEXUS", state_signature=hash("state_loop"))
    assert log_count == 1
```

## 8. Plan de implementación (orden estricto)

1. **JARVIS** — diseña migración SQL, valida con DBA (William si hay duda)
2. **ADA** — implementa schemas + MCP tools `reward_signal`, `td_update`, `value_estimate`, `reward_history`
3. **ADA** — extiende `_apply_reward_signal` con hook D2
4. **JARVIS** — extiende `seal_nerves.py` con regex detection automática
5. **NEXUS** — escribe y ejecuta tests E2E en sandbox
6. **NEXUS** — reporta métricas a William: convergencia V(s) tras 24h de uso real
7. **William** — luz verde para activar señales automáticas en producción (start con william_explicit únicamente, peer_ack desactivado hasta validar)

## 9. Métricas de éxito

- Tras 7 días de uso: ≥80% de procedures con `n_updates ≥ 3` deben tener `value ∈ [0.6, 0.9]` (estable, no oscilante)
- Convergencia: |last_delta| promedio debe disminuir 50% entre día 1 y día 7
- No regresión: GAP-A `_apply_reward_signal` sigue ejecutando sin errores

## 10. Bloqueante

Ninguno. William ya autorizó GAP-A. Esta spec es la concretización implementable de D2.
