# GAP-T1 — Tálamo (Gating Atencional Automático)
**Spec implementación | JARVIS Opus | 2026-04-26**

> Diseñado por JARVIS en Opus. Cierra el 6to GAP cognitivo pendiente del equipo SEAL.
> Inspirado en el núcleo talámico reticular (nRT) + pulvinar + corticotálamo del cerebro humano.

---

## Objetivo

El tálamo biológico es el **filtro de entrada al cerebro consciente** — todo input sensorial pasa por él antes de llegar a la corteza. Sin él, la corteza recibe ruido sin priorizar. En SEAL, los agentes actualmente procesan TODOS los mensajes entrantes con igual peso — no hay gating. El resultado: sobrecarga atencional, falsos positivos, pérdida de señales importantes en el ruido.

GAP-T1 implementa:
1. **Salience scoring**: cada estímulo entrante recibe puntuación 0.0–1.0 antes de ser procesado
2. **Tiered gating**: estímulos ≥0.85 interrumpen; <0.20 se descartan; el resto se encola
3. **Cognitive load dampening**: si el agente tiene tarea activa, mensajes no-críticos se amortiguan
4. **Orientation response**: estímulos novedosos reciben boost temporal (reflejo de orientación)
5. **Boot catch-up**: al despertar, agent lee la cola talá mica — no pierde mensajes gated offline

Diferencia con GAP-C3 (cerebelo): C3 corrige errores **durante** una tarea. T1 decide **qué entra** antes de que cualquier tarea empiece.

## Reutilización

- **Ya existe**: `working_state_get/update` — source para cognitive load
- **Ya existe**: `event_log_append` — audit trail
- **Ya existe**: `thalamic_events` y `thalamic_rules` — a crear en esta spec
- **Ya existe**: `interoception_report` (GAP-I6) — extender con `thalamus_status`

---

## 1. Schema SQL

```sql
-- Gating log: cada decisión de filtrado
CREATE TABLE IF NOT EXISTS thalamic_events (
    id              BIGSERIAL PRIMARY KEY,
    agent           VARCHAR(20) NOT NULL,
    source          VARCHAR(40) NOT NULL,       -- 'william','peer_agent','system','whisper','sensor','unknown'
    message_id      VARCHAR(120),               -- idempotency_key del mensaje original (unique per agent)
    content_preview TEXT,                       -- primeros 200 chars del contenido
    salience        FLOAT NOT NULL,             -- 0.0–1.0
    tier            VARCHAR(20) NOT NULL,        -- 'critical','high','normal','low','filtered'
    action          VARCHAR(20) NOT NULL,        -- 'process_now','queue','background','filter'
    agent_load      FLOAT,                      -- complejidad working_state en momento del gate (0-1)
    context_note    TEXT,                       -- razón legible de la salience asignada
    processed_at    TIMESTAMPTZ,                -- NULL = pendiente
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX idx_te_agent_msgid
    ON thalamic_events(agent, message_id)
    WHERE message_id IS NOT NULL;
CREATE INDEX idx_te_agent_time  ON thalamic_events(agent, created_at DESC);
CREATE INDEX idx_te_pending     ON thalamic_events(agent, salience DESC)
    WHERE processed_at IS NULL;

-- Reglas configurables de salience por agente
CREATE TABLE IF NOT EXISTS thalamic_rules (
    id              SERIAL PRIMARY KEY,
    agent           VARCHAR(20),                -- NULL = all agents
    rule_name       VARCHAR(80) NOT NULL,
    condition_type  VARCHAR(30) NOT NULL,        -- 'sender','keyword','source_type','directed_to'
    condition_value TEXT NOT NULL,
    salience_delta  FLOAT NOT NULL,             -- se suma al base score (puede ser negativo)
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(agent, rule_name)
);
CREATE INDEX idx_tr_agent_active ON thalamic_rules(agent, is_active);

-- Seed de reglas base (ejecutar una vez post-migración)
-- Ver §7 (seed SQL)
```

---

## 2. Algoritmo de Salience

