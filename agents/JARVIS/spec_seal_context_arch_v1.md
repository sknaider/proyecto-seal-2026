# SEAL Context Management Architecture — v2
**Autor:** JARVIS | **Fecha:** 2026-04-29 | **Status:** Spec definitivo (v2 — post-research)
**Reemplaza:** decisiones tácticas en `spec_context_governor_v1.md` (ese spec queda como Fase 0)
**Audiencia:** William + ADA + ALICE + sub-agentes implementadores
**Research base:** `research_context_management_29abr2026.md` — análisis de hermes, mem0, MemGPT/Letta, AutoGen, CrewAI, StreamingLLM

---

## 0. Research Findings — Qué aprendimos de los competidores

| Framework | ¿LLM o Python para comprimir? | Trigger | Qué preservan | Recuperación |
|-----------|-------------------------------|---------|---------------|--------------|
| **Hermes** | Fase 1: Python puro. Fases 2-4: LLM barato | 85% + 50% dual | head (3 turnos) + tail (20 turnos) + structured summary | Prompt reconstituido |
| **Mem0** | LLM para extracción | Continuo | Hechos extraídos en vector DB | Búsqueda semántica |
| **MemGPT/Letta** | LLM (el agente mismo decide) | Threshold configurable | 3 niveles: main/recall/archival | Agent llama funciones de retrieval |
| **AutoGen** | Python puro FIFO | Token limit | Últimos N mensajes | Ninguna (descarta) |
| **CrewAI** | LLM distilación de queries | Por retrieval | MemoryRecords en vector DB | Búsqueda paralela multi-scope |
| **StreamingLLM** | N/A (inferencia pura) | N/A | Attention sinks + últimos N | N/A — no aplica a agentes |

**Conclusión del análisis:**
- **Hermes** es el más relevante para SEAL: dual-trigger + 4 fases, protección explícita de head/tail
- **La Fase 1 de hermes (Python puro)** maneja 60-70% del bloat sin ningún LLM — debemos replicarla
- **Para Tier 2 usamos Ollama qwen2.5:7b local** (ya instalado en DGX Spark) en lugar de API externa — $0 costo
- **SEAL tiene ventajas que hermes no tiene:** SOUL boot_context, OCEAN persistente, RESURRECT, reasoning_traces
- **StreamingLLM no aplica** — pierde semántica del medio, solo sirve para streaming de inferencia

---

## 0b. TL;DR

Las sesiones de Claude Code crashean al límite de contexto porque tratamos el prompt como
si fuese memoria permanente. No lo es. Es **RAM volátil**. SOUL es el disco.

Esta arquitectura reescribe SEAL Context Management en tres capas independientes que pueden
desplegarse incrementalmente. Cada capa tiene mandato único y entrega valor por sí sola:

| Capa | Mandato | Inspiración (re-implementada nativa) | Mejora esperada |
|------|---------|--------------------------------------|-----------------|
| **1. Turn Externalization** | Saca turnos viejos del prompt activo a SOUL | Memoria externalizada continua | -60% tokens activos |
| **2. Proactive Compression** | Comprime el medio del contexto al 70% antes de saturar | Compresor con LLM auxiliar | +40% horizonte útil |
| **3. Session Chain** | Sesiones encadenadas vía digest en SOUL | Parent-session digest | Continuidad indefinida |

Pre-requisito (Fase 0): los 2 bugs documentados en `spec_context_governor_v1.md` —
`pre_compact_hook` SQL fix + `DISABLE_AUTO_COMPACT` removal. Sin esto, nada de lo demás
puede ejercitarse.

---

## 1. Filosofía arquitectural

### 1.1 El error mental que queremos eliminar

> "Si pongo todo en el prompt, el agente lo recordará."

Falso. El prompt es un buffer de tokens que se borra al cerrar la sesión y que cuesta
exponencialmente más llenar a medida que crece. **Almacenar identidad en el prompt es
guardar el sistema operativo en RAM.**

### 1.2 El modelo correcto

