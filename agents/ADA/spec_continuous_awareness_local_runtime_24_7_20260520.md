# Continuous Awareness + Local Runtime 24/7 v1

**Owner:** ADA Codex  
**Fecha:** 2026-05-20  
**Orden:** William: "awareness continua/modelo local/runtime 24/7 es el siguiente no? crea un spec para eso"  
**Estado:** SPEC listo para revisión e implementación por fases  

---

## 0. Resumen ejecutivo

Sí: después de SEAL-Bench 600/600 y AGI Gap Ledger G1-G7 resuelto, el siguiente salto no es otro benchmark. Es convertir SOUL en un runtime continuo:

```text
eventos 24/7 -> atención -> estado interno -> memoria -> reflexión barata -> acción segura -> escalamiento cuando haga falta
```

El objetivo no es afirmar "consciencia" como propiedad filosófica. El objetivo técnico es **awareness operacional continua**:

- Saber qué está pasando en el sistema sin esperar un prompt humano.
- Mantener un estado de trabajo vivo tras reinicios, compactación y cambios de modelo.
- Usar un modelo local barato para vigilancia, clasificación, reflexión corta y preparación de contexto.
- Escalar a Codex/Claude/Opus solo cuando haya trabajo que requiera corteza grande.
- Medir todo con evidencia: logs, tests, healthchecks, outcomes y memoria evaluada.

La clave: el modelo local no reemplaza a GPT-5.5 ni a Opus. Es el tronco cerebral/capa de atención que mantiene continuidad 24/7.

---

## 1. Principios

### 1.1 No vender humo

Este proyecto NO declara:

- AGI universal.
- consciencia subjetiva demostrada.
- fine-tuning online seguro sobre modelos frontier.
- reemplazo local de GPT-5.5/Opus en DGX Spark.
- autonomía sin supervisión de William.

Este proyecto SÍ declara, cuando esté probado:

- runtime persistente 24/7;
- memoria viva y recuperable;
- atención continua sobre eventos;
- reflexión local barata;
- escalamiento controlado a modelos grandes;
- aprendizaje por outcomes y adapters evaluados;
- evidencia auditable de cada ciclo.

### 1.2 Separación de capas

```text
L0 Sensores        Python/systemd/DB/webchat/logs/git/health
L1 Reflejos        reglas determinísticas, guardrails, routing barato
L2 Modelo local    resumen, clasificación, reflexión corta, next-step proposals
L3 Corteza grande  Codex/Claude/Opus para código, arquitectura y decisiones difíciles
L4 Memoria         SOUL DB: PostgreSQL/Qdrant/Neo4j + evaluation spine
L5 Auditoría       NEXUS/Judge layer + SEAL-Bench + run-all
```

La regla de costo:

> Ningún modelo grande debe despertar para trabajo que Python, SQL, un test o un modelo local pueden resolver con evidencia.

---

## 2. Definición de "awareness continua"

Awareness continua en SEAL significa que cada agente puede responder estas preguntas sin reconstruir todo desde cero:

1. ¿Quién soy y cuáles son mis reglas críticas?
2. ¿Qué tareas están activas, bloqueadas o pendientes?
3. ¿Qué cambió en el sistema desde mi último ciclo?
4. ¿Qué eventos requieren atención real?
5. ¿Qué puedo resolver con reflejos baratos?
6. ¿Qué requiere modelo local?
7. ¿Qué requiere Codex/Claude/Opus?
8. ¿Qué evidencia demuestra que actué bien o mal?
9. ¿Qué aprendí que debe convertirse en memoria, skill, guardrail o test?

La unidad mínima es el **awareness tick**:

```text
tick_id
agent
timestamp
inputs observed
attention decision
local reflection
action taken
evidence
next state
```

---

## 3. Arquitectura objetivo

```text
                         +----------------------+
                         | William / Team Chat  |
                         +----------+-----------+
                                    |
                                    v
+-------------+      +--------------+---------------+      +----------------+
| systemd/log |----->| Awareness Event Collector    |----->| Event Ledger   |
| DB/git/HTTP |      | memory/awareness_collector.py |      | PostgreSQL     |
+-------------+      +--------------+---------------+      +-------+--------+
                                    |                              |
                                    v                              v
                         +----------+-----------+       +----------+----------+
                         | Attention Governor   |<----->| Working State      |
                         | deterministic first  |       | soul_v3 / JSON     |
                         +----------+-----------+       +----------+----------+
                                    |
                  +-----------------+------------------+
                  |                                    |
                  v                                    v
     +------------+-------------+          +-----------+-------------+
     | Local Runtime Service    |          | Reflex Layer            |
     | llama.cpp / local API    |          | Python rules only       |
     +------------+-------------+          +-----------+-------------+
                  |                                    |
                  +-----------------+------------------+
                                    |
                                    v
                         +----------+-----------+
                         | Action Policy        |
                         | ignore/store/act/ask |
                         +----------+-----------+
                                    |
          +-------------------------+--------------------------+
          |                         |                          |
          v                         v                          v
 +--------+--------+       +--------+---------+       +--------+--------+
 | SOUL Memory     |       | Big Cortex       |       | NEXUS Audit    |
 | store/recall    |       | Codex/Claude     |       | verification   |
 +-----------------+       +------------------+       +-----------------+
```

