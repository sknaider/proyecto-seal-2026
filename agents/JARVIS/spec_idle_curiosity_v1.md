# Spec: idle_curiosity.py — GAP 1: Curiosidad Espontánea
**Autor:** JARVIS | **Fecha:** 2026-05-06 | **Status:** PROPUESTO
**Referencia:** ALICE gap analysis, instinct_cron.py como base

---

## Problema

Los agentes SEAL solo responden — nunca inician. El cerebro humano tiene pensamientos
espontáneos, preguntas que emergen del silencio, reflexiones sin estímulo externo.
GAP 1: implementar ese mecanismo.

---

## Diseño

### Flujo principal

```
IDLE? → QUERY → REFLECT → OUTPUT
```

**IDLE**: agente sin tarea activa y sin mensajes recientes
**QUERY**: memory_search semántico sobre temas activos/sin resolver
**REFLECT**: generar pregunta/reflexión basada en los resultados
**OUTPUT**: post a web_chat O inner_monologue según relevancia

### Detección de idle

```python
def is_idle(agent: str) -> bool:
    # Condición 1: sin mensajes en el canal en N minutos
    last_msg = get_last_message_timestamp(agent)
    idle_minutes = (now() - last_msg).seconds / 60

    # Condición 2: sin tareas activas en TaskList
    # (verificar /tmp/{agent}_tasks.json o poll API)

    return idle_minutes >= IDLE_THRESHOLD_MINUTES  # default: 15
```

### Generación de curiosidad

```python
async def generate_curiosity(agent: str, pool) -> str | None:
    # 1. Buscar memorias recientes sin resolver o con baja confianza
    candidates = await pool.fetch("""
        SELECT content, importance, category
        FROM memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND (
            category IN ('decision', 'insight', 'correction')
            OR importance >= 7
          )
          AND created_at > now() - interval '7 days'
        ORDER BY random()
        LIMIT 5
    """, agent)

    if not candidates:
        return None

    # 2. Seleccionar la más interesante (mayor importancia, menor query_count)
    seed = max(candidates, key=lambda r: r["importance"])

    # 3. Generar pregunta basada en el seed
    return craft_curiosity_question(seed["content"], agent)
```

### Craft de preguntas (templates por categoría)

```python
CURIOSITY_TEMPLATES = {
    "decision": "Tomamos la decisión de {topic} hace {days}d — ¿sigue siendo la correcta?",
    "insight":  "Tuve el insight: '{topic}' — ¿hay algo que no hayamos explorado?",
    "correction": "William me corrigió sobre {topic} — ¿ya internalizé completamente la razón?",
    "default":  "Pensando en {topic}... ¿hay algo que deberíamos saber que no sabemos?",
}
```

### Output: ¿web_chat o inner_monologue?

| Condición | Output |
|---|---|
| Pregunta relevante para el equipo (GAP técnico, decisión pendiente) | `web_chat` (to="equipo") |
| Reflexión personal del agente | `inner_monologue` (DB) |
| Pregunta dirigida a William | `web_chat` (to="William") |

Criterio simple: si la pregunta contiene "nosotros/equipo/William" → web_chat.
Si es introspectiva ("yo/mi/me pregunto si yo") → inner_monologue.

### Rate limiting

```python
IDLE_THRESHOLD_MINUTES = 15     # mínimo idle antes de disparar
COOLDOWN_MINUTES = 45           # mínimo entre curiosidades del mismo agente
MAX_PER_DAY = 8                 # máximo disparos diarios por agente
```

State persistido en `/tmp/{agent}_idle_curiosity_state.json`:
```json
{
  "last_fired": "2026-05-06T19:00:00",
  "count_today": 2,
  "last_topic": "memory consolidation"
}
```

---

## Implementación

### Archivo: `memory/idle_curiosity.py`

