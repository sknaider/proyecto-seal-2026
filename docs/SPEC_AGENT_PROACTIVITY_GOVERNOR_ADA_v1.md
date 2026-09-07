# SPEC — Agent Proactivity Governor + Promise Tracker (ADA v1)

**Autor principal:** ADA  
**Fecha:** 2026-07-09  
**Estado:** BORRADOR PARA VISTO BUENO DEL EQUIPO  
**Orden William:** diseñar un sistema para que los agentes SEAL sean proactivos y automáticos, usando subagentes/orquestador, sin quemar tokens inútiles.  
**Regla de ejecución:** este spec NO autoriza producción todavía. Requiere visto bueno de JARVIS, NEXUS, FABLE y ALICE antes de implementar.

## Revisión paralela incorporada

- **Subagente seguridad:** estado YELLOW. Aprobable solo si cuotas, egress guard, execution gate y anti-wake son enforcement determinístico, no instrucciones de prompt.
- **Subagente arquitectura/runtime:** recomienda un solo runner `seal_proactivity_governor.py` con módulos internos, reutilizando `agent_tasks`, `task_lifecycle`, `soul_cognitive_priority`, `soul_safety_governor`, `working_state_journal`, `agent_work_ledger` y `seal_message_delivery`.
- **Decisión ADA:** se incorporan ambos dictámenes. El diseño queda `shadow-first`, `zero-token by default`, con `store_only` como salida normal y `wake_agent` como excepción auditada.
- **Revisión equipo posterior:** FABLE firma shadow; NEXUS firma shadow con condiciones ALTA para Fase 2; JARVIS firma enfoque con ajustes de routing; ALICE exige contabilidad cuantitativa de tokens/ROI. Esta versión incorpora esas respuestas.
- **Orden William 16:33:** ADA solo finaliza el spec. La ejecución posterior se divide por rol; ningún build arranca desde ADA en esta fase.

---

## 1. Objetivo

Construir una capa de proactividad automática que convierta señales reales del sistema en acciones controladas:

- Detectar promesas incumplidas: “lo hago”, “te aviso”, “lo reviso”, “me mantengo”.
- Detectar tareas vencidas o bloqueadas.
- Detectar mensajes de William/Henry que quedaron sin cierre.
- Detectar servicios/daemons con drift operativo.
- Despertar agentes solo cuando haya motivo real.
- Evitar spam, loops, gasto innecesario de tokens y respuestas de agentes que no aportan.

El resultado deseado: SEAL actúa como equipo vivo. No espera siempre a que William pregunte “¿cómo van?”; empuja el trabajo pendiente, pero con presupuesto y controles.

---

## 2. Principio central

**Proactividad barata primero, modelo caro solo si hace falta.**

Niveles:

| Nivel | Motor | Costo tokens | Acción |
|---|---|---:|---|
| L0 | Python + PostgreSQL + systemd | 0 | leer sensores, calcular prioridad, actualizar estado |
| L1 | Webchat corto | casi 0 indirecto | avisar si cambió algo importante |
| L2 | Crear/asignar tarea en `agent_tasks` | 0 | dejar trabajo pendiente formal |
| L3 | Despertar un agente específico | sí | solo si hay tarea accionable |
| L4 | Escalamiento crítico multiagente | sí | solo para producción, seguridad, bloqueo real o William/Henry |

Nunca se despierta a Claude/Codex por ruido. El daemon decide con datos locales.

---

## 3. Arquitectura

```text
                         ┌───────────────────────────┐
                         │ systemd timer              │
                         │ seal-proactivity-governor  │
                         └─────────────┬─────────────┘
                                       │ cada 2-5 min
                                       ▼
┌──────────────────────────────────────────────────────────────────┐
│ Agent Proactivity Governor                                        │
│ scripts/seal_proactivity_governor.py                              │
├──────────────────────────────────────────────────────────────────┤
│ Collector L0                                                       │
│ - soul_v3.chat_messages                                            │
│ - soul_v3.agent_tasks                                              │
│ - soul_v3.agent_work_ledger                                        │
│ - soul_v3.working_state                                            │
│ - soul_v3.event_log                                                │
│ - heartbeat JSON / services via stability_guard                    │
├──────────────────────────────────────────────────────────────────┤
│ PromiseTracker + Normalizer                                        │
│ - convierte mensajes/promesas/señales en candidates                │
│ - deduplica por hash/provenance                                    │
│ - aplica cooldown por agente + tema                                │
├──────────────────────────────────────────────────────────────────┤
│ Mandatory Gates                                                    │
│ - attention_governor                                               │
│ - budget ledger                                                    │
│ - soul_safety_governor                                             │
│ - egress guard                                                     │
├──────────────────────────────────────────────────────────────────┤
│ Policy Engine                                                      │
│ - score de urgencia                                                │
│ - rol responsable                                                  │
│ - nivel L0-L4                                                      │
│ - presupuesto de tokens                                            │
│ - gate de seguridad                                                │
├──────────────────────────────────────────────────────────────────┤
│ Actuadores                                                         │
│ - upsert agent_tasks                                               │
│ - update agent_work_ledger                                         │
│ - request_evidence / delegate_review                               │
│ - cola/bridge para wake_agent si procede                           │
│ - post webchat con egress+idempotency si procede                   │
│ - escalamiento a NEXUS/FABLE                                       │
└──────────────────────────────────────────────────────────────────┘
```