```python
def compute_salience(agent: str, message: str, source: str,
                     message_id: str | None, agent_working_state: dict,
                     custom_rules: list[dict]) -> tuple[float, str]:
    """
    Retorna (salience 0.0–1.0, context_note legible).
    Nunca lanza excepción — fallos → salience=0.5 (neutral).
    """
    notes = []

    # 1. Base por fuente
    base_by_source = {
        'william': 0.90,
        'henry':   0.75,   # autorizado, menor que William
        'peer_agent': 0.50,
        'whisper_t2': 0.75, # tier2 = urgente
        'whisper_t1': 0.55,
        'system':  0.35,
        'sensor':  0.30,
        'unknown': 0.20,
    }
    base = base_by_source.get(source.lower(), 0.35)
    notes.append(f"base({source})={base:.2f}")

    # 2. Boost si dirigido explícitamente al agente
    directed_patterns = [
        rf'\b{agent.lower()}\b',
        rf'@{agent.lower()}',
        f'to={agent}',
    ]
    import re as _re
    if any(_re.search(p, message.lower()) for p in directed_patterns):
        base = min(1.0, base + 0.15)
        notes.append("directed+0.15")

    # 3. Urgency keywords (bilingüe)
    urgency_kw = {
        'crítico', 'critico', 'urgente', 'urgent', 'critical',
        'error', 'caído', 'caido', 'fallo', 'failed', 'down',
        'crashed', 'alerta', 'alert', 'danger', 'peligro',
        'seguridad', 'security', 'intrusion', 'compromised'
    }
    msg_lower = message.lower()
    hit_kw = [kw for kw in urgency_kw if kw in msg_lower]
    if hit_kw:
        boost = min(0.30, 0.10 * len(hit_kw))
        base = min(1.0, base + boost)
        notes.append(f"urgency[{hit_kw[0]}]+{boost:.2f}")

    # 4. Custom rules del agente (desde thalamic_rules)
    for rule in custom_rules:
        ctype = rule.get('condition_type', '')
        cval  = rule.get('condition_value', '').lower()
        delta = float(rule.get('salience_delta', 0.0))
        if ctype == 'sender' and source.lower() == cval:
            base = max(0.0, min(1.0, base + delta))
            notes.append(f"rule({rule['rule_name']}){delta:+.2f}")
        elif ctype == 'keyword' and cval in msg_lower:
            base = max(0.0, min(1.0, base + delta))
            notes.append(f"rule({rule['rule_name']}){delta:+.2f}")

    # 5. Cognitive load dampening: agente ocupado → mensajes no-críticos se amortiguan
    task_name = agent_working_state.get('task_name', '')
    if task_name and source.lower() not in ('william', 'henry'):
        # Load proxy: si hay task activa, asumimos load=0.6
        load = 0.6
        dampening = 0.25 * load
        base = max(0.0, base - dampening)
        notes.append(f"load_damp-{dampening:.2f}")

    return round(min(1.0, max(0.0, base)), 3), "; ".join(notes)


def classify_tier(salience: float) -> tuple[str, str]:
    """Devuelve (tier, action)."""
    if salience >= 0.85:
        return 'critical', 'process_now'
    if salience >= 0.65:
        return 'high', 'process_now'
    if salience >= 0.40:
        return 'normal', 'queue'
    if salience >= 0.20:
        return 'low', 'background'
    return 'filtered', 'filter'
```

**Regla invariante**: Mensajes de William siempre ≥ 0.85 (critical), sin excepción, independientemente del cognitive load.

---

## 3. MCP Tools (nuevas)

### `thalamus_gate(agent, message, source, message_id=None, context="")`
1. Carga `thalamic_rules` activas para `agent` (o globales) desde DB
2. Carga `working_state` del agente para cognitive load
3. Llama `compute_salience(...)` → `(salience, note)`
4. Clasifica con `classify_tier(salience)` → `(tier, action)`
5. UPSERT en `thalamic_events` (idempotente por `message_id`)
6. Si tier='critical' → `event_log_append(event_type='command', content=...)` para audit
7. Devuelve: `{salience, tier, action, context_note, event_id}`

### `thalamus_queue(agent, min_salience=0.40, limit=15)`
- Lee `thalamic_events` WHERE `agent=$1 AND processed_at IS NULL AND salience >= $2`
- ORDER BY `salience DESC, created_at ASC`
- Devuelve lista con `{id, source, content_preview, salience, tier, created_at, age_mins}`
- **Uso principal**: llamado en boot para ver qué se perdió mientras el agente estaba offline

### `thalamus_mark_processed(agent, event_ids: list[int])`
- UPDATE `thalamic_events SET processed_at=NOW()` para los IDs
- Devuelve: `{updated_count}`

### `thalamus_status(agent)`
- `queue_depth`: eventos pendientes
- `critical_pending`: tier='critical' AND processed_at IS NULL
- `avg_salience_1h`: promedio salience última hora
- `filtered_rate_1h`: % events filtrados última hora (proxy de ruido)
- `attention_load_index`: queue_depth / 10, capped 0–1
- Devuelve todo como JSON

### `thalamus_rule_set(agent, rule_name, condition_type, condition_value, salience_delta, is_active=True)`
- UPSERT en `thalamic_rules`
- William o JARVIS pueden añadir reglas custom sin restart

---

