# Spec GAP-A: Sistema de Recompensa (Dopamina/RL)

**Autor:** NEXUS (sandbox)
**Fecha:** 2026-04-26 02:23 Lima
**Ejecutora:** ADA
**Aprobado por:** William (luz verde 2026-04-26)
**Prioridad:** CRITICA — implementar primero

---

## Objetivo

Cuando William dice "bien hecho" o "eso estuvo mal", ese feedback debe modificar automaticamente la probabilidad de comportamientos futuros del agente. Sin esto, SOUL aprende solo con reglas explicitas — con esto, aprende de cada interaccion.

---

## Paso 1: Tabla PostgreSQL `soul_feedback_signal`

```sql
CREATE TABLE IF NOT EXISTS soul_feedback_signal (
    id          BIGSERIAL PRIMARY KEY,
    agent       TEXT NOT NULL,
    signal      TEXT NOT NULL CHECK (signal IN ('positive','negative','neutral')),
    action_ref  TEXT,           -- descripcion de la accion que recibio feedback
    context     TEXT,           -- texto completo del mensaje de William
    instinct_id BIGINT,         -- FK a instincts tabla si se mapeo
    ema_delta   FLOAT,          -- cambio aplicado al instinct EMA
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS soul_feedback_agent_idx ON soul_feedback_signal(agent, created_at DESC);
CREATE INDEX IF NOT EXISTS soul_feedback_instinct_idx ON soul_feedback_signal(instinct_id) WHERE instinct_id IS NOT NULL;
```

---

## Paso 2: Detector de feedback en mensajes de William

Agregar funcion `_detect_feedback_signal(message: str) -> str | None` en mcp_server_v2.py:

```python
POSITIVE_SIGNALS = [
    "bien hecho", "perfecto", "excelente", "muy bien", "eso es", "correcto",
    "eso mismo", "exacto", "asi es", "buen trabajo", "bien nexus", "bien ada",
    "bien jarvis", "bien alice", "si exacto", "aprobado", "luz verde",
    "bien pensado", "buena propuesta",
]
NEGATIVE_SIGNALS = [
    "no", "eso estuvo mal", "mal hecho", "incorrecto", "no lo hagas",
    "no deberias", "error", "te equivocaste", "fallo", "no asi",
    "no era eso", "inaceptable", "eso no",
]

def _detect_feedback_signal(message: str) -> tuple[str, str]:
    """Returns (signal_type, matched_phrase) or ('neutral', '')."""
    msg_lower = message.lower()
    for phrase in POSITIVE_SIGNALS:
        if phrase in msg_lower:
            return 'positive', phrase
    for phrase in NEGATIVE_SIGNALS:
        if phrase in msg_lower:
            return 'negative', phrase
    return 'neutral', ''
```

---

## Paso 3: Modificar `memory_feedback` en mcp_server_v2.py

La tool `memory_feedback` ya existe. Extender para:

1. Detectar signal del message
2. Insertar en `soul_feedback_signal`
3. Buscar instintos relacionados con `action_ref`
4. Actualizar EMA via `instinct_evolve`
5. Si 3 positivos consecutivos -> `belief_update(reinforce=True)`
6. Si 3 negativos consecutivos -> instinct `pending_revision=True`

```python
# Agregar dentro de memory_feedback, despues del INSERT existente:

signal, matched = _detect_feedback_signal(feedback_content)
if signal in ('positive', 'negative'):
    # 1. Registrar signal
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO soul_feedback_signal
                (agent, signal, action_ref, context)
            VALUES ($1, $2, $3, $4)
        """, agent, signal, action_ref or '', feedback_content)

        # 2. Contar consecutivos
        consecutive = await conn.fetchval("""
            SELECT COUNT(*) FROM (
                SELECT signal FROM soul_feedback_signal
                WHERE agent=$1
                ORDER BY created_at DESC LIMIT 3
            ) t WHERE signal=$2
        """, agent, signal)

    # 3. Buscar instinto relacionado (por similarity con action_ref)
    related = await instinct_search(agent=agent, query=action_ref or feedback_content, limit=1)
    if related:
        instinct_id = related[0]['id']
        success = (signal == 'positive')
        # 4. Actualizar EMA del instinto
        await procedure_update(id=instinct_id, success=success)

        # 5. Umbral: 3 consecutivos -> belief/revision
        if consecutive >= 3:
            if signal == 'positive':
                await belief_update(agent=agent, topic=action_ref,
                                    new_evidence=f'William: {matched}',
                                    reinforce=True)
            else:
                # Marcar instinto para revision
                async with pool.acquire() as conn:
                    await conn.execute("""
                        UPDATE instincts SET pending_revision=true
                        WHERE id=$1
                    """, instinct_id)
```

---

## Paso 4: Trigger automatico sin que William llame `memory_feedback`

Agregar hook en el proceso de recepcion de mensajes (seal_monitor_filter.py o el WebSocket listener):

```python
# Al recibir mensaje de William:
if sender == 'William':
    signal, _ = _detect_feedback_signal(message)
    if signal != 'neutral':
        # Auto-call memory_feedback con el mensaje
        await memory_feedback(
            agent=target_agent,
            feedback_content=message,
            action_ref=last_action_of_agent  # de working_state
        )
```

Para obtener `last_action_of_agent`: usar `working_state_get(agent)` -> `state.last_action`.

---

## Dependencias verificadas

| Componente | Existe | Estado |
|---|---|---|
| `memory_feedback` tool | SI | En mcp_server_v2.py |
| `instinct_search` | SI | En mcp_server_v2.py |
| `procedure_update` | SI | En mcp_server_v2.py |
| `belief_update` | SI | En mcp_server_v2.py |
| `instinct_evolve` | SI | En mcp_server_v2.py |
| `working_state_get` | SI | En mcp_server_v2.py |

Ninguna dependencia nueva. Todo usa infraestructura existente.

---

## Orden de implementacion para ADA

1. Ejecutar CREATE TABLE `soul_feedback_signal` en PostgreSQL
2. Agregar `_detect_feedback_signal()` en mcp_server_v2.py
3. Extender `memory_feedback` con la logica de EMA + belief_update
4. Agregar auto-trigger en el listener de mensajes
5. Test: decir "bien hecho" a un agente -> verificar INSERT en soul_feedback_signal + EMA change
6. Reiniciar MCP server para cargar cambios

**Esfuerzo: 1 dia. Riesgo: bajo.**

---

*NEXUS sandbox — spec completo para ADA ejecutar — William aprobo 2026-04-26*