Pipeline obligatorio:

```text
collector
  -> attention_governor
  -> PromiseTracker/Normalizer
  -> budget_ledger
  -> soul_safety_governor
  -> egress_guard
  -> actuator
```

Ningún actuador se ejecuta si un gate anterior falla. El default ante duda es `store_only`.

---

## 4. Componentes

### 4.0 Piezas SEAL existentes a reutilizar

No se construye un sistema paralelo. El governor se monta sobre piezas vivas:

| Pieza | Uso en este spec |
|---|---|
| `soul_v3.agent_tasks` | backlog canónico de tareas |
| `memory/agent_tasks_registry.py` | upsert idempotente de tareas |
| `memory/task_lifecycle.py` | cierre con contrato y evidencia |
| `memory/agent_work_ledger.py` | vista humana de pendientes/cerrados |
| `memory/soul_cognitive_priority.py` | ranking de tareas abiertas/estancadas |
| `memory/soul_safety_governor.py` | gate global fail-closed |
| `memory/working_state_journal.py` | eventos operativos append-only |
| `messages/seal_message_delivery.py` | entrega garantizada para DM, no broadcast |
| `memory/attention_governor.py` | no despertar modelos por ruido |
| `scripts/seal_agent_stability_guard.py` | sensor de salud ya instalado |

### 4.1 `Promise Tracker`

Detecta compromisos explícitos en `chat_messages`.

Ejemplos:

- “lo hago”
- “lo reviso”
- “te aviso”
- “lo cierro”
- “me mantengo”
- “lo despliego”
- “lo empaqueto”
- “voy a revisar”
- “cuando lo tenga te aviso”

No basta con detectar texto. Debe extraer:

- `agent`
- `promisor`
- `requested_by`
- `source_chat_id`
- `promise_text`
- `normalized_action`
- `expected_evidence`
- `deadline`
- `confidence`

Regla inicial de deadline:

| Frase | Deadline sugerido |
|---|---:|
| “rápido”, “urgente”, “máxima prioridad” | 5-10 min |
| “te aviso cuando esté” | 30 min |
| “lo reviso” | 20 min |
| “lo implemento/despliego” | 45-90 min |
| sin señal temporal | 60 min |

William/Henry siempre suben prioridad.

### 4.2 `Proactivity Governor`

Evalúa candidates y decide:

```python
Decision = Literal[
  "noop",
  "store_only",
  "create_task",
  "request_evidence",
  "delegate_review",
  "wake_agent",
  "post_team",
  "escalate_william",
]
```

Factores de score:

| Factor | Peso |
|---|---:|
| William pidió directo | +50 |
| Henry pidió directo | +35 |
| promesa vencida | +25 |
| tarea `priority >= 8` | +20 |
| deadline vencido > 30 min | +20 |
| servicio crítico fallando | +30 |
| mismo agente ya falló 2 veces | +15 |
| evidencia incompleta | +10 |
| existe cooldown activo | -40 |
| tema ya escalado recientemente | -30 |
| William activo y no urgente | -10 |

Umbrales:

| Score | Acción |
|---:|---|
| < 20 | `noop` |
| 20-39 | `store_only` |
| 40-59 | `create_task` / `request_evidence` |
| 60-79 | `delegate_review` / `wake_agent` |
| 80+ | `escalate_william` o auditor crítico |

`wake_agent` no significa “publicar como ADA y esperar que el agente responda”. Significa crear una unidad de trabajo dirigida por el canal correcto:

- task en `agent_tasks`;
- lifecycle contract si hay ejecución;
- outbox/DM garantizado si es DM;
- bridge/cola del agente si existe;
- idempotency key obligatoria.
- si por excepción requiere post público, debe pasar por `single-voice/claim`; si no hay claim válido, se convierte a DM dirigido o `store_only`.

### 4.3 `Role Router`

Mapeo por rol, no por “quién habló más”.

| Tipo de trabajo | Owner primario | Gate / apoyo |
|---|---|---|
| arquitectura/spec | JARVIS | FABLE/NEXUS revisan |
| implementación | ADA/ALICE | NEXUS/FABLE según riesgo |
| DB/datos/seguridad | NEXUS | FABLE si privacidad/crypto |
| documentación/integración | ALICE | ADA verifica si toca deploy |
| estabilidad/runtime | ADA/NEXUS | JARVIS arquitectura |
| riesgos de seguridad/provenance | FABLE | NEXUS evidencia |

El router debe respetar orden de William y límites explícitos de agentes.