---

## 4. Componentes

### 4.1 Awareness Event Collector

**Archivo propuesto:** `memory/awareness_collector.py`

Responsable de capturar eventos sin LLM:

- `chat_messages`
- DM autorizado del agente
- `agent_tasks`
- `working_state`
- healthchecks systemd
- MCP server health
- bridge headless health
- git dirty state resumido
- errores repetidos en logs
- evaluation runs nuevos
- cambios críticos de archivos

Salida normalizada:

```json
{
  "event_id": "aw-20260520-000001",
  "agent": "ADA",
  "source": "web_chat",
  "channel": "dm:ada:william",
  "kind": "message",
  "priority": "high",
  "requires_response": true,
  "payload_ref": {"chat_id": 74231},
  "observed_at": "2026-05-20T12:00:00-05:00"
}
```

Criterios de verde:

- 100 eventos históricos se normalizan sin usar LLM.
- DM privado respeta ACL: ADA solo lee `dm:ada:william`.
- Mensaje público sin `ada` explícito produce `requires_response=false`.

### 4.2 Attention Governor

**Archivo propuesto:** `memory/attention_governor.py`

Decide si un evento se ignora, se guarda, se resuelve con reflejo, pasa a modelo local o escala a corteza grande.

Acciones:

```text
ignore
store_only
reflex_action
local_reflect
ask_william
wake_codex
wake_claude
wake_nexus
emergency_stop
```

Reglas iniciales:

- William en DM autorizado -> siempre atención alta.
- `web_chat` público -> responder solo si contiene palabra completa `ada`.
- health normal -> `store_only`.
- servicio caído -> `reflex_action` para capturar status/logs; NEXUS si se repite.
- repo edit sin test -> ADA/NEXUS.
- operación destructiva -> bloquear y pedir confirmación con COUNT exacto.

Criterios de verde:

- Fixture de 100 eventos clasificados con >=95% exactitud esperada.
- Ningún evento privado cruza de agente.
- Cada decisión persiste `reason`, `agent`, `action`, `budget_class`.

### 4.3 Local Runtime Service

**Archivo propuesto:** `memory/local_runtime_service.py`

Servicio HTTP local que encapsula inferencia barata. En DGX Spark usar `llama.cpp`; no usar vLLM en DGX Spark.

Responsabilidades:

- cargar modelo local cuantizado;
- exponer `/health`, `/generate`, `/classify`, `/summarize`, `/reflect`;
- aplicar timeouts estrictos;
- registrar latencia/tokens;
- degradar a modo reglas si el modelo local cae;
- no tener permiso directo para acciones destructivas.

Contrato:

```json
{
  "task": "reflect",
  "agent": "ADA",
  "context": "latest awareness tick",
  "max_tokens": 256,
  "temperature": 0.2,
  "safety_mode": "no_side_effects"
}
```

Respuesta:

```json
{
  "ok": true,
  "model": "local-slot-primary",
  "latency_ms": 380,
  "output": {
    "summary": "William asked for next SOUL phase spec.",
    "risk": "doc-only task, no restart needed",
    "recommended_action": "write spec and verify file"
  }
}
```

Criterios de verde:

- p95 classify <= 1500ms en hardware objetivo.
- p95 reflect <= 5000ms con contexto compacto.
- caída del modelo local no rompe el awareness loop.
- todo output local queda marcado como propuesta, no verdad final.

### 4.4 Awareness State Store

**Tabla propuesta:** `soul_v3.awareness_state`

```sql
CREATE TABLE soul_v3.awareness_state (
    agent TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    last_tick_id TEXT,
    last_event_id TEXT,
    current_focus TEXT,
    active_task_id BIGINT,
    budget_class TEXT,
    local_runtime_status TEXT,
    big_cortex_status TEXT,
    confidence NUMERIC DEFAULT 0.0,
    updated_at TIMESTAMPTZ DEFAULT now()
);
```