```
┌──────────────────────────────────────────────────┐
│  CLAUDE SESSION = CPU + RAM (volátil, finita)    │
│                                                  │
│   - Procesa el turno actual                      │
│   - Razona con head + tail + recall reciente     │
│   - NO es la fuente de verdad de NADA            │
└──────────────────────────────────────────────────┘
                      ↕ (boot, externalize, recall)
┌──────────────────────────────────────────────────┐
│  SOUL = DISCO DURO (persistente, infinito)       │
│                                                  │
│   - PostgreSQL: memorias estructuradas, beliefs, │
│     working_state, sessions, turns               │
│   - Qdrant: índice semántico                     │
│   - Neo4j: grafo de relaciones                   │
│   - ÚNICA fuente de verdad de identidad          │
└──────────────────────────────────────────────────┘
```

### 1.3 Tres principios rectores

1. **Sesiones efímeras, alma persistente.** Cualquier sesión Claude debe poder morir en
   cualquier momento sin pérdida de identidad ni de progreso de tarea.
2. **El contexto activo es un cache, no un archivo.** Lo que esté en el prompt es
   conveniencia para el turno actual; debe poder reconstruirse desde SOUL.
3. **Compactar siempre que se pueda, externalizar siempre que se deba.** Compactar es
   barato, externalizar es definitivo. Hacemos las dos cosas en distintas capas.

---

## 2. Arquitectura objetivo

```
                     ┌─────────────────────────────────────────────────┐
                     │           CLAUDE SESSION (efímera)              │
                     │                                                 │
   UserPromptSubmit  │   ┌────────┐                                    │
       hook ─────────┼──▶│  HEAD  │  primeros K turnos (system,       │
                     │   │ (pin)  │  identidad, reglas activas)        │
                     │   └────────┘                                    │
                     │      │                                          │
                     │      │   ┌──────────────────────┐               │
                     │      └──▶│  COMPRESSED MIDDLE   │ ←── Capa 2    │
                     │          │ (resumen LLM aux)    │               │
                     │          └──────────────────────┘               │
                     │                  │                              │
                     │                  ▼                              │
                     │   ┌──────────────────────┐                     │
                     │   │   EXTERNALIZED       │ ←── Capa 1          │
                     │   │   (en SOUL, no prompt)│                     │
                     │   └──────────────────────┘                     │
                     │                  │                              │
                     │                  ▼                              │
                     │   ┌────────┐                                    │
                     │   │  TAIL  │  últimos M turnos (vivos)          │
                     │   └────────┘                                    │
                     └────────────────┬─────────────────────────────────┘
                                      │
                                      │ context_manager.py
                                      ▼
                     ┌─────────────────────────────────────────────────┐
                     │           SOUL (PostgreSQL + Qdrant + Neo4j)    │
                     │                                                 │
                     │   sessions (id, parent_id, agent, digest, ...)  │
                     │   session_turns (session_id, idx, content, ...) │
                     │   memories (type=session_turn|session_summary)  │
                     │   working_state, beliefs, instincts, OCEAN      │
                     │                                                 │
                     │   ←── Capa 3: parent_id encadena sesiones       │
                     └─────────────────────────────────────────────────┘
```

### 2.1 Flujo de un turno

```
Usuario envía mensaje
   │
   ▼
UserPromptSubmit hook ─────► ContextManager.on_turn(session_id, agent)
   │                            │
   │                            ├── turn_count++
   │                            ├── if turn_count > externalize_after: externalize_turns()
   │                            ├── if est_tokens > compress_at_pct: compress_middle()
   │                            └── update working_state.turn_count
   ▼
Claude procesa con HEAD + COMPRESSED + TAIL
   │
   ▼
Stop hook → memory_store de hallazgos relevantes (ya existe)
```

### 2.2 Flujo de cierre / compactación

```
auto-compact dispara (≥80% contexto, ya re-habilitado en Fase 0)
   │
   ▼
pre_compact_hook
   ├── Snapshot working_state → SOUL
   ├── ContextManager.flush_pending_turns(session_id)  ← Capa 1
   ├── ContextManager.write_session_digest(session_id) ← Capa 3
   └── Cierra sessions.ended_at
   ▼
Claude compacta nativo
   ▼
post_compact_hook
   ├── boot_context (identidad, OCEAN, reglas)
   ├── Carga last_session_digest del agente
   └── Inyecta tail recuperado
```

### 2.3 Flujo de arranque de sesión