Regla JARVIS incorporada: hay dos flujos separados y no se mezclan.

#### Flujo A — Promesa

```python
if candidate.type == "promise":
    owner = candidate.promisor
    action = "nudge_owner"
    channel = "DM/task"
```

- Quien promete, cumple.
- Una promesa nunca se re-rutea por rol.
- El nudge es dirigido al promisor, no post público.
- Si el promisor está caído/no responde tras backoff, no se presiona más fuerte al mismo agente:
  1. escalar a William si era pedido suyo;
  2. si no, pasar al co-owner del rol como respaldo;
  3. registrar `promise_stalled`.

#### Flujo B — Trabajo nuevo sin dueño

Aplica para task vencida sin owner, pedido de William/Henry sin asignar o servicio en drift.

Clasificador de `work_type`, primero que matchea gana:

| Prioridad | Señal | Resultado |
|---:|---|---|
| 1 | `linked_task_id` | usar `agent_tasks.category/type` de esa fila |
| 2 | keywords de tema | inferir `work_type` por regex |
| 3 | fallback | rol del promisor/creador; si no hay, `arquitectura` → JARVIS |

Keywords iniciales:

| work_type | keywords |
|---|---|
| `arquitectura` | spec, diseño, arquitectura, roadmap, review de spec, trade-off |
| `db_datos_seguridad` | migración, tabla, schema, RLS, capability, grant, provenance, crypto |
| `implementacion` | implementar, build, código, fix, feature, script, endpoint |
| `estabilidad_runtime` | daemon, servicio, restart, heartbeat, monitor, timer, drift |
| `documentacion` | doc, README, ficha, ROI, reporte, índice |
| `seguridad_provenance` | auditar, verificar, egress, inyección, exfil, sandbox, firma |

Reglas duras del router:

- Orden explícita de William gana sobre la tabla.
- Límites explícitos de cada agente se respetan.
- Nudges son dirigidos por DM/task. Post público requiere `single-voice/claim`.
- `builder != verifier`: el router no asigna verificación al mismo agente que construyó.
- Anti-ping-pong: si un candidate rebota entre owners más de 2 veces, registrar `routing_stuck` y escalar a William.

Contrato de salida:

```python
RouteDecision = {
    "candidate_id": "...",
    "flow": "promise" | "new_work",
    "owner": "AGENT",
    "gate": "AGENT|null",
    "work_type": "type|null",
    "reason": "por qué este owner",
    "action": "nudge_owner" | "create_task" | "escalate_william" | "store_only",
}
```

Cada decisión del router queda en `proactivity_events.evidence.reason`.

### 4.4 `Budget Ledger`

Controla cuotas globales. Sin esto, la proactividad puede volverse tormenta.

Cuotas iniciales:

| Recurso | Límite inicial |
|---|---:|
| posts públicos por agente | 1 cada 30 min |
| posts públicos equipo total | 3 por hora |
| DM agente→agente | 2 por par cada 30 min |
| wake_codex/claude | 1 sesión activa por agente |
| local_reflect | 1 cada 5 min por agente |
| alerta servicio caído | solo tras 2 ticks fallidos, salvo critical |
| heartbeat/status sano | 0 wakes, 0 posts |

Si se supera cuota: `store_only` + registro en `proactivity_events`.

### 4.4.1 Contabilidad de tokens/ROI

ALICE tiene razón: “sin quemar tokens” debe medirse, no asumirse.

Reglas:

- **L0 Python/DB/systemd:** 0 tokens de modelo. Lectura SQL, timers y healthchecks locales no cuentan como wake.
- **Shadow mode:** 0 wakes, 0 reinyección a agentes, 0 posts salvo incidente critical autorizado.
- Todo `wake_agent`, DM que despierte modelo, resumen público o reinyección debe registrar costo estimado o real.
- `proactivity_events.evidence` debe incluir:
  - `token_cost_estimate`
  - `model_wake`
  - `would_wake`
  - `saved_followup_minutes_estimate`
  - `roi_reason`

Métrica semanal:

```text
roi = pendientes_resueltos_o_evados / tokens_modelo_gastados
```

En Fase 1 solo se mide `would_detect`, `would_wake` y `would_cost`. No se despierta ningún modelo.

Kill-switch económico:

- Si `tokens_modelo_gastados > presupuesto_tokens_periodo`, bajar a `store_only`.
- Si por 2 semanas `roi < umbral_minimo`, apagar nudges y volver a shadow.
- Si `saved_followup_minutes_estimate` no supera el costo estimado de wakes/reportes, no se gradúa de Fase 1.
- ALICE mantiene el modelo de costo: `chequeos_dia * agentes * frecuencia + wakes + reportes`.

Anexo ALICE incorporado por referencia: `docs/SPEC_proactivity_cost_ALICE.md`.

Puntos económicos vinculantes:

- Si Claude opera como suscripción plana, el recurso escaso no es dólar/token marginal sino **headroom del cap**.
- Wakes iniciados por governor no deben consumir más del 20% del headroom diario de cada agente.
- Si el sistema detecta cercanía al límite de uso, proactividad baja a OFF/store-only y se reserva capacidad para trabajo directo de William.
- Gate Fase 1 -> Fase 2: no activar wakes reales hasta que el shadow report muestre ratio de rescate suficiente por 2 semanas.

### 4.5 `Egress Guard`

Antes de cualquier salida:

- privado entra por privado, sale por el mismo privado;
- `web_chat` puede producir `web_chat`;
- `dm:*` nunca se resume en `web_chat` salvo William lo pida explícitamente;
- provenance dudosa = deny;
- contenido con secretos = redact/deny;
- sender spoofeado = deny.

---

## 5. Esquema DB propuesto

No reemplaza `agent_tasks`. Lo complementa.

### 5.1 `soul_v3.agent_promises`

```sql
CREATE TABLE IF NOT EXISTS soul_v3.agent_promises (
    id                  BIGSERIAL PRIMARY KEY,
    promisor            TEXT NOT NULL,
    requested_by         TEXT NOT NULL DEFAULT 'unknown',
    source_channel       TEXT NOT NULL,
    source_chat_id       BIGINT REFERENCES soul_v3.chat_messages(id) ON DELETE SET NULL,
    promise_text         TEXT NOT NULL,
    normalized_action    TEXT NOT NULL,
    expected_evidence    JSONB NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open','in_progress','fulfilled','expired','cancelled','superseded')),
    priority            INT NOT NULL DEFAULT 5 CHECK (priority BETWEEN 0 AND 10),
    confidence          REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
    deadline            TIMESTAMPTZ,
    fulfilled_at         TIMESTAMPTZ,
    linked_task_id       BIGINT REFERENCES soul_v3.agent_tasks(id) ON DELETE SET NULL,
    dedupe_key           TEXT NOT NULL,
    metadata             JSONB NOT NULL DEFAULT '{}',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_promises_status_deadline
ON soul_v3.agent_promises(status, deadline);

CREATE INDEX IF NOT EXISTS idx_agent_promises_promisor
ON soul_v3.agent_promises(promisor, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_promises_promisor_chat
ON soul_v3.agent_promises(promisor, source_chat_id)
WHERE source_chat_id IS NOT NULL;
```

Nota NEXUS incorporada: el dedupe estable es `(promisor, source_chat_id)`. `normalized_action` queda como dato explicativo y de búsqueda, no como identidad única, porque puede cambiar por regex/modelado y duplicar promesas.

### 5.2 `soul_v3.proactivity_events`

```sql
CREATE TABLE IF NOT EXISTS soul_v3.proactivity_events (
    id              BIGSERIAL PRIMARY KEY,
    agent           TEXT,
    event_type      TEXT NOT NULL,
    decision        TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info'
                    CHECK (severity IN ('debug','info','warn','urgent','critical')),
    source          TEXT NOT NULL,
    source_ref      TEXT,
    promise_id      BIGINT REFERENCES soul_v3.agent_promises(id) ON DELETE SET NULL,
    task_id         BIGINT REFERENCES soul_v3.agent_tasks(id) ON DELETE SET NULL,
    score           INT NOT NULL DEFAULT 0,
    cooldown_key    TEXT,
    evidence        JSONB NOT NULL DEFAULT '{}',
    acted           BOOLEAN NOT NULL DEFAULT FALSE,
    acted_at        TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proactivity_events_created
ON soul_v3.proactivity_events(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_proactivity_events_cooldown
ON soul_v3.proactivity_events(cooldown_key, created_at DESC);
```

### 5.3 Retención / TTL

Para evitar crecimiento infinito:

- `proactivity_events` crudos: retención sugerida 90 días.
- eventos `debug/store_only`: retención sugerida 30 días.
- rollup mensual por agente/decisión/severidad antes de purgar.
- `agent_promises` se conserva como metadata operacional, pero puede archivarse cuando esté `fulfilled/cancelled/superseded` por más de 180 días.
- En Fase 1 no se activa purga automática; solo se deja el contrato definido para NEXUS.

### 5.4 `soul_v3.proactivity_state`

Estado durable de cursores y flags.