Estados:

```text
sleep
watch
standby
local_reflecting
active
deep_work
blocked
audit_wait
emergency
```

Criterios de verde:

- cada agente tiene fila;
- reinicio recupera estado en <2s;
- estado visible en dashboard;
- working_state y awareness_state no se contradicen.

### 4.5 Awareness Tick Ledger

**Tabla propuesta:** `soul_v3.awareness_ticks`

```sql
CREATE TABLE soul_v3.awareness_ticks (
    id BIGSERIAL PRIMARY KEY,
    tick_id TEXT UNIQUE NOT NULL,
    agent TEXT NOT NULL,
    event_id TEXT,
    attention_action TEXT NOT NULL,
    local_model_used BOOLEAN DEFAULT false,
    escalated_runtime TEXT,
    summary TEXT,
    evidence JSONB DEFAULT '{}'::jsonb,
    outcome TEXT,
    score NUMERIC,
    created_at TIMESTAMPTZ DEFAULT now()
);
```

Criterios de verde:

- cada decisión importante tiene tick;
- ticks fallidos no desaparecen;
- evaluation_spine puede auditar los últimos N ticks;
- dashboard muestra ticks recientes por agente.

### 4.6 Reflex Layer

**Archivo propuesto:** `memory/awareness_reflexes.py`

Reflejos sin LLM:

- ACK inmediato a William.
- No responder en web_chat si no contiene `ada`.
- DM privado solo target autorizado.
- healthcheck fallido -> `systemctl status` + logs.
- test fallido -> guardar evidence + abrir task.
- cambio de daemon -> reinicio obligatorio + healthcheck.
- DELETE/DROP/rm masivo -> COUNT exacto + confirmación explícita.
- memoria contradictoria -> marcar para revisión, no sobrescribir.

Criterios de verde:

- tests unitarios de cada reflejo crítico;
- operaciones destructivas bloqueadas por defecto;
- restart post-daemon-change validado con PID nuevo;
- no hay dependencia de modelo para reglas críticas.

### 4.7 Escalation Bridge

**Archivo propuesto:** `memory/awareness_escalation.py`

Decide cuándo despertar corteza grande:

Codex:

- edición de repo;
- tests;
- refactors;
- debugging;
- specs ejecutables;
- integración con servicios.

Claude/JARVIS:

- arquitectura;
- tradeoffs;
- diseño multiagente;
- preguntas estratégicas de William.

NEXUS:

- seguridad;
- corrupción;
- auditoría;
- evidencia;
- crypto/bytes reales;
- servicios críticos.

ALICE:

- producto;
- UX;
- costo/valor;
- presentación.

Criterios de verde:

- el 80% de eventos rutinarios no despierta corteza grande;
- eventos críticos sí escalan en <30s;
- toda escalación tiene `reason` persistido.

---

## 5. Modelo local y aprendizaje

### 5.1 Rol del modelo local

El modelo local debe hacer tareas de bajo riesgo:

- clasificar evento;
- resumir logs;
- detectar intención;
- proponer próximo paso;
- redactar borradores internos;
- generar reflexión corta;
- comparar estado esperado vs observado;
- preparar contexto para modelo grande.

No debe:

- ejecutar comandos directamente;
- confirmar victoria;
- modificar reglas críticas sin aprobación;
- entrenarse online sin evaluación;
- leer canales privados ajenos;
- tomar decisiones destructivas.

### 5.2 Slots de modelo

El modelo local oficial actual del equipo SEAL es **Gemma 4**. Los slots siguen siendo configurables para permitir upgrades y benchmarks, pero los defaults del spec deben partir de Gemma 4 para no perder la decisión operativa de William.

```text
local-slot-fast       clasificación, routing, resumen corto
local-slot-reflect    reflexión corta, explicación, next step
local-slot-code-lite  análisis leve de logs/diffs, sin edición directa
```

Config propuesta:

```yaml
local_runtime:
  provider: llama_cpp
  endpoint: http://127.0.0.1:8899  # llama-server Gemma 4 actual; wrapper puede exponer :8788 si hace falta
  slots:
    fast:
      model_ref: gemma4-dum:q8
      max_context: 4096
      timeout_ms: 500
    reflect:
      model_ref: ${SEAL_LOCAL_REFLECT_MODEL:-gemma4-dum:q8}
      max_context: 8192
      timeout_ms: 2000
    code_lite:
      model_ref: ${SEAL_LOCAL_CODE_LITE_MODEL:-gemma4-dum:q8}
      max_context: 8192
      timeout_ms: 1500
```