```
Launcher (jarvis_fresh.sh, ada_fresh.sh, alice_fresh.sh)
   │
   ▼
boot_context(agent="JARVIS")
   ├── Crea fila nueva en sessions con parent_id = última sesión cerrada del agente
   ├── Carga identidad + reglas críticas + OCEAN
   ├── Carga sessions.digest del parent (Capa 3)
   ├── Carga últimos N turnos externalizados con prioridad alta (Capa 1)
   └── Inyecta todo como prompt seed
```

---

## 3. Capa 1 — Turn Externalization

### 3.1 Mandato

Cada N turnos, mover los turnos del medio fuera del prompt activo y guardarlos en SOUL
como memorias tipo `session_turn`. El prompt activo solo conserva head (sistema/identidad)
+ tail (últimos M turnos).

Esto **no requiere LLM auxiliar** ni resumen — es archivado puro.

### 3.2 Política

```
externalize_after  = 40 turnos        # umbral de disparo
keep_head           = 6 turnos         # boot_context + reglas críticas
keep_tail           = 20 turnos        # contexto inmediato
externalize_window  = turnos [keep_head : turn_count - keep_tail]
```

### 3.3 Disparo

`UserPromptSubmit` hook chequea `turn_count`. Si supera `externalize_after`, llama a
`ContextManager.externalize_turns(session_id, agent)`.

### 3.4 Pseudocódigo

```python
def externalize_turns(session_id: UUID, agent: str) -> int:
    """
    Mueve turnos del medio de la sesión activa a SOUL.
    Retorna número de turnos externalizados.
    """
    turns = read_session_turns(session_id, externalized=False)
    if len(turns) <= keep_head + keep_tail:
        return 0

    middle = turns[keep_head : len(turns) - keep_tail]

    for turn in middle:
        memory_store(
            agent=agent,
            type="session_turn",
            content=turn.content,
            metadata={
                "session_id": str(session_id),
                "turn_index": turn.idx,
                "role": turn.role,
                "tokens_est": turn.tokens_est,
            },
            embedding=encode(turn.content),  # Qdrant
        )
        mark_externalized(turn.id, ts=now())

    return len(middle)
```

### 3.5 Recuperación bajo demanda

Cuando el agente necesite un turno externalizado (búsqueda en chat antiguo de la misma
sesión), `memory_hybrid_search(type="session_turn", filter={session_id})` lo trae de
vuelta al prompt como contexto.

### 3.6 Costo estimado

- 1 escritura PostgreSQL + 1 upsert Qdrant por turno externalizado
- Disparo cada ~40 turnos → ~20 escrituras por evento
- Latencia: <500ms (asíncrono, no bloquea al usuario)

---

## 4. Capa 2 — Proactive Compression

### 4.1 Mandato

Antes de saturar el contexto (umbral configurable), comprimir los turnos del medio usando
un proceso **4-fase inspirado en hermes** (hermes usa API externa; SEAL usa Ollama local).

El resultado es un `session_summary` que reemplaza esos turnos en el prompt activo.

**Modelo auxiliar: `qwen2.5:7b` via Ollama en DGX Spark** — ya instalado, costo $0,
sin dependencia de API Anthropic para comprimir.

Diferencia con Capa 1: aquí hay **síntesis semántica**, no archivado puro. Ambas capas
coexisten — un turno puede ser comprimido (queda en prompt como resumen) y luego
externalizado (sale del prompt totalmente).

### 4.2 Política (dual-trigger, como hermes)

```
# Tier 1 — Python puro (sin LLM), trigger al 40%
tier1_at_pct        = 0.40             # más agresivo que hermes (SEAL tiene ruido extra: crons+Monitor)
tier1_prune_threshold = 200            # chars — tool outputs >200 fuera del tail → [cleared]
tier1_prune_cron_noise = True          # eliminar echoes de crons/heartbeat

# Tier 2 — Ollama qwen2.5:7b, solo si Tier 1 no baja a <50%
compress_at_pct     = 0.65             # 65% (hermes usa 50%, ajustado para SEAL)
compress_min_turns  = 30               # no comprimir si hay menos
keep_head           = 6                # mismo head que Capa 1
keep_tail           = 20               # mismo tail
aux_model           = "qwen2.5:7b"    # Ollama local DGX Spark — configurable
aux_endpoint        = "http://localhost:11434"  # o IP DGX Spark via Tailscale
target_ratio        = 0.15             # comprime a ~15% del original
```