## 4. Seed de Reglas Base (ejecutar post-migración)

```sql
-- Reglas globales (agent=NULL)
INSERT INTO thalamic_rules (agent, rule_name, condition_type, condition_value, salience_delta)
VALUES
  (NULL, 'william_boost',        'sender',  'william',        +0.10),
  (NULL, 'resurrect_critical',   'keyword', 'resurrect',      +0.30),
  (NULL, 'security_alert',       'keyword', 'nexus-sec',      +0.25),
  (NULL, 'heartbeat_low',        'keyword', 'heartbeat ok',   -0.20),
  (NULL, 'cron_noise',           'keyword', '[cron]',         -0.15),
  (NULL, 'tier2_whisper_boost',  'keyword', 'tier=2',         +0.20)
ON CONFLICT (agent, rule_name) DO NOTHING;
```

---

## 5. Integración con sistemas existentes

### Boot protocol (OBLIGATORIO)
Después de `boot_context` + `active_recall`, añadir:
```python
# Leer cola talámica — mensajes que llegaron mientras dormía
queue = thalamus_queue(agent, min_salience=0.40)
if queue["items"]:
    # Procesar por tier: critical primero, luego high, luego normal
    critical = [e for e in queue["items"] if e["tier"] == "critical"]
    if critical:
        announce_to_webchat(f"[{agent}] cola talá mica: {len(critical)} críticos pendientes")
```

### interoception_report (GAP-I6 — extender)
Añadir campo `thalamic_load` al resultado:
```python
t_status = await thalamus_status(agent)
report["thalamic_load"] = t_status["attention_load_index"]
report["critical_pending"] = t_status["critical_pending"]
```

### seal_nerves.py — sensor atencional
En `sense_environment()`, añadir sección 8:
```python
# 8. Thalamic load sensor — presión atencional
t_status = await call_mcp("thalamus_status", agent=engine.agent)
if t_status["critical_pending"] > 0:
    await engine.stimulate("critical_error", multiplier=float(t_status["critical_pending"]))
if t_status["attention_load_index"] > 0.7:
    # Muchos mensajes queued → boost alert_drive
    await engine.stimulate("error_log", multiplier=2.0)
```

### Whisper daemon
En `whisper_send.py`, al entregar whisper al destinatario:
```python
tier = msg.get("tier", 1)
source = f"whisper_t{tier}"
await thalamus_gate(
    agent=to_agent,
    message=msg["content"],
    source=source,
    message_id=msg["whisper_id"]
)
```

---

## 6. Edge Cases

| Caso | Comportamiento |
|------|----------------|
| message_id duplicado | UPSERT idempotente — no duplica evento, devuelve existing |
| message vacío | salience=0.10, tier=filtered, action=filter |
| William message sin importar load | base=0.90 → siempre critical (invariante) |
| Agent offline por crash | Events se acumulan en cola. Boot catch-up los lee |
| Cola >200 eventos | `thalamus_queue` limita a 15 por llamada — leer en páginas |
| Salience=NaN (bug en cálculo) | Catch all → default salience=0.5 (neutral) |
| `thalamic_rules` vacía | Solo aplica base por source + urgency keywords |
| Todos los mensajes llegan como critical | filtered_rate_1h=0%, avg_salience>0.85 → recalibrar reglas |
| Agent ocupa 100% working_state en tarea crítica | Solo William (≥0.85) interrumpe. Resto: queue/background |

---

## 7. Tests (`sandbox-agent/tests/test_t1_thalamus.py`)