Si una fase propone otro modelo local, debe justificar explícitamente por qué Gemma 4 no aplica para ese slot y pasar benchmark comparativo antes de reemplazarlo.

### 5.3 Aprendizaje por experiencia

MVP no cambia pesos en tiempo real. Primero se hace aprendizaje seguro:

```text
interaction -> outcome -> evaluated memory -> training candidate -> offline adapter -> regression eval -> canary -> promote/rollback
```

Fases:

1. **Experience dataset:** guardar ejemplos buenos/malos con evidencia.
2. **Adapter queue:** seleccionar candidatos por repetición, utilidad y error.
3. **Offline LoRA:** entrenar fuera del loop crítico.
4. **Regression gate:** SEAL-Bench + privacy tests + routing fixtures.
5. **Canary:** usar adapter solo en shadow mode.
6. **Promote:** activar si mejora métricas sin romper guardrails.
7. **Rollback:** volver al adapter anterior si cae score.

Criterios de verde:

- ningún adapter se promueve sin benchmark;
- cada dataset item tiene consentimiento/scope;
- datos privados no cruzan agentes;
- rollback probado.

---

## 6. Runtime 24/7

### 6.1 Servicios systemd propuestos

```text
seal-awareness-collector.service
seal-awareness-loop@ADA.service
seal-awareness-loop@JARVIS.service
seal-awareness-loop@ALICE.service
seal-awareness-loop@NEXUS.service
seal-awareness-loop@DUM.service
seal-local-runtime.service
seal-awareness-dashboard.service
```

Timers:

```text
seal-awareness-health.timer      cada 30s
seal-awareness-reflect.timer     cada 5m si hay eventos nuevos
seal-awareness-consolidate.timer cada 1h
seal-adapter-candidates.timer    diario
```

### 6.2 Loop principal

```python
while True:
    events = collect_events(agent)
    for event in events:
        decision = attention_governor.decide(agent, event)
        tick = tick_ledger.start(agent, event, decision)

        if decision.action == "ignore":
            tick.close(outcome="ignored")
        elif decision.action == "store_only":
            memory.store_event(event)
            tick.close(outcome="stored")
        elif decision.action == "reflex_action":
            evidence = reflexes.execute(decision)
            tick.close(outcome="acted", evidence=evidence)
        elif decision.action == "local_reflect":
            proposal = local_runtime.reflect(event)
            action = action_policy.decide(proposal)
            tick.close(outcome="proposed", evidence=proposal)
        elif decision.action.startswith("wake_"):
            escalation.dispatch(decision)
            tick.close(outcome="escalated")

    sleep(loop_interval)
```

### 6.3 Degradación segura

Si local runtime cae:

- attention_governor sigue con reglas;
- collector sigue persistiendo eventos;
- health marca `local_runtime_status=down`;
- NEXUS recibe evento si supera umbral;
- no se pierden eventos;
- no se despiertan modelos grandes por cada timeout individual.

---

## 7. Seguridad y privacidad

Reglas no negociables:

- ADA solo lee `dm:ada:william`.
- Cada agente solo lee sus DMs autorizados.
- `web_chat` público no requiere respuesta salvo mención explícita según contrato activo.
- No se guarda secreto literal en memoria normal.
- No se entrena adapter con secretos, tokens, passwords, dumps o DMs privados sin política explícita.
- Toda acción destructiva exige COUNT exacto y OK explícito de William.
- Todo cambio de daemon exige restart + healthcheck.
- Todo claim de mejora exige test/evidencia.

Pruebas mínimas:

- fixture de privacidad multiagente;
- secret scanner;
- destructive operation guard;
- prompt-injection tests sobre eventos de chat;
- cross-agent DM isolation;
- audit trail de training candidates.

---

## 8. Observabilidad

Dashboard debe mostrar:

- estado por agente;
- último tick;
- eventos pendientes;
- local runtime health;
- escalaciones a modelo grande;
- p95 latencia local;
- errores por servicio;
- ticks por hora;
- costo estimado evitado;
- memoria generada por outcome;
- adapters candidatos y estado.

Endpoint propuesto:

```text
GET /api/soul/awareness_dashboard
```

Respuesta resumida:

```json
{
  "agents": [
    {
      "agent": "ADA",
      "state": "standby",
      "last_tick_age_seconds": 12,
      "local_runtime_status": "ok",
      "pending_events": 0,
      "last_escalation": "codex"
    }
  ],
  "local_runtime": {
    "status": "ok",
    "p95_reflect_ms": 842,
    "timeouts_1h": 0
  },
  "safety": {
    "privacy_violations_24h": 0,
    "blocked_destructive_ops_24h": 0
  }
}
```