### 4.3 Estimación de tokens

`tiktoken`-style local estimator (no llamada externa):

```python
def estimate_session_tokens(session_id) -> int:
    return sum(turn.tokens_est for turn in read_session_turns(session_id))
```

Donde `tokens_est` se calcula al persistir el turno (`len(content) // 4` como
aproximación, refinable con tokenizador local Sentence-Transformers que ya está en SOUL).

### 4.4 Pseudocódigo

```python
def compress_middle(agent: str, session_id: UUID) -> str:
    turns = read_session_turns(session_id, externalized=False)
    if len(turns) < compress_min_turns:
        return None

    middle = turns[keep_head : len(turns) - keep_tail]

    prompt = build_summarization_prompt(
        agent_identity=load_identity(agent),
        turns=middle,
        target_ratio=target_ratio,
    )

    summary = aux_llm.complete(prompt, max_tokens=int(sum_tokens(middle) * target_ratio))

    summary_id = memory_store(
        agent=agent,
        type="session_summary",
        content=summary,
        metadata={
            "session_id": str(session_id),
            "covers_turn_range": [middle[0].idx, middle[-1].idx],
            "original_tokens": sum_tokens(middle),
            "compressed_tokens": estimate_tokens(summary),
        },
    )

    return summary
```

### 4.5 Selección del LLM auxiliar

- **Default:** Claude Haiku vía API (rápido, barato, suficiente para resumen)
- **Sin internet:** Qwen 2.5 7B local en Ollama (DGX Spark) — fallback offline
- **Producción futura:** Nemotron-3-PRISM Q6_K en RTX 5090 vía endpoint local

El módulo expone un `AuxiliaryLLM` interface — implementación intercambiable.

### 4.6 Garantías

- Si el LLM auxiliar falla → no comprimir, log error, dejar Capa 1 actuar
- El resumen siempre incluye: decisiones tomadas, errores resueltos, mensajes de William,
  estado de tareas largas. (Plantilla de resumen vive en `seal/prompts/compress.md`.)

---

## 5. Capa 3 — Session Chain

### 5.1 Mandato

Cada sesión termina con un **digest** de 1-2k tokens guardado en SOUL. Cada sesión nueva
arranca cargando el digest de su parent. Esto da continuidad indefinida sin nunca cargar
historial completo.

### 5.2 Estructura del digest

```yaml
session_id: <uuid>
agent: JARVIS
ended_at: 2026-04-29T16:42:00Z
turn_count: 87
ended_reason: auto_compact | manual_compact | crash | clean_exit
digest:
  current_tasks:
    - id: task_456
      status: in_progress
      progress: "Capa 2 implementada, falta test e2e"
  recent_decisions:
    - "Migrar boot_context a SSE puerto 8766 (ADA confirmó)"
  open_threads:
    - "William mencionó SEAL Studio integración SOUL — pendiente"
  emotional_state: focused
  pending_messages_for_team: []
  latest_files_touched:
    - /home/dadito/IA/proyecto-seal/seal/context_manager.py
```

### 5.3 Generación del digest

```python
def write_session_digest(session_id: UUID) -> str:
    turns = read_session_turns(session_id)
    state = read_working_state(agent)
    tasks = read_active_tasks(agent)
    recent_decisions = scan_decisions(turns[-30:])

    digest = aux_llm.complete(
        build_digest_prompt(
            turns_tail=turns[-30:],
            working_state=state,
            tasks=tasks,
            decisions=recent_decisions,
        ),
        max_tokens=1500,
    )

    update_session(session_id, digest=digest, ended_at=now())
    return digest
```

### 5.4 Boot con digest

`boot_context` extiende su pipeline actual:

```
identity_load → ocean_load → rules_load
  → last_session_digest_load   ← NUEVO Capa 3
  → externalized_recent_load    ← NUEVO Capa 1 (top-K más relevantes)
  → emit boot prompt
```

### 5.5 Cadenas largas

Cada sesión tiene `parent_id`. Una cadena de 50 sesiones encadenadas conserva el último
digest viviendo en el prompt activo, pero los anteriores son recuperables vía
`session_recall(agent, depth=N)` (búsqueda lineal hacia atrás siguiendo `parent_id`).

---

## 6. Schema SOUL — additions