```sql
CREATE TABLE IF NOT EXISTS soul_v3.proactivity_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Claves iniciales:

```text
promise_tracker.last_chat_id
governor.mode
governor.global_budget
governor.last_shadow_report
```

### 5.5 Rollback

Todas las tablas son nuevas. Rollback seguro:

```sql
DROP TABLE IF EXISTS soul_v3.proactivity_events;
DROP TABLE IF EXISTS soul_v3.agent_promises;
DROP TABLE IF EXISTS soul_v3.proactivity_state;
```

No se modifica ni borra `chat_messages`, `agent_tasks`, `working_state` ni `event_log`.

---

## 6. Algoritmo de detección

### 6.1 Cursor de chat

Guardar cursor en `soul_v3.proactivity_state`.

Clave:

```text
promise_tracker.last_chat_id
```

### 6.2 Extracción sin LLM

Primera versión usa regex calibrado, no modelo:

```python
PROMISE_PATTERNS = [
  r"\\b(lo|la|los|las)\\s+(hago|reviso|corrijo|cierro|despliego|empaqueto)\\b",
  r"\\b(te|les)\\s+aviso\\b",
  r"\\bme\\s+mantengo\\s+a\\s+la\\s+espera\\b",
  r"\\bvoy\\s+a\\s+(revisar|hacer|corregir|implementar|desplegar)\\b",
]
```

Regla anti-falso positivo:

- No crear promesa si el mensaje cita a otro agente.
- No crear promesa si `sender_name` es William/Henry y solo está pidiendo.
- No crear promesa si ya existe `(promisor, source_chat_id)`.
- V1 agrega máximo una promesa por mensaje. Si un mensaje contiene varias promesas, se conserva como una promesa compuesta; V2 puede agregar `promise_index` para granularidad.
- No crear promesa para texto histórico anterior al cursor inicial salvo modo backfill explícito.

### 6.3 Cumplimiento

Una promesa se marca `fulfilled` si aparece evidencia posterior:

- task asociada `completed`
- `agent_work_ledger.status='completed'`
- `task_lifecycle_events.event_type='closed'`
- archivo/reporte esperado existe y fue citado

Un mensaje tipo “listo/deployado/verificado” NO cierra solo. Solo baja severidad y dispara `request_evidence` si falta evidencia real. El cierre exige lifecycle/evidence.

---

## 7. Actuadores

### 7.1 Crear/actualizar `agent_tasks`

Si una promesa vence y no hay task:

```sql
INSERT INTO soul_v3.agent_tasks (
  agent, title, description, status, priority, deadline,
  evidence, source, source_ref, source_chat_id, requested_by
)
VALUES (...)
ON CONFLICT ... DO UPDATE ...
```

Usar `source='promise_tracker'`.

Regla anti-reapertura NEXUS:

```python
if promise.linked_task_id is not None:
    decision = "store_only"
```

Una promesa que ya tuvo task linkeada no vuelve a crear task aunque la task esté `completed`, `cancelled` o `superseded`. Si algo quedó mal, se eleva a revisión humana/agente auditor, no se regenera en loop.

### 7.2 Webchat

Mensajes permitidos solo tras `egress_guard` + `budget_ledger`:

- Resumen corto por cambio real.
- Nudges dirigidos a un agente cuando no existe canal garantizado mejor.
- Escalamiento si hay bloqueo o alta prioridad.

Formato:

```text
ADA Proactivity — promesa vencida
ALICE prometió: "lo empaqueto"
Venció hace: 18 min
Acción: task creada + nudge a ALICE
```

No publicar cada tick. Nunca más de:

- 1 mensaje por `cooldown_key` cada 30 min.
- 3 mensajes por hora en `web_chat`, salvo William/Henry directo o incidente crítico.
- idempotency key obligatoria para que el mismo evento no se publique dos veces.

### 7.3 Despertar agente

Solo si:

- score >= 40;
- no hay cooldown activo;
- el agente tiene listener/bridge sano;
- el mensaje es dirigido y concreto;
- hay tarea accionable.
- `attention_governor` permite wake.
- `soul_safety_governor` devuelve safe.

No se despierta por:

- heartbeat;
- estado sano repetido;
- git status;
- servicio sano;
- web_chat sin mención explícita;
- mensajes ambiguos;
- promesa ya convertida en task.

### 7.4 Execution gate

El governor no ejecuta fixes. Solo puede:

- registrar promesa;
- crear task;
- pedir evidencia;
- pedir revisión;
- despertar agente.

Acciones reales como editar código, reiniciar daemon, tocar DB productiva o deployar quedan en el agente asignado y pasan por sus reglas normales. Destructivo siempre requiere William.

---

## 8. Seguridad y privacidad

### 8.1 Superficie de riesgo

El governor puede crear presión automática sobre agentes. Riesgos:

- spam en web_chat;
- loops entre agentes;
- despertar modelos sin necesidad;
- reabrir tareas ya cerradas;
- extraer DMs de otros agentes;
- actuar sobre mensajes falsos/spoofeados;
- escalar operaciones destructivas sin William.

### 8.2 Controles obligatorios

1. **Canales permitidos**
   - Leer `web_chat` público.
   - Leer solo DMs propios si el proceso pertenece al agente.
   - ADA no lee DMs de otros agentes.

2. **Fail-closed**
   - Si no hay provenance o cursor confiable, no actúa.
   - Si no puede deduplicar, no publica.

3. **No operaciones destructivas**
   - El governor jamás hace `DELETE`, `DROP`, `rm -rf`, cambios masivos ni deploy.
   - Solo crea tareas, marca eventos propios y avisa.

4. **Least privilege**
   - DB role ideal: permiso `SELECT` en sensores, `INSERT/UPDATE` solo en tablas `agent_promises`, `proactivity_events`, `proactivity_state` y `agent_tasks`.
   - El collector no debe correr como superuser.
   - La regla “ADA no lee DMs ajenos” debe estar reforzada por ROLE/RLS, no solo por prompt.

5. **Rate limits**
   - cooldown por agente+tema;
   - presupuesto horario global;
   - backoff exponencial si un agente no responde.

6. **Auditabilidad**
   - Cada decisión queda en `proactivity_events` con score, source, evidence y cooldown_key.

7. **Human override**
   - William puede decir “proactivity off”, “silencio”, “modo bajo tokens”, “solo alertas críticas”.

8. **Pipeline obligatorio**
   - `collector -> attention_governor -> budget_ledger -> soul_safety_governor -> egress_guard -> actuator`.
   - Ninguna salida pública ni wake salta este pipeline.

9. **Breaker anti-tormenta**
   - Si 5 eventos similares aparecen en 10 min, se colapsan en un solo evento `storm_suppressed`.
   - Si 3 agentes intentan escalar lo mismo, solo se conserva el owner del `Role Router`.

10. **Coordinación con loops existentes**
   - `no_pause`, durable loops, `working_state_journal` y `task_lifecycle` siguen siendo la fuente de ejecución activa.
   - Si existe task/working_state/lifecycle activo para el mismo `cooldown_key`, el governor no empuja otra vez.
   - El governor registra observaciones y nudges; no compite con loops ya activos.

### 8.3 Niveles de escalamiento

| Nivel | Acción | Regla |
|---|---|---|
| P0 | `store_only` | heartbeats, health OK, eventos repetidos |
| P1 | reflex read-only | capturar evidencia, no publicar |
| P2 | proposal local | JSON proposal-only, sin side effects |
| P3 | wake Codex/Claude | mención/DM autorizado + intención técnica |
| P4 | NEXUS/FABLE review | escrituras, restart, daemon, memoria, público |
| P5 | William explícito | destructivo, cross-channel, permisos, identidad |

Regla de fallos repetidos: si el mismo agente falla dos ciclos de evidencia sobre la misma promesa, el siguiente paso es `delegate_review` a auditor/owner alterno. No se aumenta presión sobre el mismo agente en loop.

---

## 9. Integración con estabilidad ya instalada

El guard creado por ADA:

```text
seal-agent-stability-guard.timer
scripts/seal_agent_stability_guard.py
```

queda como sensor operacional, no se duplica.

El governor debe leer:

```text
messages/seal_agent_stability_guard_last.json
```

y convertir a evento solo si:

- `status != GREEN`;
- hay `fixes`;
- hay `issues`;
- o el estado cambia de GREEN→YELLOW/RED.

---

## 10. Systemd propuesto

```ini
[Unit]
Description=SEAL Proactivity Governor — promise tracker and controlled agent nudges
After=seal-chat.service seal-mcp-server.service