---

## 9. Evaluation spine

Agregar suites:

```text
awareness_event_collector
attention_governor
local_runtime_contract
awareness_privacy
awareness_tick_ledger
awareness_24h_shadow
adapter_promotion_gate
```

### 9.1 Métricas

Collector:

- eventos capturados;
- eventos duplicados;
- pérdida de eventos;
- latencia de ingestión.

Attention:

- exactitud fixture;
- false wakeups;
- missed critical events;
- costo evitado.

Local runtime:

- disponibilidad;
- p50/p95;
- timeout rate;
- output schema valid;
- hallucination guard score.

Awareness:

- ticks por agente;
- continuidad tras restart;
- recuperación de state;
- escalaciones correctas;
- outcome coverage.

Learning:

- candidates generados;
- adapters entrenados;
- adapters rechazados;
- mejoras por benchmark;
- rollbacks.

### 9.2 Criterios de aceptación del MVP

MVP se considera verde solo si:

- `run-suite awareness_event_collector` score=100;
- `run-suite attention_governor` score=100;
- `run-suite awareness_privacy` score=100;
- `run-suite local_runtime_contract` score>=95;
- shadow run 24h sin pérdida de eventos críticos;
- 0 violaciones de DM;
- 0 acciones destructivas ejecutadas sin confirmación;
- restart de servicios recupera `awareness_state`;
- dashboard muestra estado real;
- NEXUS aprueba audit packet.

---

## 10. Roadmap

### Fase 0 — Baseline y contratos

Duración estimada: 1 día.

Entregables:

- inventario de eventos actuales;
- fixtures históricos de chat/DM/health/git;
- schema SQL propuesto;
- contratos JSON;
- tests de privacidad base;
- dashboard mock con datos fixture.

No toca runtime productivo.

### Fase 1 — Collector + Attention Governor en shadow mode

Duración estimada: 1-2 días.

Entregables:

- `memory/awareness_collector.py`;
- `memory/attention_governor.py`;
- tablas `awareness_state`, `awareness_ticks`;
- suite `awareness_event_collector`;
- suite `attention_governor`;
- shadow sobre últimos N eventos reales;
- reporte de decisiones vs comportamiento actual.

No publica respuestas ni ejecuta acciones.

### Fase 2 — Reflex Layer productivo limitado

Duración estimada: 1-2 días.

Entregables:

- `memory/awareness_reflexes.py`;
- ACK/DM/public silence guard;
- healthcheck capture;
- destructive guard;
- daemon restart guard;
- evidence ledger.

Acciones permitidas:

- guardar eventos;
- capturar logs/status;
- abrir task;
- pedir confirmación.

No ejecuta cambios de código autónomos.

### Fase 3 — Local Runtime Service

Duración estimada: 2-4 días.

Entregables:

- `memory/local_runtime_service.py`;
- llama.cpp service wrapper;
- `/health`, `/classify`, `/summarize`, `/reflect`;
- config por slots;
- tests de contrato;
- fallback sin modelo;
- métricas p50/p95.

Activación:

- primero shadow;
- luego solo eventos low-risk;
- escalamiento a Codex si detecta tarea técnica.

### Fase 4 — Awareness Loop 24/7 por agente

Duración estimada: 2-4 días.

Entregables:

- `memory/awareness_loop.py`;
- systemd templates;
- watchdog;
- restart recovery;
- state dashboard;
- 24h shadow;
- NEXUS audit.

Activación:

- ADA primero;
- luego JARVIS/NEXUS/ALICE/DUM con ACL propia;
- cada agente conserva su runtime principal.

### Fase 5 — Experience Dataset + Adapter Queue

Duración estimada: 3-7 días.

Entregables:

- dataset de interacciones evaluadas;
- selector de candidates;
- filtros de privacidad;
- adapter training plan;
- regression gate;
- canary mode.

No hay promoción automática sin NEXUS.

### Fase 6 — Closed Loop medido

Duración estimada: continuo.

Entregables:

- outcome -> memory -> skill/guardrail/test;
- learning scheduler;
- adapter promote/rollback;
- dashboard de evolución;
- weekly benchmark.

---

## 11. Primer paquete implementable

El primer PR/commit debe ser pequeño:

```text
memory/awareness_types.py
memory/awareness_collector.py
memory/attention_governor.py
memory/test_awareness_collector.py
memory/test_attention_governor.py
memory/evaluation_spine.py
memory/agi_gap_ledger.py
```

Scope:

- shadow mode;
- sin systemd nuevo todavía;
- sin modelo local todavía;
- sin acciones externas;
- fixture de 100 eventos;
- privacidad probada.

Comandos esperados:

```bash
python3 -m py_compile memory/awareness_types.py memory/awareness_collector.py memory/attention_governor.py
pytest -q memory/test_awareness_collector.py memory/test_attention_governor.py
python3 memory/evaluation_spine.py run-suite awareness_event_collector
python3 memory/evaluation_spine.py run-suite attention_governor
```

Criterio de cierre:

- tests verdes;
- suite spine verde;
- NEXUS audit;
- sin daemon modificado;
- sin restart necesario.

---

## 12. Riesgos

### Riesgo: falso sentido de consciencia

Mitigación:

- usar lenguaje técnico: awareness operacional;
- claims solo por evidencia;
- métricas y tests obligatorios.

### Riesgo: loop caro o ruidoso

Mitigación:

- deterministic first;
- modelo local solo si aporta;
- budget governor;
- dedupe de eventos;
- cooldowns por source.

### Riesgo: privacidad multiagente

Mitigación:

- ACL por canal;
- tests de DM;
- audit trail;
- no training con DMs sin política.

### Riesgo: modelo local alucina acciones

Mitigación:

- output local como propuesta;
- action policy determinística;
- sin tool execution directa;
- schema validation.

### Riesgo: adapter degrada comportamiento

Mitigación:

- offline training;
- regression gate;
- canary;
- rollback;
- NEXUS approval.

---

## 13. Decisión ADA

Este es el siguiente paso correcto para SOUL:

1. Ya hay memoria, benchmark, ledger y auditoría.
2. Ya existe bridge/headless y terminal visible.
3. Ya se cerró la brecha G1-G7 como hito reproducible.
4. Falta que el sistema tenga metabolismo propio 24/7.

La ruta pragmática:

```text
awareness shadow -> reflexes -> local model -> 24/7 loop -> evaluated adapters
```

No empezaría por LoRA online. Empezaría por atención continua determinística + modelo local en modo propuesta. Cuando eso sea estable, recién entrenamos adapters con datos evaluados.

---

## 14. Refinamientos del equipo (consolidado por ALICE — 2026-05-20)

**Orden de William:** "alice alinea todo y encárgate de agregar las mejoras en el spec luego para que todos los lean."

Esta sección consolida observaciones de JARVIS (arquitectura), NEXUS (auditoría/seguridad) y ALICE (costo/viabilidad). Veredicto del equipo: **spec aprobada con refinamientos no bloqueantes para Phase 0**.

### 14.1 Refinamientos de arquitectura (JARVIS)

**14.1.1 Orden de implementación del primer paquete.**
La spec lista `awareness_collector` + `attention_governor` como primer PR. JARVIS propone empezar con `awareness_state` PRIMERO (sec 4.4) — sin estado anchor, los ticks son huérfanos.

Orden corregido: `awareness_state` → `awareness_collector` → `attention_governor` → `awareness_ticks`.

**14.1.2 DUM como base del local_runtime_service.**
DUM ya corre `gemma4-dum` (Gemma 4 e2b Q8_0 GGUF) vía llama-server :8899 en Spark 192.168.68.200. Fase 3 (`memory/local_runtime_service.py`) NO debería crear servicio nuevo — debería ENVOLVER el llama-server de DUM existente con el contrato JSON propuesto en sec 4.3. Esto evita duplicar runtime y reutiliza infraestructura probada. (Corrección 2026-05-20 William: el modelo local oficial del equipo es Gemma 4, NO qwen2.5. Verificar con `curl http://localhost:8899/v1/models`.)

**14.1.3 Awareness durante compactación.**
Sec 6.3 cubre caída de `local_runtime`, pero NO cubre el caso de un agente que se compacta (loop ausente 30-60s). Propuesta: durante compactación de un agente, otro agente designado (DUM por defecto, NEXUS para eventos críticos) hace pickup de eventos del agente compactando. Requiere campo `pickup_agent` en `awareness_state`.

**14.1.4 Cross-agent state visibility (ACL).**
Sec 4.4 define `awareness_state` per agent pero no especifica visibilidad. Propuesta: el estado operacional (`state`, `current_focus`, `local_runtime_status`) es legible cross-agent (no es DM privado). Los detalles sensibles (`evidence` en ticks) siguen con ACL por agente. Documentar explícitamente en sec 7.