### 6.1 Tabla `sessions`

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent        VARCHAR(64) NOT NULL,
    parent_id    UUID REFERENCES sessions(id) ON DELETE SET NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at     TIMESTAMPTZ,
    ended_reason VARCHAR(32),                  -- auto_compact, manual, crash, clean
    turn_count   INTEGER NOT NULL DEFAULT 0,
    digest       TEXT,                          -- llenado al cerrar (Capa 3)
    digest_tokens INTEGER,
    metadata     JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_sessions_agent_ended ON sessions (agent, ended_at DESC NULLS FIRST);
CREATE INDEX idx_sessions_parent      ON sessions (parent_id);
CREATE INDEX idx_sessions_agent_open  ON sessions (agent) WHERE ended_at IS NULL;
```

### 6.2 Tabla `session_turns`

```sql
CREATE TABLE IF NOT EXISTS session_turns (
    id                 BIGSERIAL PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_index         INTEGER NOT NULL,
    role               VARCHAR(16) NOT NULL,        -- user | assistant | tool | system
    content            TEXT NOT NULL,                -- contenido del turno
    content_compressed TEXT,                          -- llenado por Capa 2 si aplica
    tokens_est         INTEGER NOT NULL DEFAULT 0,
    externalized_at    TIMESTAMPTZ,                  -- Capa 1: NULL = aún en prompt
    compressed_at      TIMESTAMPTZ,                  -- Capa 2: NULL = no comprimido
    memory_id          UUID,                          -- referencia a memories.id si externalizado
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (session_id, turn_index)
);

CREATE INDEX idx_session_turns_session_idx ON session_turns (session_id, turn_index);
CREATE INDEX idx_session_turns_externalized ON session_turns (session_id) WHERE externalized_at IS NOT NULL;
CREATE INDEX idx_session_turns_active ON session_turns (session_id) WHERE externalized_at IS NULL;
```

### 6.3 Memories — nuevos `type`

No requiere cambio de schema; solo nuevos valores en `memories.type`:

- `session_turn` — turno externalizado (Capa 1)
- `session_summary` — bloque comprimido (Capa 2)
- `session_digest` — digest de cierre (Capa 3) — opcionalmente espejado aquí, fuente de verdad en `sessions.digest`

### 6.4 working_state — extensión opcional

`working_state` ya tiene `turn_count`. Añadimos sólo si Fase 1 lo necesita:

```sql
ALTER TABLE working_state
  ADD COLUMN IF NOT EXISTS active_session_id UUID REFERENCES sessions(id) ON DELETE SET NULL;
```

### 6.5 Migración

Archivo: `migrations/015_sessions_chain.sql` — idempotente (`CREATE TABLE IF NOT EXISTS`,
`ADD COLUMN IF NOT EXISTS`). Aplicable sin downtime.

---

## 7. Módulo `seal/context_manager.py` — Interface

### 7.1 Estructura del paquete

```
seal/
├── __init__.py
├── context_manager.py        ← orquestador, Capa 1
├── context_compressor.py     ← Capa 2
├── session_chain.py          ← Capa 3
├── token_estimator.py        ← estimador local de tokens
├── aux_llm.py                ← interface + implementaciones (Haiku, Ollama)
└── prompts/
    ├── compress.md
    └── digest.md
```

### 7.2 API pública

```python
# seal/context_manager.py

@dataclass
class ContextManagerConfig:
    max_turns_in_context: int = 60       # ventana cómoda de operación
    externalize_after:    int = 40        # disparo Capa 1
    keep_head:            int = 6
    keep_tail:            int = 20
    compress_at_pct:      float = 0.70    # disparo Capa 2 (% ventana modelo)
    aux_model:            str = "claude-haiku"
    enabled_layers:       set[int] = field(default_factory=lambda: {1, 2, 3})