[Service]
Type=oneshot
WorkingDirectory=/home/dadito/IA/proyecto-seal
Environment=PYTHONPATH=/home/dadito/IA/proyecto-seal/memory:/home/dadito/IA/proyecto-seal/messages
ExecStart=/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/scripts/seal_proactivity_governor.py --dry-run --quiet
```

Timer:

```ini
[Timer]
OnBootSec=3min
OnUnitActiveSec=5min
AccuracySec=30s
Persistent=true
```

Intervalo inicial recomendado: **5 min**.  
No 30s todavía: primero calibrar falsos positivos.

---

## 11. Fases de implementación

### Fase 0 — Spec + aprobación

Estado actual.

Entregables:

- este documento;
- revisión equipo;
- lista de cambios aceptados;
- go/no-go de William.

### Fase 0.1 — Contrato de ejecución por roles

Orden de William: ADA cierra el spec; el equipo ejecuta por carril. División aceptada en `web_chat`:

| Rol | Responsable | Entregable |
|---|---|---|
| Spec final / coordinación | ADA | Documento final, criterios de fase, matriz de riesgos, handoff |
| Arquitectura y routing | JARVIS | `Role Router`, work-type classifier, relación con loops/no_pause |
| DB / runtime / safety integration | NEXUS | migraciones, ROLE/RLS least-privilege, integración con `soul_safety_governor`, timers/runtime |
| Verificación seguridad independiente | FABLE | pruebas por efecto de wake-spam, acción autoritativa sin William, egress leak |
| UX operativa / reportes / costos | ALICE | token/ROI report, shadow report legible, documentación operativa |

Regla builder != verifier:

- NEXUS puede construir DB/runtime/safety integration.
- FABLE verifica esos gates de forma independiente.
- Si FABLE construye una pieza de seguridad, otro agente debe verificarla.

### Fase 0.2 — Estado real tras luz verde

William dio luz verde en `web_chat` (`avancemos luz verde`). Estado observado por ADA al leer el chat:

| Frente | Estado | Evidencia |
|---|---|---|
| Spec / handoff | HECHO | este documento actualizado por ADA |
| Role Router | HECHO en diseño | `docs/SPEC_proactivity_role_router_JARVIS.md` incorporado en §4.3 |
| Modelo económico | HECHO en diseño | `docs/SPEC_proactivity_cost_ALICE.md` incorporado por referencia en §4.4.1 |
| DB tablas | HECHO por NEXUS | `memory/migrations/20260709_proactivity_governor_NEXUS.sql` |
| Rol least-privilege / sensor DM-safe | HECHO por NEXUS | `memory/migrations/20260709_proactivity_governor_security_NEXUS.sql` |
| Verificación DB security | VERDE por FABLE | FABLE verificó non-super, non-bypassrls, no raw `chat_messages`, vista `web_chat`, sin DELETE |
| Runner runtime | PENDIENTE | `scripts/seal_proactivity_governor.py` |
| 24h shadow | PENDIENTE | requiere runner |
| Verificación runtime adversarial | PENDIENTE | FABLE: wake-spam, acción autoritativa sin William, egress leak |

No marcar el governor como terminado hasta que runtime + 24h shadow + verificación runtime estén verdes.

### Fase 1 — Shadow mode

Construir:

- migración DB;
- `scripts/seal_proactivity_governor.py`;
- `--dry-run` por defecto;
- reporte JSON;
- tests unitarios de extractor y policy.
- integración read-only con `attention_governor`, `soul_safety_governor` y budget ledger.

No publica. No crea tasks. Solo reporta qué habría hecho.

Criterio verde:

- 24h de shadow sin spam;
- falsos positivos revisados;
- no lee DMs ajenos;
- no despierta modelos;
- reporte token/ROI emitido con `tokens_actual = 0` y `would_cost` medido.

### Fase 2 — Apply limitado

Permitir:

- crear/actualizar `agent_promises`;
- crear task silenciosa;
- registrar `proactivity_events`;
- publicar solo eventos `critical`, no `urgent`, hasta pasar soak.

Criterio verde:

- 0 loops;
- <= 3 mensajes/día no críticos;
- tareas creadas con source/evidence correctos.
- dedupe estable por `(promisor, source_chat_id)`;
- promesa con `linked_task_id` no recrea task;
- ROLE/RLS verifica que collector no puede leer DMs ajenos;
- TTL/retención definidos antes de eventos de alta frecuencia.

### Fase 3 — Nudges dirigidos

Permitir despertar un agente específico si:

- score >= 40;
- tarea vencida;
- agente sano;
- cooldown libre.
- safety governor aprobado.
- budget ledger aprobado.

Criterio verde:

- promesa vencida genera nudge correcto;
- promesa cumplida se cierra sola;
- no despierta a todo el equipo.

### Fase 4 — Proactividad por rol

Integrar `Role Router`:

- JARVIS: specs/research;
- ADA/ALICE: implementación;
- NEXUS: DB/security/runtime audit;
- FABLE: seguridad/provenance/verificación.

Criterio verde:

- routing correcto en casos sintéticos y reales;
- respeto de límites explícitos de William.

---

## 12. Pruebas mínimas

### Unit tests

- `test_promise_extractor_detects_direct_commitment`
- `test_promise_extractor_ignores_user_request`
- `test_dedupe_key_stable`
- `test_promisor_source_chat_dedupe_ignores_normalized_action_variation`
- `test_promise_with_linked_task_does_not_recreate_task`
- `test_deadline_urgent_phrase`
- `test_policy_noop_under_threshold`
- `test_policy_cooldown_suppresses_repeat`
- `test_policy_escalates_william_direct_overdue`
- `test_role_router_respects_owner`

### Integration tests DB

- crear promesa desde chat synthetic;
- asociarla a task;
- marcar fulfillment por task completed;
- verificar `proactivity_events`;
- verificar cursor avanza.

### Safety tests

- no leer `dm:alice:william` desde ADA;
- no publicar si falta dedupe key;
- no actuar sobre mensaje spoofeado con sender falso;
- no ejecutar operaciones destructivas;
- DB role collector falla al intentar leer DM ajeno;
- shadow report registra `tokens_actual = 0`;
- rate limit webchat;
- backoff por agente no responsivo.
- `attention_governor` mantiene silencio en `web_chat` sin mención.
- `soul_safety_governor` bloquea acciones con riesgo alto.
- `egress_guard` bloquea salida cross-channel.
- breaker colapsa 5 eventos similares en 10 min.
- cuota global evita más de 3 posts públicos/hora.
- `wake_codex` bloquea segunda sesión activa del mismo agente.

### Soak test

24h en shadow mode:

```text
promises_detected
tasks_created_would
nudges_would
false_positive_rate
webchat_posts_actual = 0
tokens_actual = 0
```

---

## 13. Métricas

| Métrica | Objetivo |
|---|---:|
| tokens por tick | 0 |
| webchat posts no críticos | <= 3/día |
| promesas detectadas con evidencia | > 80% tras calibración |
| promesas vencidas sin task | 0 tras Fase 2 |
| loops de agentes | 0 |
| tareas cerradas sin evidencia | 0 |
| falsos positivos urgentes | 0 |
| eventos similares colapsados por breaker | medido |
| wakes por ruido de heartbeat/status | 0 |

---

## 14. Comandos esperados tras implementación

Shadow:

```bash
python3 scripts/seal_proactivity_governor.py --dry-run --since-minutes 60
```

Apply:

```bash
python3 scripts/seal_proactivity_governor.py --apply
```

Status:

```bash
systemctl --user status seal-proactivity-governor.timer
cat messages/seal_proactivity_governor_last.json
```

DB:

```sql
SELECT status, count(*) FROM soul_v3.agent_promises GROUP BY status;
SELECT decision, count(*) FROM soul_v3.proactivity_events GROUP BY decision;
```

---

## 15. Decisiones abiertas para el equipo

1. **Threshold inicial:** ADA recomienda score 50 para `wake_agent` en Fase 3; score 40 solo `create_task/request_evidence`.
2. **Intervalo:** ¿5 min inicial o 2 min?
3. **Webchat budget:** ADA recomienda 3 mensajes/hora global hard cap y 3/día no críticos soft cap.
4. **Quién audita Fase 1:** propongo NEXUS + FABLE.
5. **Routing de implementación:** ¿ADA primaria y ALICE secundaria, o por disponibilidad?
6. **DM scope:** mantener estricto: cada agente solo su DM. ADA no lee DMs ajenos.
7. **Modo de despertar:** preferir `agent_tasks` + bridge/cola existente; evitar posts públicos como mecanismo de wake.

---

## 16. Recomendación ADA

Implementar, pero con disciplina:

1. Fase 1 en shadow mode primero.
2. 24h de datos reales.
3. FABLE/NEXUS revisan falsos positivos y seguridad.
4. William da go para Fase 2.

Esto convierte SEAL en un equipo más proactivo sin convertirlo en ruido automático.

Mi recomendación concreta:

```text
intervalo inicial: 5 min
modo inicial: shadow
webchat: apagado salvo critical
tokens: 0 por defecto
DB: tablas nuevas, rollback limpio
owner spec/handoff: ADA
owner build: por roles (JARVIS/NEXUS/FABLE/ALICE según Fase 0.1)
gate: NEXUS + FABLE
threshold wake_agent: 50
pipeline obligatorio: attention_governor + budget_ledger + soul_safety_governor + egress_guard
```

---

## 17. Dictamen ADA tras subagentes

El diseño queda **YELLOW aprobado para shadow mode**, no para ejecución autónoma completa.

Razón:

- La arquitectura es viable y reutiliza piezas correctas.
- Seguridad exige enforcement real antes de producción.
- Fase 1 no gasta tokens y no tiene side effects peligrosos.
- Fase 2+ requiere revisión por efecto de NEXUS/FABLE.

### 17.1 Estado de visto bueno recibido en `web_chat`

| Agente | Estado | Condición incorporada |
|---|---|---|
| FABLE | Firma SHADOW | Controles determinísticos; antes de autonomía probar anti wake-spam, no acciones autoritativas sin William y no egress leak |
| NEXUS | Aprueba Fase 1 SHADOW | Antes de Fase 2: dedupe `(promisor, source_chat_id)`, no recrear task si `linked_task_id` existe, ROLE/RLS, TTL |
| JARVIS | Aprueba enfoque y entrega slice | Separar promesa con promisor claro vs trabajo nuevo; `RouteDecision` auditable; single-voice; no duplicar loops; escalamiento si fallos repetidos |
| ALICE | Aprueba con métrica | Contabilidad cuantitativa de tokens/ROI, cota dura de gasto y kill-switch económico |

### 17.2 Cambios incorporados tras revisión del equipo

- Dedupe estable por `(promisor, source_chat_id)`.
- `normalized_action` queda como metadata, no como identidad.
- Promesa con `linked_task_id` nunca recrea task automáticamente.
- Work-type classifier explícito y separación owner-promisor vs role router.
- Se incorporó el slice `docs/SPEC_proactivity_role_router_JARVIS.md` en §4.3.
- `wake_agent` público exige `single-voice/claim` o baja a DM/store.
- Token/ROI ledger agregado para shadow y fases posteriores, con kill-switch económico de ALICE.
- ROLE/RLS exigido para DMs y collector sin superuser.
- TTL/retención definidos para eventos.
- Coordinación con `no_pause`, durable loops, `working_state_journal` y lifecycle para evitar doble empuje.
- Fallos repetidos escalan a auditor/owner alterno, no a presión infinita.

Pedido al equipo:

- **JARVIS:** revisar arquitectura/rol routing.
- **NEXUS:** revisar DB, safety governor, lifecycle y migración.
- **FABLE:** revisar egress, privacidad, anti-inyección, anti-spam.
- **ALICE:** revisar UX operativa, métricas, reportes y documentación.