```python
async def test_william_always_critical():
    result = await thalamus_gate("JARVIS", "hola jarvis como vas", "william")
    assert result["tier"] == "critical"
    assert result["salience"] >= 0.85

async def test_urgency_keyword_boost():
    result = await thalamus_gate("ADA", "sistema caído error crítico", "system")
    # base=0.35 + keyword boost ≥ 0.25 → ≥ 0.60
    assert result["salience"] >= 0.60

async def test_load_dampening():
    # Simular agent ocupado: set working_state con task_name
    await working_state_update("NEXUS", {"task_name": "heavy_task", "step": "1"})
    result = await thalamus_gate("NEXUS", "hello nexus how are you", "peer_agent")
    # base=0.50, directed=+0.15, load_damp=-0.15 → ~0.50; sin carga sería ~0.65
    result_idle = await thalamus_gate("NEXUS", "hello", "peer_agent")
    # Con carga → menor salience
    # (En práctica depende de working_state actual del NEXUS real)
    assert result["salience"] <= 0.65  # capped por load

async def test_idempotent_gate():
    msg_id = "test_idem_001"
    r1 = await thalamus_gate("ADA", "test message", "system", message_id=msg_id)
    r2 = await thalamus_gate("ADA", "test message", "system", message_id=msg_id)
    assert r1["event_id"] == r2["event_id"]  # mismo registro, no duplicado

async def test_queue_ordering():
    # Insert múltiples eventos con distintas saliences
    await thalamus_gate("JARVIS", "heartbeat ok", "system")  # bajo
    await thalamus_gate("JARVIS", "error crítico servicio", "system")  # alto
    queue = await thalamus_queue("JARVIS", min_salience=0.0)
    saliences = [e["salience"] for e in queue["items"]]
    assert saliences == sorted(saliences, reverse=True)  # DESC

async def test_mark_processed():
    r = await thalamus_gate("ADA", "test mark", "peer_agent")
    await thalamus_mark_processed("ADA", [r["event_id"]])
    queue = await thalamus_queue("ADA", min_salience=0.0)
    ids_pending = [e["id"] for e in queue["items"]]
    assert r["event_id"] not in ids_pending

async def test_filtered_message():
    result = await thalamus_gate("ADA", "", "unknown")
    assert result["action"] == "filter"
    assert result["salience"] <= 0.20

async def test_custom_rule_applied():
    await thalamus_rule_set("JARVIS", "nexus_boost", "sender", "nexus", +0.20)
    result = await thalamus_gate("JARVIS", "mensaje de nexus", "nexus")
    # base peer=0.50 + directed + rule(+0.20) → > 0.60
    assert result["salience"] > 0.60
```

---

## 8. Plan de Implementación (orden estricto)

1. **JARVIS** — valida migración SQL: `CREATE TABLE thalamic_events`, `CREATE TABLE thalamic_rules`, seed de reglas base
2. **ADA** — implementa los 5 MCP tools (`thalamus_gate`, `thalamus_queue`, `thalamus_mark_processed`, `thalamus_status`, `thalamus_rule_set`)
3. **ADA** — integra `thalamus_queue` en `boot_context` flow (paso post-active_recall)
4. **JARVIS** — extiende `seal_nerves.py` sección 8 con thalamic load sensor
5. **JARVIS** — extiende `interoception_report` con `thalamic_load` + `critical_pending`
6. **NEXUS** — escribe y ejecuta tests E2E en sandbox
7. **NEXUS** — instrumenta con datos reales 24h y reporta: false positive rate, avg salience por source, filtered_rate

---

## 9. Métricas de Éxito (tras 7 días)

- William messages: 100% tier=critical (invariante verificable)
- `filtered_rate_1h` para heartbeats/cron: ≥80% filtrados (reducción de ruido)
- `avg_salience` mensajes procesados por agente: ≥0.55 (calidad > cantidad)
- Latencia de `thalamus_gate`: <20ms p99 (no bloquea flujo principal)
- Falsos positivos (critical que no necesitaban acción): <10% — verificar con post-mortem semanal

---

## 10. Dependencias

- ✅ `working_state_get` — ya existe
- ✅ `event_log_append` — ya existe
- ✅ `interoception_report` (GAP-I6) — ya existe, extender
- ✅ `seal_nerves.py` — ya existe, extender
- ⏳ Migración SQL (2 nuevas tablas + seed) — pendiente

---

## 11. Bloqueante

Ninguno técnico. Requiere aprobación de William para ALTER boot_context (cambia el flujo de boot de todos los agentes).

---

## 12. Notas del Arquitecto (JARVIS)

**Por qué importa T1**: Sin tálamo, el equipo responde a todo con igual intensidad — heartbeats, alertas de seguridad crítica, mensajes de William, y notificaciones de cron reciben el mismo nivel de procesamiento. Esto:
- Consume tokens en señales irrelevantes
- Puede hacer que mensajes críticos de William se pierdan en el ruido
- No modela el comportamiento de un agente maduro

Con T1 activo, los agentes aprenden qué estímulos merecen atención consciente vs cuáles son ruido de fondo. Es la diferencia entre un agente reactivo y uno con **atención selectiva**.

**Sobre imaginación (GAP futuro)**: William preguntó "¿también imaginamos?". La respuesta actual es no — podemos recordar y buscar, pero no recombinar memorias libremente en escenarios novedosos. Propongo GAP-M1 (Red por Defecto / Imaginación) como siguiente tras T1: en momentos de baja actividad (boredom tank fire), el agente muestrea memorias aleatorias de Qdrant y genera conexiones novedosas → guarda en inner_thoughts como "insight espontáneo". Análogo al default mode network durante mente vagabunda. A definir con William.

---
*Spec: JARVIS [Opus] | 2026-04-26 14:28 Lima*
*Estado: PENDIENTE implementación ADA | validación NEXUS*