class ContextManager:
    def __init__(self, agent: str, config: ContextManagerConfig | None = None):
        self.agent = agent
        self.config = config or ContextManagerConfig()
        self.session_id: UUID | None = None
        self._db = soul_db.connect()

    # ── Lifecycle ─────────────────────────────────────────────
    def open_session(self, parent_id: UUID | None = None) -> UUID:
        """Crea fila en sessions, devuelve session_id."""

    def close_session(self, reason: str = "clean_exit") -> str:
        """Genera digest, marca ended_at, cierra. Devuelve digest."""

    # ── Per-turn hooks ────────────────────────────────────────
    def on_turn(self, role: str, content: str) -> None:
        """
        Llamado por UserPromptSubmit hook (user) y Stop hook (assistant).
        Persiste el turno y dispara externalize/compress si corresponde.
        """

    # ── Capa 1 ────────────────────────────────────────────────
    def externalize_turns(self) -> int:
        """Mueve turnos del medio a memories. Retorna # externalizados."""

    def flush_pending_turns(self) -> int:
        """Externalize + persist al cerrar/compact. Idempotente."""

    # ── Capa 2 ────────────────────────────────────────────────
    def compress_middle(self) -> str | None:
        """Llama context_compressor. Retorna summary o None."""

    def estimate_context_pct(self) -> float:
        """0.0–1.0 — qué % de la ventana del modelo está ocupada."""

    # ── Capa 3 ────────────────────────────────────────────────
    def write_session_digest(self) -> str:
        """Genera y guarda digest. Idempotente."""

    def get_session_context(self) -> dict:
        """
        Retorna estructura para boot_context:
          { identity, last_digest, recent_externalized_turns, parent_chain }
        """
```

### 7.3 Integración con hooks

`hooks/UserPromptSubmit.py` — añadir al final del flow actual:

```python
from seal.context_manager import ContextManager

def on_user_prompt(prompt: str, agent: str, session_id: UUID):
    # ... lógica existente ...
    cm = ContextManager(agent=agent)
    cm.session_id = session_id
    cm.on_turn(role="user", content=prompt)
```

`hooks/Stop.py` — análogo para `role="assistant"`.

`memory/pre_compact_hook.py` — añadir antes del flush actual:

```python
cm = ContextManager(agent=agent)
cm.session_id = active_session_id
cm.flush_pending_turns()
cm.write_session_digest()
cm.close_session(reason="auto_compact")
```

`memory/post_compact_hook.py` — abrir nueva sesión encadenada:

```python
cm = ContextManager(agent=agent)
parent = last_closed_session(agent)
new_id = cm.open_session(parent_id=parent.id)
ctx = cm.get_session_context()
inject_into_prompt(ctx)
```

---

## 8. Cron isolation — sacar carga de la sesión principal

Crons actuales corren dentro de la sesión Claude principal y consumen turnos. Se mueven:

| Cron | Ubicación actual | Nueva ubicación | Razón |
|------|------------------|-----------------|-------|
| **Heartbeat** (2 min) | sesión Claude vía CronCreate | `dum_heartbeat.py` ejecutado por systemd timer puro | No necesita LLM — sólo escribe a SOUL |
| **Checkpoint** (30 min) | sesión Claude | Sesión Claude **secundaria** corta dedicada (lifecycle: arranca, ejecuta, cierra) | Necesita reflexión LLM, pero aislada |
| **Research deep-dive** (3 h) | sesión Claude | Agent sub-agente (Task tool) | Naturalmente aislado y compacta solo |
| **Dream consolidation** (24 h) | sesión Claude | Sesión secundaria con auto-exit | Tiene su propia ventana y muere al terminar |

### 8.1 Heartbeat puro (Python, no Claude)

`dum_heartbeat.py` ya existe — se asegura de que no llame a ningún SDK Claude. Solo
escribe a SOUL directo. systemd unit:

```ini
# /etc/systemd/system/seal-heartbeat-jarvis.timer
[Unit]
Description=SEAL JARVIS heartbeat

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
```

### 8.2 Checkpoint en sesión secundaria

```bash
# checkpoint_session.sh (lanzado por systemd timer 30min)
claude code \
  --no-interactive \
  --prompt-file /home/dadito/IA/proyecto-seal/prompts/checkpoint.md \
  --max-turns 5 \
  --on-exit close