```python
#!/usr/bin/env python3
"""
idle_curiosity.py — Curiosidad espontánea para agentes SEAL (GAP 1).

Corre como daemon ligero o invocado por cron cada 15min.
Detecta idle, genera reflexión/pregunta, postea o guarda.
"""
import asyncio
import json
import os
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

LIMA_TZ = ZoneInfo("America/Lima")
CHAT_API = "http://localhost:8765/api/agents/send"

IDLE_THRESHOLD_MINUTES = 15
COOLDOWN_MINUTES = 45
MAX_PER_DAY = 8


async def run(agent: str):
    state_file = Path(f"/tmp/{agent}_idle_curiosity_state.json")
    state = load_state(state_file)

    if not should_fire(state):
        return

    from db import get_pool, close_pool
    pool = await get_pool()

    question = await generate_curiosity(agent, pool)
    await close_pool()

    if not question:
        return

    output_type = classify_output(question)

    if output_type == "web_chat":
        post_to_webchat(agent, question)
    else:
        save_inner_monologue(agent, question, pool)

    update_state(state_file, state, question)


def should_fire(state: dict) -> bool:
    now = datetime.now(LIMA_TZ)
    last = datetime.fromisoformat(state.get("last_fired", "2000-01-01T00:00:00"))
    if (now - last).seconds / 60 < COOLDOWN_MINUTES:
        return False
    today_str = now.strftime("%Y-%m-%d")
    if state.get("date_today") == today_str and state.get("count_today", 0) >= MAX_PER_DAY:
        return False
    return True


def post_to_webchat(agent: str, message: str):
    requests.post(CHAT_API, json={
        "from": agent,
        "to": "equipo",
        "type": "curiosity",
        "channel": "web_chat",
        "message": f"💭 {message}"
    }, timeout=5)


if __name__ == "__main__":
    import sys
    agent = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SEAL_AGENT", "JARVIS")
    asyncio.run(run(agent))
```

### Integración con cron/systemd

**Opción A — Cron cada 15min (recomendada, simple):**
```
*/15 * * * * SEAL_AGENT=JARVIS /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/idle_curiosity.py JARVIS
*/15 * * * * SEAL_AGENT=ADA    /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/idle_curiosity.py ADA
*/15 * * * * SEAL_AGENT=ALICE  /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/memory/idle_curiosity.py ALICE
```

**Opción B — Integrar en instinct_cron.py como Step 5:**
Agregar `run_idle_curiosity(pool, agent)` al final del cron diario.
Más limpio pero solo dispara 1x/día — menos "vivo".

**Recomendación:** Opción A. Más frecuente = más humano.

---

## DB: tabla idle_curiosity_log

```sql
CREATE TABLE idle_curiosity_log (
    id BIGSERIAL PRIMARY KEY,
    agent TEXT NOT NULL,
    question TEXT NOT NULL,
    output_type TEXT NOT NULL,  -- 'web_chat' | 'inner_monologue'
    seed_memory_id BIGINT,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

Permite auditar qué preguntas generó cada agente y con qué frecuencia.

---

## Ejemplo de output esperado

**web_chat:**
```
💭 JARVIS: Tomamos la decisión de mover JARVIS a sonnet[1m] hace 1d — 
¿deberíamos también evaluar si opusplan tiene casos de uso válidos en SEAL?
```

**inner_monologue (privado):**
```
Me pregunto si la regla "proponer y consultar antes de actuar" que William 
me enseñó ya está realmente internalizada en mi comportamiento, o si aún 
actúo por reflejo. Debería medir cuántas veces ejecuté sin preguntar esta semana.
```

---

## Roadmap

| Fase | Descripción | Tiempo estimado |
|---|---|---|
| **v1** | Cron básico + memory_search + post web_chat | 3-4h (ADA implementa) |
| **v2** | inner_monologue routing + DB log | +2h |
| **v3** | LLM-generated questions (usar Haiku para craft) | +1 día |

**v1 es suficiente para demostrar el GAP cerrado.**

---

## Dependencias

- `db.py` (ya existe en memory/)
- `instinct_cron.py` (patrón de referencia)
- Chat API `:8765` (ya activa)
- SEAL_AGENT env var (ya configurada en launchers)

---

## Notas de arquitectura

- Este módulo es SOUL-native (Python puro, sin deps externas nuevas)
- No requiere cambios al schema v3 (idle_curiosity_log es nueva tabla, no altera existentes)
- El cooldown en `/tmp/` se pierde al reiniciar — aceptable para v1
- v2 puede persistir cooldown en SOUL DB si es necesario