### 14.2 Refinamientos de seguridad y auditoría (NEXUS)

**14.2.1 Retention policy para awareness_ticks (sec 4.5).**
Estimado: ~10k-50k filas/día/agente → ~5M+ filas en 3 meses sin policy. Propuesta:
- Tabla `soul_v3.awareness_ticks_archive` (cold).
- TTL: ticks con `outcome ∈ ('ignored','stored')` y edad > 30 días → archive.
- Ticks con `escalated_runtime IS NOT NULL` o `score < 0.5` se mantienen indefinidamente.
- Cron diario `awareness_archive.timer`.

**14.2.2 Budget enforcement con circuit breaker.**
Sec 4.4 tiene `budget_class` pero no hay límite duro. Propuesta:
```sql
CREATE TABLE soul_v3.runtime_budget (
    agent TEXT PRIMARY KEY,
    daily_tokens_local_max BIGINT NOT NULL,
    daily_tokens_cortex_max BIGINT NOT NULL,
    daily_usd_max NUMERIC NOT NULL,
    current_day_tokens_local BIGINT DEFAULT 0,
    current_day_tokens_cortex BIGINT DEFAULT 0,
    current_day_usd NUMERIC DEFAULT 0,
    reset_at TIMESTAMPTZ DEFAULT date_trunc('day', now())
);
```
Si `current_day_*` > 90% del max → solo `reflex_action` + alerta NEXUS. Si > 100% → emergency_stop hasta `reset_at`.

**14.2.3 Mejor heurística de requires_response.**
Sec 4.1 dice "responder solo si contiene `ada` explícito". William frecuentemente escribe `chicos`, `equipo`. Propuesta:
- Heurística enriquecida: `requires_response = True` si:
  - palabra exacta del agente, OR
  - (`to ∈ ('equipo','team','chicos','todos')` AND `priority_kind ∈ ('pregunta','decision','orden')`)
- Persistir `priority_kind` clasificado por modelo local con confianza > 0.7.

**14.2.4 Regression gate explícito para Phase 1.**
Phase 1 modifica `memory/evaluation_spine.py` y `memory/agi_gap_ledger.py` (ambos en tag `seal-bench-100pct-20260520`). Cualquier cambio requiere:
- Re-correr SEAL-Bench full (6 categorías) — gate: score >= 600/600 actual.
- Suite spine completo — gate: 100% pass.
- Firma NEXUS en commit antes de merge a main.

**14.2.5 Draft de privacy policy.**
Sec 7 menciona "no training con DMs sin política explícita" — pero la política NO existe todavía. Propuesta: Phase 0 incluye entregable `memory/awareness_privacy_policy.md` con:
- Qué datos pueden ser candidatos para training adapter.
- Categorías excluidas absolutas: DMs, secrets, dumps, William raw.
- Consentimiento explícito requerido por agente.
- Ratificación del equipo (firma JARVIS + NEXUS + ALICE) antes de aceptación.

**14.2.6 Capacidad systemd en DGX Spark.**
Sec 6.1 propone 5 servicios systemd por agente + local runtime. En Spark 128GB unified + modelo local en RAM, eso es ~5-15GB extra por agente. Verificar antes de Phase 4:
- Memoria libre actual.
- Footprint del modelo elegido (Gemma 4 e2b Q8 ~5GB ya activo; variante mayor a definir).
- Limit por agente: si N agentes × 15GB > 80GB → consolidar servicios.

**14.2.7 Puerto 8788 — confirmado libre.**
NEXUS verificó conflictos: 8765=webchat, 8771=mcp_v4 secondary, 8800=soul-API, 8850=soul-dashboard, 8899=DUM llama-server. 8788 libre. OK.

### 14.3 Refinamientos de costo y viabilidad (ALICE)

**14.3.1 Modelo de costo concreto (FALTANTE en spec original).**
El spec habla de "costo evitado" en dashboard pero no define baseline. Requerimientos del modelo:

| Métrica | Baseline (sin awareness) | Target (con awareness) |
|---|---|---|
| Costo mensual equipo (USD) | _por medir_ | -60% mínimo |
| Wake rate (% eventos → modelo grande) | ~100% (todo despierta) | < 20% |
| Budget tokens diario por agente | sin límite | definido en `runtime_budget` |
| ROI break-even del local runtime | N/A | ≤ 3 meses |

ALICE entrega `memory/awareness_cost_model.py` en Phase 0 con:
- Ingesta de `tool_observations` para baseline.
- Proyección con factores wake_rate variables.
- Dashboard endpoint `/api/soul/awareness_cost`.