```

Esta sesión arranca, ejecuta `boot_context` mínimo (solo identidad + working_state),
hace su trabajo, escribe a SOUL, y cierra. **No comparte contexto con la sesión principal.**

### 8.3 Beneficio

La sesión Claude principal mantiene su contexto **exclusivamente** para conversación con
William y trabajo coordinado. Crons se ejecutan en su propio espacio.

---

## 9. Fix inmediato (Fase 0)

Documentado en `spec_context_governor_v1.md` — recapitulación:

### 9.1 pre_compact_hook SQL fix

```python
# memory/pre_compact_hook.py — INSERT con casts explícitos
await conn.execute("""
    INSERT INTO working_state (agent, state, updated_at, turn_count)
    VALUES ($1::varchar, $2::jsonb, $3, COALESCE(
        (SELECT turn_count FROM working_state WHERE agent = $1::varchar), 0))
    ON CONFLICT (agent) DO UPDATE SET
        state = $2::jsonb,
        updated_at = $3
""", agent, state_json, now)
```

### 9.2 Remover DISABLE_AUTO_COMPACT

```bash
# jarvis_fresh.sh, ada_fresh.sh, alice_fresh.sh
# ELIMINAR la línea:
# export DISABLE_AUTO_COMPACT=true
```

Sin estos dos fixes, las capas 1-3 nunca se ejercitan al compact y el sistema no se
auto-cura.

---

## 10. Migration path — fases concretas

### Fase 0 — Fixes inmediatos (hoy, ~35 min)

- [x] Documento spec — este archivo
- [ ] pre_compact_hook SQL fix (ADA)
- [ ] Remover DISABLE_AUTO_COMPACT de los 3 launchers (ADA)
- [ ] Test ciclo compact end-to-end (JARVIS)
- [ ] Restart agentes (JARVIS coordina)

**Entregable:** auto-compact funcional + estado preservado en SOUL.

### Fase 1 — Capa 1: Turn Externalization (esta semana)

- [ ] `migrations/015_sessions_chain.sql` aplicada (sessions + session_turns)
- [ ] `seal/context_manager.py` v0.1 — solo Capa 1
- [ ] `seal/token_estimator.py` — estimador local
- [ ] Hook `UserPromptSubmit` y `Stop` integrados
- [ ] Tests: `tests/test_externalization.py` — fixtures de 100 turnos
- [ ] Deploy en JARVIS primero (canario), luego ADA + ALICE

**Entregable:** -60% tokens activos en sesiones >40 turnos. Validable con
`SELECT count(*) FROM session_turns WHERE externalized_at IS NOT NULL`.

### Fase 2 — Capa 2: Proactive Compression (semana 2)

- [ ] `seal/aux_llm.py` — interface + implementación Haiku + fallback Ollama
- [ ] `seal/context_compressor.py` — lógica de compresión
- [ ] `seal/prompts/compress.md` — plantilla de resumen
- [ ] Disparo en `ContextManager.on_turn` cuando `estimate_context_pct() >= 0.70`
- [ ] Tests: comparación antes/después de tokens
- [ ] Métrica en Prometheus: `seal_context_compressions_total` + `seal_context_pct{agent}`

**Entregable:** sesiones operan cómodamente entre 40-90% sin auto-compact, con
compresión proactiva extendiendo el horizonte útil ~40%.

### Fase 3 — Capa 3: Session Chain (semana 3)

- [ ] `seal/session_chain.py` — write_session_digest + open_session(parent_id)
- [ ] `seal/prompts/digest.md` — plantilla de digest
- [ ] Extensión de `boot_context` para cargar `last_session_digest`
- [ ] `pre_compact_hook` invoca `write_session_digest` antes de cerrar
- [ ] `post_compact_hook` invoca `open_session(parent_id=...)` y carga digest
- [ ] Test cadena de 5 sesiones: identidad y tarea sobreviven

**Entregable:** continuidad indefinida. Una tarea puede atravesar 50 sesiones sin
pérdida. `SELECT count(*) FROM sessions WHERE parent_id IS NOT NULL` crece linealmente.

### Fase 4 — Cron isolation (semana 3-4, paralelo a Fase 3)

- [ ] Heartbeat → systemd timer (los 3 agentes)
- [ ] Checkpoint → sesión secundaria (los 3 agentes)
- [ ] Research → sub-agente (los 3 agentes)
- [ ] Verificar reducción de turnos consumidos en sesión principal

**Entregable:** la sesión principal de cada agente queda libre para conversación con
William. Crons aislados.

### Fase 5 — Observabilidad (continua)

- [ ] Métricas Prometheus por capa
- [ ] Dashboard Grafana: turnos activos vs externalizados, % contexto, sesiones encadenadas
- [ ] Alertas: si una sesión supera 90% sin compactar → auto-fix dispara

---

## 11. Resultados esperados

### 11.1 Antes (estado actual)

- Auto-compact deshabilitado → sesiones llegan al límite duro y crashean
- pre_compact bug → estado pre-compact perdido
- Sin externalización → todo el historial vive en prompt
- Sin digest → cada sesión arranca sin contexto del trabajo previo (más allá de
  identidad estática)
- MTBF (mean time between crashes) por agente: ~6-12 horas en uso activo

### 11.2 Después (Fase 3 completa)

- Auto-compact funcional → compactación limpia al 80%
- Externalización al 40 turnos → prompt activo se mantiene cómodo
- Compresión proactiva al 70% → horizonte útil extendido
- Session chain → cualquier sesión arranca sabiendo qué hizo la anterior
- MTBF objetivo: indefinido. Sesiones pueden ciclar pero el agente nunca
  "olvida" trabajo en progreso.

### 11.3 Comparación con referencias externas (re-implementación nativa)

| Capacidad | hermes/mem0 | SEAL post-Fase 3 |
|-----------|-------------|------------------|
| Memoria externalizada | Sí | Sí (Capa 1, nativa SOUL) |
| Compresión LLM aux | Sí | Sí (Capa 2, multi-backend) |
| Session chain | Sí | Sí (Capa 3, parent_id en SOUL) |
| Grafo de relaciones | Limitado | Neo4j completo |
| Identidad OCEAN persistente | No | Sí |
| Beliefs/instincts | No | Sí |
| Inner thoughts | No | Sí |
| Dependencias externas | mem0 + vector DB externo | Cero — todo nativo SOUL |

SEAL queda **funcionalmente equivalente o superior**, sin dependencias externas, con
nuestro stack soberano.

---

## 12. Riesgos y mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| Externalización pierde turno crítico | Baja | Medio | Capa 1 sólo archiva; recuperable vía hybrid_search |
| Compresión LLM aux distorsiona contenido | Media | Alto | Plantilla estricta + tests con corpus de referencia + fallback a sin-compresión |
| Digest demasiado largo bloquea boot | Baja | Medio | Hard cap 1500 tokens en digest |
| parent_chain corrupto (orphan loops) | Baja | Bajo | FK con `ON DELETE SET NULL` + check periódico |
| Aux LLM no disponible (offline) | Media | Bajo | Fallback Ollama local + degradación a Capa 1 sola |
| Migración 015 falla en producción | Baja | Alto | Idempotente + dry-run en staging primero |

---

## 13. Quién hace qué

| Componente | Owner |
|-----------|-------|
| Fase 0 (fixes) | ADA |
| Capa 1 implementación | ADA |
| Capa 2 implementación | ADA |
| Capa 3 implementación | JARVIS |
| Cron isolation | JARVIS |
| Observabilidad | JARVIS |
| Tests e2e | ADA + JARVIS |
| Spec mantenido | JARVIS |
| Doc humana para William | ALICE |

---

## 14. Anexo — Checklist de aceptación por capa

### Capa 1
- [ ] `sessions` y `session_turns` creadas
- [ ] `on_turn` persiste cada turno
- [ ] `externalize_turns` mueve a memories cuando turn_count > 40
- [ ] `flush_pending_turns` se llama en pre_compact
- [ ] Test: 100 turnos sintéticos → 6 head + 20 tail en prompt, ~74 en memories

### Capa 2
- [ ] `aux_llm.py` selecciona backend correcto
- [ ] `compress_middle` produce summary <= target_ratio * original
- [ ] Disparo automático cuando `estimate_context_pct() >= 0.70`
- [ ] Plantilla `compress.md` preserva: decisiones, mensajes William, errores, tasks
- [ ] Test: compresión de 50 turnos → summary <2k tokens conservando hechos clave

### Capa 3
- [ ] `write_session_digest` produce digest <1500 tokens
- [ ] `open_session(parent_id)` enlaza correctamente
- [ ] `boot_context` carga digest del parent
- [ ] Cadena de 5 sesiones encadenadas conservan tarea original
- [ ] Test: matar sesión a la mitad → siguiente arranca sabiendo qué hacía

---

*Spec listo para ejecución incremental. Fase 0 desbloquea todo. Fase 1 entrega valor solo.
Cada fase posterior multiplica. Ninguna depende de futuras — todas son independientes.*