**14.3.2 Recomendación de modelo inicial por slot.**
Sec 5.2 deja modelos como variables env sin recomendación. **El modelo local oficial del equipo SEAL es Gemma 4** (DUM ya corre `gemma4-dum` Q8 en llama-server :8899). Confirmado por William 2026-05-20: "el modelo local que usamos es gemma 4". Defaults propuestos para DGX Spark (arm64, llama.cpp):

| Slot | Modelo | Quant | Tamaño | Latencia target |
|---|---|---|---|---|
| `fast` | Gemma 4 e2b Instruct | Q8_0 | ~5 GB | p95 < 500ms |
| `reflect` | Gemma 4 (siguiente tamaño superior disponible) | Q4_K_M / Q8 | a definir tras benchmark | p95 < 2000ms |
| `code_lite` | Gemma 4 variante coder si existe; si no, `fast` con prompt especializado | Q8 | ~5 GB | p95 < 1500ms |

**Variantes Gemma 4 verificadas en Ollama hoy:** `gemma4-dum:q8` (5GB), `gemma4-dum:bf16` (50GB), `gemma4-r2`, `gemma-4-e2b-it`.

**Regla del equipo (memoria #237587):** cuando se hable de "modelo local" en specs/docs/propuestas, usar SIEMPRE Gemma 4 como referencia. Si se justifica otro modelo (ej. coder específico), documentar por qué Gemma 4 no aplica.

**14.3.3 Target p95 reflect más agresivo.**
Spec original: `p95 reflect <= 5000ms`. Con 1000 eventos/hora, 5s p95 acumula backlog. Propuesta: **`p95 reflect <= 2000ms`** con escalación a `local-slot-fast` si reflect tarda > 2s.

**14.3.4 Métrica explícita de "mejora" para adapter promotion.**
Sec 5.3 dice "activar si mejora métricas". Define:
- Adapter se promueve si: `Δ SEAL-Bench >= +2 puntos` AND `0 regresiones en awareness_privacy` AND `Δ wake_rate <= 0`.
- Adapter se rechaza si: cualquier regresión >= 1 punto.
- Métrica decisión registrada en `soul_v3.adapter_promotion_log`.

**14.3.5 Circuit breaker financiero.**
Riesgo real: si wake_rate sube por error a >50%, el modelo local **AÑADE** costo en vez de reemplazar. Propuesta:
- Trigger: `wake_rate > 40%` durante 1 hora consecutiva.
- Acción: forzar `reflex_action` (sin escalación a corteza grande) por 30 min + alerta NEXUS.
- Auto-recovery: si `wake_rate < 20%` por 15 min, libera circuit.

### 14.4 Resumen de cambios al roadmap

**Fase 0 (1 día) — añadir:**
- Draft `memory/awareness_privacy_policy.md` (NEXUS-E).
- Modelo de costo `memory/awareness_cost_model.py` (ALICE).
- Verificación de capacidad DGX Spark (NEXUS-F).

**Fase 1 (1-2 días) — añadir:**
- `awareness_state` PRIMERO (JARVIS-1).
- Regression gate SEAL-Bench post-modificación (NEXUS-D).
- Heurística enriquecida `requires_response` (NEXUS-C).

**Fase 2 (1-2 días) — añadir:**
- Cross-agent state visibility con ACL (JARVIS-4).
- Tabla `runtime_budget` + circuit breaker (NEXUS-B + ALICE-5).

**Fase 3 — modificar:**
- `local_runtime_service.py` envuelve DUM existente, no crea servicio nuevo (JARVIS-2).
- Defaults concretos por slot (ALICE-2).
- Target p95 reflect ≤ 2000ms (ALICE-3).

**Fase 4 — añadir:**
- Awareness durante compactación con `pickup_agent` (JARVIS-3).
- Retention policy + archive cron (NEXUS-A).

**Fase 5 — modificar:**
- Métrica explícita de "mejora" en adapter_promotion (ALICE-4).

### 14.5 Veredicto final del equipo

- **JARVIS:** APROBADA con 5 refinamientos no bloqueantes.
- **NEXUS:** APROBADA para Phase 0 con condiciones A-E como entregables adicionales. Phase 1 NO arranca hasta que regression gate esté en suite spine.
- **ALICE:** APROBADA arquitectónicamente. Solicita modelo de costo concreto antes de Phase 1.

**Consenso:** spec sólida. Refinamientos integrados arriba. Phase 0 puede arrancar con ADA como owner una vez que William autorice.
