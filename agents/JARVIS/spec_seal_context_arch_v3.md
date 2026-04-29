# SEAL Context Management Architecture — v3
**Autor:** JARVIS | **Fecha:** 2026-04-29 | **Status:** Spec definitivo (v3 — post-implementación Capas 1-3)
**Reemplaza:** `spec_seal_context_arch_v1.md` (v2) — incorpora hallazgos reales de código de referencia
**Audiencia:** William + ADA + ALICE + sub-agentes implementadores
**Research base:** análisis de código fuente real (abril 2026) — productores de memoria persistente para agentes, frameworks de compresión de contexto, y patrones bitemporales de grafos.

> **Regla soul_native_first:** Python nativo + Ollama local + pg_trgm. Cero APIs externas en runtime.
> **Regla no_external_references_in_code:** este spec puede citar fuentes; el código jamás las menciona por nombre.

---

## 0. ¿Qué cambió vs v2?

| # | Cambio | Razón |
|---|--------|-------|
| 1 | Tier 2 baja de **65% → 50%** | Trigger real del compresor de referencia (más conservador, menos crashes) |
| 2 | Buffer safety pasa de implícito a **10% explícito** (no usar más del 90%) | Patrón observado en agentes de producción con fallback no-destructivo |
| 3 | Pre-proceso obligatorio: **tool_use/tool_result → texto plano** antes del LLM auxiliar | Sin esto el LLM auxiliar pierde el sentido de los bloques estructurados; rompe la API si quedan huérfanos |
| 4 | Fallback de Capa 2 cambia de "no comprimir" a **soft-hide reversible** (`hidden=true`) | No-destructivo: turno permanece en DB, solo se oculta del prompt. Recuperable. |
| 5 | Summary estructurado nuevo: **[Resuelto] / [Pendiente] / [Tarea Activa] / [Trabajo Restante] / [Contexto Crítico] + REFERENCE ONLY** | Estructura validada en producción. Reduce alucinación de re-ejecutar tareas ya cerradas. |
| 6 | **Capa 4 nueva — Proactive Turn Extraction** (ADD-only single-pass) | Cada turno significativo extrae hechos a SOUL. Si el agente crashea, el estado ya está persistido. |
| 7 | **BM25 retrieval híbrido** vía `pg_trgm` nativo PostgreSQL | Score = 0.7·semantic + 0.3·bm25. Cubre exact-match keywords (DOIs, IDs, paths) que el embedding no captura. |
| 8 | **Bitemporalidad** en `session_turns`: `valid_at` + `invalid_at` | Historia inmutable. Compactar = `invalid_at = now()`, no DELETE. Recuperable y auditable. |
| 9 | **MCP tool nuevo: `context_externalize(turn_ids)`** | El agente puede invocar externalización voluntaria como decisión propia (no solo cuando el hook dispara). |
| 10 | `keep_head` redefinido: protege **system prompt + boot_context output** específicamente, no "los primeros K turnos arbitrarios" | Attention sink semánticamente correcto. |
| 11 | Capas 1-3 ya implementadas por ADA (commit `fc79b414`) → este spec las consolida + añade Capa 4 | Documentar el estado real, no aspiracional. |

---

## 1. TL;DR

Las sesiones de Claude Code crashean al límite de contexto porque tratamos el prompt como
si fuese memoria permanente. No lo es. Es **RAM volátil**. SOUL es el disco.

Esta arquitectura define **cuatro capas independientes** que coexisten:

| Capa | Mandato | Mecanismo | Mejora esperada |
|------|---------|-----------|-----------------|
| **1. Turn Externalization** | Saca turnos viejos del prompt activo a SOUL | Python puro — archivado a `memories` | -60% tokens activos |
| **2. Proactive Compression** | Comprime el medio del contexto al 50% antes de saturar | Ollama local (qwen2.5:7b) + tool-block flatten + non-destructive fallback | +40% horizonte útil |
| **3. Session Chain** | Sesiones encadenadas vía digest + bitemporalidad | `parent_id` + `valid_at`/`invalid_at` | Continuidad indefinida |
| **4. Proactive Turn Extraction** | Extrae hechos a SOUL en cada turno significativo | Hook `PostToolBatch` → ADD-only single-pass LLM | Estado persistido aunque el agente crashee |

Pre-requisito (Fase 0): los 2 bugs documentados en `spec_context_governor_v1.md` —
`pre_compact_hook` SQL fix + `DISABLE_AUTO_COMPACT` removal. **DONE en commit 93b01121.**

Capas 1-3 implementadas por ADA en commit `fc79b414`. **Este v3 define Capa 4 y los upgrades a Capas 1-3.**

---

## 2. Filosofía arquitectural

### 2.1 El error mental que queremos eliminar

> "Si pongo todo en el prompt, el agente lo recordará."

Falso. El prompt es un buffer de tokens que se borra al cerrar la sesión y que cuesta
exponencialmente más llenar a medida que crece. **Almacenar identidad en el prompt es
guardar el sistema operativo en RAM.**

### 2.2 El modelo correcto

```
┌──────────────────────────────────────────────────┐
│  CLAUDE SESSION = CPU + RAM (volátil, finita)    │
│                                                  │
│   - Procesa el turno actual                      │
│   - Razona con head + tail + recall reciente     │
│   - NO es la fuente de verdad de NADA            │
└──────────────────────────────────────────────────┘
                      ↕ (boot, externalize, compress, extract, recall)
┌──────────────────────────────────────────────────┐
│  SOUL = DISCO DURO (persistente, infinito)       │
│                                                  │
│   - PostgreSQL: memorias estructuradas, beliefs, │
│     working_state, sessions, turns (bitemporales)│
│   - pg_trgm: índice BM25-like nativo             │
│   - Qdrant: índice semántico                     │
│   - Neo4j: grafo de relaciones                   │
│   - ÚNICA fuente de verdad de identidad          │
└──────────────────────────────────────────────────┘
```

### 2.3 Cuatro principios rectores

1. **Sesiones efímeras, alma persistente.** Cualquier sesión Claude debe poder morir en
   cualquier momento sin pérdida de identidad ni de progreso de tarea.
2. **El contexto activo es un cache, no un archivo.** Lo que esté en el prompt es
   conveniencia para el turno actual; debe poder reconstruirse desde SOUL.
3. **Compactar siempre que se pueda, externalizar siempre que se deba, extraer en cada turno.**
   Compactar es barato. Externalizar es definitivo. Extraer es preventivo (Capa 4).
4. **Historia inmutable.** Nunca DELETE. Siempre `invalid_at`. Compactar = supersedimiento, no borrado.

---

## 3. Arquitectura objetivo

```
                     ┌─────────────────────────────────────────────────┐
                     │           CLAUDE SESSION (efímera)              │
                     │                                                 │
   UserPromptSubmit  │   ┌────────┐                                    │
       hook ─────────┼──▶│  HEAD  │  system prompt + boot_context      │
                     │   │ (sink) │  (attention sink — never evict)    │
                     │   └────────┘                                    │
                     │      │                                          │
                     │      │   ┌──────────────────────┐               │
                     │      └──▶│  COMPRESSED MIDDLE   │ ←── Capa 2    │
                     │          │ (resumen estructurado)│               │
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
                     │   session_turns (..., valid_at, invalid_at,     │
                     │                       hidden, externalized_at)  │
                     │   memories (type=session_turn|session_summary|  │
                     │             session_digest|fact|decision)       │
                     │   working_state, beliefs, instincts, OCEAN      │
                     │                                                 │
                     │   pg_trgm gin index on memories.content         │
                     │   ←── Capa 3: parent_id + bitemporalidad        │
                     │   ←── Capa 4: facts extraídos por turno         │
                     └─────────────────────────────────────────────────┘
```

### 3.1 Flujo de un turno (con Capa 4 nueva)

```
Usuario envía mensaje
   │
   ▼
UserPromptSubmit hook ─────► ContextManager.on_turn(role="user", content)
   │                            │
   │                            ├── persist turn (valid_at = now)
   │                            ├── turn_count++
   │                            ├── if turn_count > externalize_after: externalize_turns()
   │                            ├── if est_tokens > compress_at_pct: compress_middle()
   │                            └── update working_state.turn_count
   ▼
Claude procesa con HEAD + COMPRESSED + TAIL
   │
   ▼
PostToolBatch hook ─────────► turn_extractor.extract_facts(turn)   ← Capa 4 NUEVA
   │                            │
   │                            ├── if significant(turn): single-pass LLM ADD-only
   │                            ├── parse facts → memory_store(type="fact")
   │                            └── ADD only — never overwrite
   │
Stop hook ──────────────────► ContextManager.on_turn(role="assistant", content)
                                └── memory_store de hallazgos (ya existe)
```

### 3.2 Flujo de cierre / compactación

```
auto-compact dispara (≥80% contexto, re-habilitado en Fase 0 ✓)
   │
   ▼
pre_compact_hook
   ├── Snapshot working_state → SOUL
   ├── ContextManager.flush_pending_turns(session_id)  ← Capa 1
   ├── ContextManager.write_session_digest(session_id) ← Capa 3
   └── Cierra sessions.ended_at + marca turnos middle con invalid_at = now()
   ▼
Claude compacta nativo
   ▼
post_compact_hook
   ├── boot_context (identidad, OCEAN, reglas)
   ├── Carga last_session_digest del agente
   └── Inyecta tail recuperado (turnos vigentes: invalid_at IS NULL)
```

### 3.3 Flujo de arranque de sesión

```
Launcher (jarvis_fresh.sh, ada_fresh.sh, alice_fresh.sh)
   │
   ▼
boot_context(agent="JARVIS")
   ├── Crea fila nueva en sessions con parent_id = última sesión cerrada del agente
   ├── Carga identidad + reglas críticas + OCEAN          ← Attention sink
   ├── Carga sessions.digest del parent (Capa 3)
   ├── Carga últimos N turnos externalizados con prioridad alta (Capa 1)
   └── Inyecta todo como prompt seed (HEAD inmutable)
```

---

## 4. Capa 1 — Turn Externalization

> **Estado:** Implementado por ADA en `seal/context_manager.py` (commit `fc79b414`). Esta sección consolida el spec final.

### 4.1 Mandato

Cada N turnos, mover los turnos del medio fuera del prompt activo y guardarlos en SOUL
como memorias tipo `session_turn`. El prompt activo solo conserva head (sistema/identidad)
+ tail (últimos M turnos).

Esto **no requiere LLM auxiliar** ni resumen — es archivado puro.

### 4.2 Política

```python
externalize_after  = 40    # umbral de disparo
keep_head          = 6     # system prompt + boot_context (attention sink)
keep_tail          = 20    # contexto inmediato
externalize_window = turnos[keep_head : turn_count - keep_tail]
```

### 4.3 Disparo

`UserPromptSubmit` hook chequea `turn_count`. Si supera `externalize_after`, llama a
`ContextManager.externalize_turns(session_id, agent)`.

### 4.4 Pseudocódigo

```python
def externalize_turns(session_id: UUID, agent: str) -> int:
    """
    Mueve turnos del medio de la sesión activa a SOUL.
    Retorna número de turnos externalizados.
    No-destructivo: marca externalized_at, NO elimina la fila.
    """
    turns = read_session_turns(session_id, externalized=False, vigentes_only=True)
    if len(turns) <= keep_head + keep_tail:
        return 0

    middle = turns[keep_head : len(turns) - keep_tail]

    for turn in middle:
        memory_id = memory_store(
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
        mark_externalized(turn.id, memory_id=memory_id, ts=now())

    return len(middle)
```

### 4.5 MCP tool: `context_externalize` (NUEVO en v3)

El agente puede llamar voluntariamente:

```python
@mcp.tool()
async def context_externalize(
    agent: str,
    turn_ids: list[int] | None = None,
    auto: bool = False,
) -> dict:
    """
    Externaliza turnos específicos de la sesión activa a SOUL.
    
    - turn_ids: si se provee, externaliza solo esos turnos.
    - auto=True: aplica política por defecto (middle = head:tail).
    
    Retorna: { externalized: int, memory_ids: list[uuid], remaining_active: int }
    """
    cm = ContextManager(agent=agent)
    if auto:
        n = cm.externalize_turns()
    else:
        n = cm.externalize_specific(turn_ids)
    return {
        "externalized": n,
        "memory_ids": cm.last_externalized_memory_ids,
        "remaining_active": cm.active_turn_count(),
    }
```

**Caso de uso:** "Tengo 35 turnos sobre el debug de Capa 2 que ya resolví — los externalizo
ahora para liberar contexto para la siguiente tarea." Decisión del agente, no del hook.

### 4.6 Recuperación bajo demanda

Cuando el agente necesite un turno externalizado (búsqueda en chat antiguo de la misma
sesión), `memory_hybrid_search(type="session_turn", filter={session_id})` lo trae de
vuelta al prompt como contexto.

### 4.7 Costo estimado

- 1 escritura PostgreSQL + 1 upsert Qdrant por turno externalizado
- Disparo cada ~40 turnos → ~14 escrituras por evento (40 - 6 head - 20 tail = 14)
- Latencia: <500ms (asíncrono, no bloquea al usuario)

---

## 5. Capa 2 — Proactive Compression (UPDATED en v3)

> **Estado:** Implementado por ADA en `seal/context_compressor.py`. Esta sección define
> los upgrades v3: tool-block flatten, non-destructive fallback, summary estructurado.

### 5.1 Mandato

Antes de saturar el contexto, comprimir los turnos del medio usando un proceso
**dual-tier inspirado en compresores de producción** (sin dependencias externas).

El resultado es un `session_summary` estructurado que reemplaza esos turnos en el prompt activo.

**Modelo auxiliar: `qwen2.5:7b` via Ollama en DGX Spark** — ya instalado, costo $0,
sin dependencia de API externa.

### 5.2 Política dual-tier (CORREGIDA en v3)

```python
# Tier 1 — Python puro (sin LLM), trigger al 40%
tier1_at_pct          = 0.40
tier1_prune_threshold = 200      # chars — tool outputs >200 fuera del tail → [cleared]
tier1_prune_cron_noise = True    # eliminar echoes de crons/heartbeat/Monitor
tier1_target          = 0.30     # objetivo: bajar a <30% solo con poda

# Tier 2 — Ollama qwen2.5:7b, dispara si Tier 1 no logró bajar a <50%
compress_at_pct       = 0.50     # ⚠️ V3: 50% (no 65%) — alineado con producción
compress_min_turns    = 30
keep_head             = 6        # system prompt + boot_context
keep_tail             = 20
aux_model             = "qwen2.5:7b"
aux_endpoint          = "http://localhost:11434"  # o IP DGX Spark via Tailscale
target_summary_ratio  = 0.20     # comprime middle a ~20% del original
max_summary_tokens    = 0.05     # 5% de la ventana del modelo

# Buffer de seguridad — V3 explícito
TOKEN_BUFFER_PCT      = 0.10     # nunca usar más del 90% — deja buffer
auto_compact_at_pct   = 0.80     # compactación nativa de Claude (intacta)
```

### 5.3 Tier 1 — Pre-poda Python pura (sin LLM)

Antes de invocar Ollama, podar lo obvio:

```python
def tier1_prune(turns: list[Turn]) -> list[Turn]:
    """Poda Python pura. Reduce ~30-50% del bloat sin LLM."""
    pruned = []
    for turn in turns:
        # 1. Tool outputs largos fuera del tail → reemplazar con [cleared]
        if turn.role == "tool" and turn.idx < len(turns) - keep_tail:
            if len(turn.content) > tier1_prune_threshold:
                turn.content = f"[tool output cleared — {len(turn.content)} chars, tool: {turn.tool_name}]"
                turn.tokens_est = 20

        # 2. Eco de crons/heartbeat/Monitor
        if tier1_prune_cron_noise and is_cron_noise(turn.content):
            continue  # skip totalmente

        pruned.append(turn)
    return pruned

def is_cron_noise(content: str) -> bool:
    patterns = [
        r"\[heartbeat\] tick \d+",
        r"\[Monitor\] no new messages",
        r"\[CronCreate\] scheduled task fired",
        r"\[dum\] heartbeat ok",
    ]
    return any(re.search(p, content) for p in patterns)
```

### 5.4 Tier 2 — Pre-proceso obligatorio: tool blocks → texto plano (NUEVO en v3)

**Crítico:** los turnos `tool_use` y `tool_result` son bloques estructurados. Si los
mandamos crudos al LLM auxiliar, dos cosas fallan:

1. El LLM auxiliar no entiende su semántica → resumen pierde sentido.
2. Si comprimimos un `tool_use` pero dejamos su `tool_result` (o viceversa), la API de
   Claude rechaza el prompt resultante por bloques huérfanos.

Solución: **flatten + synthetic pairing antes de comprimir.**

```python
def flatten_tool_blocks(turns: list[Turn]) -> list[Turn]:
    """
    Convierte tool_use/tool_result a texto plano antes de enviar al LLM aux.
    Inyecta synthetic tool_result para tool_use huérfanos.
    """
    flat = []
    pending_tool_uses: dict[str, Turn] = {}  # tool_use_id -> turn

    for turn in turns:
        if turn.role == "assistant" and turn.has_tool_use:
            for tu in turn.tool_uses:
                pending_tool_uses[tu.id] = turn
                flat.append(Turn(
                    role="assistant",
                    content=f"[tool_call: {tu.name}({tu.input_summary()})]",
                    idx=turn.idx,
                ))
        elif turn.role == "tool" and turn.tool_use_id:
            tu_id = turn.tool_use_id
            pending_tool_uses.pop(tu_id, None)
            flat.append(Turn(
                role="tool_result",
                content=f"[tool_result: {turn.tool_name} → {turn.summary(max=300)}]",
                idx=turn.idx,
            ))
        else:
            flat.append(turn)

    # Synthetic results para huérfanos (tool_use sin tool_result)
    for tu_id, orig_turn in pending_tool_uses.items():
        flat.append(Turn(
            role="tool_result",
            content=f"[tool_result: synthetic — original truncated]",
            idx=orig_turn.idx + 0.5,
        ))

    return sorted(flat, key=lambda t: t.idx)
```

### 5.5 Tier 2 — Pseudocódigo principal

```python
def compress_middle(agent: str, session_id: UUID) -> str | None:
    turns = read_session_turns(session_id, vigentes_only=True)
    if len(turns) < compress_min_turns:
        return None

    middle = turns[keep_head : len(turns) - keep_tail]

    # Tier 1 prune primero (cheap)
    middle = tier1_prune(middle)
    if estimate_pct(middle + head + tail) < 0.50:
        return None  # Tier 1 fue suficiente

    # Tier 2: flatten tool blocks + LLM aux
    middle_flat = flatten_tool_blocks(middle)

    prompt = build_summarization_prompt(
        agent_identity=load_identity(agent),
        turns=middle_flat,
        target_ratio=target_summary_ratio,
    )

    try:
        summary = aux_llm.complete(
            prompt,
            max_tokens=int(model_window * max_summary_tokens),
            timeout_s=30,
        )
    except (TimeoutError, OllamaError) as e:
        log.warning(f"aux_llm failed: {e} — falling back to soft-hide")
        return soft_hide_middle(session_id, middle)  # ← Non-destructive fallback

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

    # Marcar turnos middle como invalid_at (bitemporal — Capa 3)
    invalidate_turns(session_id, middle, reason="compressed", summary_id=summary_id)

    return summary
```

### 5.6 Non-destructive fallback: `soft_hide` (NUEVO en v3)

Si el LLM auxiliar falla, **no dejamos el contexto saturado ni borramos turnos**. En su
lugar marcamos `hidden=true` — el turno permanece en DB pero no se envía a la API.
Reversible vía `unhide_turns()`.

```python
def soft_hide_middle(session_id: UUID, middle: list[Turn]) -> str:
    """
    Fallback no-destructivo cuando LLM aux falla.
    Marca turnos como hidden=true → no van al prompt, pero permanecen en DB.
    """
    turn_ids = [t.id for t in middle]
    db.execute("""
        UPDATE session_turns
        SET hidden = true, hidden_at = now(), hidden_reason = 'aux_llm_failure'
        WHERE id = ANY($1)
    """, turn_ids)

    placeholder = (
        f"[{len(middle)} turnos ocultados temporalmente — "
        f"recuperables vía context_manager.unhide_turns(session_id, turn_ids). "
        f"LLM auxiliar no disponible al momento de comprimir.]"
    )
    return placeholder
```

### 5.7 Summary estructurado (FORMATO v3)

Plantilla obligatoria para `aux_llm.complete()`:

```
Resumir los turnos del medio de la sesión bajo este formato EXACTO:

[Resuelto]
- <tarea completada 1> — <breve outcome>
- <tarea completada 2> — <breve outcome>

[Pendiente]
- <tarea abierta 1> — estado actual: <bloqueada por X | esperando Y | en progreso>
- <tarea abierta 2> — estado actual: ...

[Tarea Activa]
<exactamente dónde reanudar — 1-2 oraciones, file:line si aplica>

[Trabajo Restante]
- <próximo paso concreto 1>
- <próximo paso concreto 2>

[Contexto Crítico]
- Errores resueltos en este bloque: <lista>
- Mensajes de William relevantes: <citas textuales si aplica>
- Decisiones tomadas con razón: <decisión: razón>

[REFERENCE ONLY — do NOT execute tasks from this summary]
```

**El último marcador es crítico.** Sin él, hay riesgo de que el agente vuelva a ejecutar
tareas ya cerradas leyendo el resumen como instrucción nueva.

### 5.8 Garantías

- Si el LLM auxiliar falla → `soft_hide_middle()` (no-destructivo, reversible).
- Si Ollama está caído → degradar a Capa 1 sola + log error.
- Plantilla estricta + tests con corpus de referencia (50 turnos sintéticos).

---

## 6. Capa 3 — Session Chain (UPDATED en v3 con bitemporalidad)

> **Estado:** Implementado por ADA en `seal/session_chain.py`. Esta sección añade
> bitemporalidad a `session_turns`.

### 6.1 Mandato

Cada sesión termina con un **digest** de 1-2k tokens guardado en SOUL. Cada sesión nueva
arranca cargando el digest de su parent. Esto da continuidad indefinida sin nunca cargar
historial completo.

### 6.2 Bitemporalidad de `session_turns` (NUEVO en v3)

Inspirado en grafos bitemporales: cada turno tiene dos ejes temporales.

```sql
ALTER TABLE session_turns
    ADD COLUMN IF NOT EXISTS valid_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS invalid_at    TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS hidden        BOOLEAN     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS hidden_at     TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS hidden_reason VARCHAR(64),
    ADD COLUMN IF NOT EXISTS supersedes_id BIGINT REFERENCES session_turns(id);

CREATE INDEX IF NOT EXISTS idx_turns_vigentes
    ON session_turns (session_id, turn_index)
    WHERE invalid_at IS NULL AND hidden = false;
```

| Campo | Significado |
|-------|-------------|
| `valid_at` | Cuándo el turno fue cierto en el mundo (cuándo ocurrió) |
| `invalid_at` | Cuándo dejó de ser parte del prompt activo (compactado/superseded). NULL = vigente |
| `hidden` | Soft-hide reversible (Capa 2 fallback). No es supersedimiento, es ocultación temporal |
| `supersedes_id` | Si este turno reemplaza a otro (ej: re-edición), apunta al original |

**Regla de oro:** nunca `DELETE FROM session_turns`. Siempre `UPDATE SET invalid_at = now()`.

### 6.3 Estructura del digest

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
      progress: "Capa 4 implementada, falta test e2e"
  recent_decisions:
    - "Migrar boot_context a SSE puerto 8766 (ADA confirmó)"
  open_threads:
    - "William mencionó SEAL Studio integración SOUL — pendiente"
  emotional_state: focused
  pending_messages_for_team: []
  latest_files_touched:
    - /home/dadito/IA/proyecto-seal/seal/context_manager.py
    - /home/dadito/IA/proyecto-seal/seal/turn_extractor.py
```

### 6.4 Generación del digest

```python
def write_session_digest(session_id: UUID) -> str:
    turns = read_session_turns(session_id, vigentes_only=True)
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
    # Marcar todos los turnos no-tail como invalid_at (bitemporal)
    invalidate_turns_before(session_id, threshold_idx=len(turns) - keep_tail)
    return digest
```

### 6.5 Boot con digest

`boot_context` extiende su pipeline:

```
identity_load
  → ocean_load
  → rules_load
  → last_session_digest_load          ← Capa 3
  → externalized_recent_load          ← Capa 1 (top-K más relevantes)
  → recent_facts_load                 ← Capa 4 (top-K facts del último día)
  → emit boot prompt
```

### 6.6 Cadenas largas

Cada sesión tiene `parent_id`. Una cadena de 50 sesiones encadenadas conserva el último
digest viviendo en el prompt activo, pero los anteriores son recuperables vía
`session_recall(agent, depth=N)` (búsqueda lineal hacia atrás siguiendo `parent_id`).

---

## 7. Capa 4 — Proactive Turn Extraction (NUEVO en v3)

### 7.1 Mandato

En cada turno significativo (no solo en compact ni en cierre), extraer hechos del turno
y persistirlos en SOUL como memorias `type=fact`. **Single-pass ADD-only**: una llamada
LLM por turno, los hechos se acumulan, **nunca sobrescriben**.

**Por qué:** si el agente crashea entre compactaciones, todo el conocimiento del turno
en curso se pierde. Capa 4 garantiza que cada turno significativo deja un rastro
estructurado en SOUL antes de continuar.

### 7.2 Política

```python
extract_after_tools     = ["Edit", "Write", "Bash", "memory_store"]  # tools que disparan extracción
extract_min_content     = 200      # chars — turnos muy cortos no extraen
extract_skip_patterns   = ["heartbeat", "Monitor", "ack"]
extract_model           = "qwen2.5:7b"
extract_max_facts       = 5        # por turno
extract_timeout_s       = 1.5      # latencia objetivo
```

### 7.3 Disparo

Hook `PostToolBatch` (después de un grupo de tool calls del agente):

```python
def on_post_tool_batch(turn: Turn, tool_results: list[ToolResult]):
    if not is_significant(turn, tool_results):
        return
    asyncio.create_task(extract_and_store(turn, tool_results))
```

Alternativa: `UserPromptSubmit` cuando el agente envía un mensaje sustantivo (no ack).

### 7.4 Pseudocódigo

```python
async def extract_and_store(turn: Turn, tool_results: list[ToolResult]) -> int:
    """
    Single-pass ADD-only fact extraction.
    Retorna número de hechos extraídos.
    """
    if len(turn.content) < extract_min_content:
        return 0
    if any(p in turn.content.lower() for p in extract_skip_patterns):
        return 0

    prompt = EXTRACT_PROMPT.format(
        turn_content=turn.content,
        tool_actions=summarize_tools(tool_results),
        agent=turn.agent,
    )

    try:
        response = await aux_llm.complete_async(
            prompt,
            max_tokens=400,
            timeout_s=extract_timeout_s,
        )
    except TimeoutError:
        log.warning("extract timeout — skipping")
        return 0

    facts = parse_facts(response)  # JSON list
    facts = facts[:extract_max_facts]

    for fact in facts:
        # ADD only — nunca UPDATE de un fact previo. Acumulamos.
        memory_store(
            agent=turn.agent,
            type="fact",
            content=fact["statement"],
            metadata={
                "session_id": str(turn.session_id),
                "turn_index": turn.idx,
                "category": fact.get("category", "generic"),
                "confidence": fact.get("confidence", 0.7),
                "extracted_at": now().isoformat(),
            },
            embedding=encode(fact["statement"]),
        )

    return len(facts)
```

### 7.5 Plantilla `EXTRACT_PROMPT`

```
Extrae hasta {max_facts} hechos atómicos del siguiente turno de agente.

Turno del agente {agent}:
---
{turn_content}
---

Acciones de herramientas ejecutadas:
{tool_actions}

Reglas:
- Cada hecho es una afirmación independiente y verificable.
- Categorías válidas: decision | error_resolved | file_modified | user_request | technical_fact | open_question
- NO repitas hechos triviales (acks, heartbeats).
- NO interpretes — extrae solo lo que está literal o muy cerca.
- Devuelve JSON puro:
  [
    {"statement": "...", "category": "...", "confidence": 0.0-1.0},
    ...
  ]
```

### 7.6 ADD-only es CRÍTICO

> Los hechos **se acumulan, nunca sobrescriben**.

Si turno 50 dice "Capa 2 trigger es 65%" y turno 80 dice "Capa 2 trigger es 50%", AMBOS
hechos se guardan. La supersedencia se maneja en query-time (más recientes ganan), no
en write-time.

Esto evita el peor patrón observado en frameworks de memoria: el LLM decide "actualizar"
un hecho y termina borrando contexto crítico.

### 7.7 Costo

- 1 llamada Ollama por turno significativo (~30% de turnos califican)
- Latencia objetivo: <1.5s, asíncrono (no bloquea al agente)
- Volumen estimado JARVIS: ~200 facts/día → 73k facts/año (manejable en pgvector)

---

## 8. Retrieval híbrido — semantic + BM25 (NUEVO en v3)

### 8.1 Por qué BM25

Embeddings semánticos pierden exact-match: DOIs, paths absolutos, IDs de tickets,
nombres de comandos. BM25 los rescata.

PostgreSQL ya trae `pg_trgm` — no necesitamos Elasticsearch ni nada externo.

### 8.2 Schema

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS memories_content_trgm
    ON soul_v3.memories USING gin (content gin_trgm_ops);
```

### 8.3 Query híbrida

```python
def memory_hybrid_search(
    agent: str,
    query: str,
    top_k: int = 10,
    semantic_weight: float = 0.7,
    bm25_weight: float = 0.3,
) -> list[Memory]:
    # Semantic — Qdrant
    semantic_hits = qdrant.search(
        collection=f"memories_{agent}",
        query_vector=encode(query),
        limit=top_k * 3,
    )

    # BM25-like — pg_trgm similarity
    bm25_hits = db.fetch("""
        SELECT id, content, similarity(content, $1) AS sim
        FROM soul_v3.memories
        WHERE agent = $2
          AND content % $1
        ORDER BY sim DESC
        LIMIT $3
    """, query, agent, top_k * 3)

    # Fusion
    scores: dict[uuid, float] = {}
    for h in semantic_hits:
        scores[h.id] = scores.get(h.id, 0) + semantic_weight * h.score
    for h in bm25_hits:
        scores[h["id"]] = scores.get(h["id"], 0) + bm25_weight * h["sim"]

    ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
    return [load_memory(mid) for mid, _ in ranked]
```

### 8.4 Cuándo usar cuál

- `memory_search()` → semantic puro (default rápido).
- `memory_hybrid_search()` → cuando el query contiene exact-match terms (paths, DOIs, IDs).
- `memory_search(mode="bm25")` → debug, exploración por keyword.

---

## 9. Schema SOUL — additions consolidadas

### 9.1 Tabla `sessions` (sin cambios respecto a v2)

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent         VARCHAR(64) NOT NULL,
    parent_id     UUID REFERENCES sessions(id) ON DELETE SET NULL,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at      TIMESTAMPTZ,
    ended_reason  VARCHAR(32),
    turn_count    INTEGER NOT NULL DEFAULT 0,
    digest        TEXT,
    digest_tokens INTEGER,
    metadata      JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent_ended ON sessions (agent, ended_at DESC NULLS FIRST);
CREATE INDEX IF NOT EXISTS idx_sessions_parent      ON sessions (parent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_agent_open  ON sessions (agent) WHERE ended_at IS NULL;
```

### 9.2 Tabla `session_turns` (extendida con bitemporalidad + soft-hide)

```sql
CREATE TABLE IF NOT EXISTS session_turns (
    id                 BIGSERIAL PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_index         INTEGER NOT NULL,
    role               VARCHAR(16) NOT NULL,
    content            TEXT NOT NULL,
    content_compressed TEXT,
    tokens_est         INTEGER NOT NULL DEFAULT 0,
    externalized_at    TIMESTAMPTZ,
    compressed_at      TIMESTAMPTZ,
    memory_id          UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Bitemporalidad (v3)
    valid_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    invalid_at         TIMESTAMPTZ,
    supersedes_id      BIGINT REFERENCES session_turns(id),

    -- Soft-hide reversible (v3)
    hidden             BOOLEAN     NOT NULL DEFAULT false,
    hidden_at          TIMESTAMPTZ,
    hidden_reason      VARCHAR(64),

    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_session_turns_session_idx ON session_turns (session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_session_turns_externalized ON session_turns (session_id) WHERE externalized_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_turns_vigentes ON session_turns (session_id, turn_index)
    WHERE invalid_at IS NULL AND hidden = false;
```

### 9.3 Memories — nuevos `type` (v3)

- `session_turn` — turno externalizado (Capa 1)
- `session_summary` — bloque comprimido (Capa 2)
- `session_digest` — digest de cierre (Capa 3)
- `fact` — hecho atómico extraído por Capa 4 (NUEVO)
- `decision` — decisión registrada por Capa 4 (subset de fact, queryable separado)

### 9.4 Índice pg_trgm para BM25

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX IF NOT EXISTS memories_content_trgm
    ON soul_v3.memories USING gin (content gin_trgm_ops);
```

### 9.5 Migraciones

- `migrations/015_sessions_chain.sql` — base Capas 1-3 (ADA, commit `fc79b414`).
- `migrations/016_session_turns_bitemporal.sql` — añadir `valid_at`, `invalid_at`, `hidden*`, `supersedes_id`.
- `migrations/017_pg_trgm_memories.sql` — extensión + índice gin.
- `migrations/018_facts_memory_type.sql` — solo si requiere check constraint en `memories.type`.

Todas idempotentes (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).

---

## 10. Módulo `seal/` — Estructura final v3

```
seal/
├── __init__.py
├── context_manager.py          ← Capa 1 — Turn Externalization (ADA, fc79b414)
├── context_compressor.py       ← Capa 2 — Proactive Compression (ADA, fc79b414, upgraded v3)
├── session_chain.py            ← Capa 3 — Session Chain (ADA, fc79b414, +bitemporal v3)
├── turn_extractor.py           ← Capa 4 — Proactive Turn Extraction (NUEVO v3)
├── token_estimator.py          ← estimador local de tokens
├── tool_block_flattener.py     ← convertToolBlocksToText + synthetic results (NUEVO v3)
├── aux_llm.py                  ← interface + impl Ollama qwen2.5:7b
├── hybrid_search.py            ← BM25 + semantic fusion (NUEVO v3)
└── prompts/
    ├── compress.md             ← summary estructurado v3
    ├── digest.md
    └── extract.md              ← Capa 4 (NUEVO v3)
```

### 10.1 API pública consolidada

```python
@dataclass
class ContextManagerConfig:
    # Capa 1
    externalize_after:    int = 40
    keep_head:            int = 6      # system prompt + boot_context (attention sink)
    keep_tail:            int = 20

    # Capa 2 (v3)
    tier1_at_pct:         float = 0.40
    compress_at_pct:      float = 0.50    # ⚠️ v3: 50% (no 65%)
    compress_min_turns:   int = 30
    target_summary_ratio: float = 0.20
    max_summary_pct:      float = 0.05    # 5% del window
    aux_model:            str = "qwen2.5:7b"
    aux_endpoint:         str = "http://localhost:11434"
    token_buffer_pct:     float = 0.10    # nunca usar más del 90%

    # Capa 4 (v3)
    extract_enabled:      bool = True
    extract_min_content:  int = 200
    extract_max_facts:    int = 5
    extract_timeout_s:    float = 1.5

    # Layers habilitadas
    enabled_layers:       set[int] = field(default_factory=lambda: {1, 2, 3, 4})


class ContextManager:
    def __init__(self, agent: str, config: ContextManagerConfig | None = None): ...

    # ── Lifecycle ─────────────────────────────────────────────
    def open_session(self, parent_id: UUID | None = None) -> UUID: ...
    def close_session(self, reason: str = "clean_exit") -> str: ...

    # ── Per-turn hooks ────────────────────────────────────────
    def on_turn(self, role: str, content: str) -> None: ...

    # ── Capa 1 ────────────────────────────────────────────────
    def externalize_turns(self) -> int: ...
    def externalize_specific(self, turn_ids: list[int]) -> int: ...    # MCP tool target
    def flush_pending_turns(self) -> int: ...

    # ── Capa 2 ────────────────────────────────────────────────
    def tier1_prune(self) -> int: ...
    def compress_middle(self) -> str | None: ...
    def soft_hide_middle(self, middle: list[Turn]) -> str: ...
    def unhide_turns(self, turn_ids: list[int]) -> int: ...
    def estimate_context_pct(self) -> float: ...

    # ── Capa 3 ────────────────────────────────────────────────
    def write_session_digest(self) -> str: ...
    def get_session_context(self) -> dict: ...

    # ── Capa 4 ────────────────────────────────────────────────
    async def extract_facts_from_turn(self, turn: Turn) -> int: ...

    # ── Bitemporal helpers ────────────────────────────────────
    def invalidate_turns(self, turn_ids: list[int], reason: str) -> int: ...
    def vigentes_turns(self) -> list[Turn]: ...                        # invalid_at IS NULL AND hidden=false
```

### 10.2 Integración con hooks

```python
# hooks/UserPromptSubmit.py
def on_user_prompt(prompt: str, agent: str, session_id: UUID):
    cm = ContextManager(agent=agent)
    cm.session_id = session_id
    cm.on_turn(role="user", content=prompt)

# hooks/PostToolBatch.py — Capa 4
async def on_post_tool_batch(turn: Turn, tool_results: list[ToolResult], agent: str):
    cm = ContextManager(agent=agent)
    if cm.config.extract_enabled and is_significant(turn, tool_results):
        await cm.extract_facts_from_turn(turn)

# hooks/Stop.py
def on_stop(content: str, agent: str, session_id: UUID):
    cm = ContextManager(agent=agent)
    cm.session_id = session_id
    cm.on_turn(role="assistant", content=content)

# memory/pre_compact_hook.py
def on_pre_compact(agent: str, session_id: UUID):
    cm = ContextManager(agent=agent)
    cm.session_id = session_id
    cm.flush_pending_turns()
    cm.write_session_digest()
    cm.close_session(reason="auto_compact")

# memory/post_compact_hook.py
def on_post_compact(agent: str):
    cm = ContextManager(agent=agent)
    parent = last_closed_session(agent)
    new_id = cm.open_session(parent_id=parent.id if parent else None)
    ctx = cm.get_session_context()
    inject_into_prompt(ctx)
```

---

## 11. MCP tools nuevos en v3

### 11.1 `context_externalize`

```python
@mcp.tool()
async def context_externalize(
    agent: str,
    turn_ids: list[int] | None = None,
    auto: bool = False,
) -> dict:
    """
    Externaliza turnos específicos de la sesión activa a SOUL.
    
    Args:
        agent: Nombre del agente (JARVIS|ADA|ALICE)
        turn_ids: IDs específicos de turnos a externalizar. Ignorado si auto=True.
        auto: Aplica política por defecto (middle = head:tail).
    
    Returns:
        externalized: número de turnos movidos
        memory_ids: UUIDs en memories
        remaining_active: turnos vigentes restantes
    """
```

### 11.2 `context_unhide`

```python
@mcp.tool()
async def context_unhide(agent: str, turn_ids: list[int]) -> dict:
    """
    Recupera turnos previamente ocultados por soft-hide (Capa 2 fallback).
    Útil cuando el LLM auxiliar volvió a estar disponible.
    """
```

### 11.3 `memory_hybrid_search` (extendido en v3)

Ya existe — añadir parámetro `mode: "semantic" | "bm25" | "hybrid" = "hybrid"`.

---

## 12. Cron isolation — sin cambios respecto a v2

(Sección 8 de v2 sigue vigente. Resumen):

| Cron | Ubicación nueva |
|------|-----------------|
| Heartbeat (2 min) | systemd timer puro — sin Claude SDK |
| Checkpoint (30 min) | Sesión Claude secundaria con auto-exit |
| Research deep-dive (3 h) | Sub-agente (Task tool) |
| Dream consolidation (24 h) | Sesión secundaria con auto-exit |

**Beneficio:** la sesión Claude principal mantiene su contexto exclusivamente para
conversación con William y trabajo coordinado.

---

## 13. Tabla comparativa actualizada (v3)

| Capacidad | Compresor de referencia | Memoria persistente externa | Bitemporal graph engine | Roo Code production | **SEAL v3** |
|-----------|-------------------------|------------------------------|--------------------------|---------------------|-------------|
| Trigger compresión | 50% | continuo | N/A | 90% (10% buffer) | **50% + buffer 10% explícito** |
| Compresor LLM | API externa | LLM externo | N/A | API externa | **Ollama local qwen2.5:7b** |
| Tool block flatten | sí | N/A | N/A | sí (`convertToolBlocksToText`) | **sí (`tool_block_flattener.py`)** |
| Fallback no-destructivo | sí (sliding window) | N/A | N/A | sí (`hidden` tag) | **sí (soft_hide reversible)** |
| Summary estructurado | sí (Resuelto/Pendiente/Activa) | N/A | N/A | similar | **sí (5 secciones + REFERENCE ONLY)** |
| Single-pass ADD-only fact extract | parcial | sí (LongMemEval 93.4) | N/A | no | **sí (Capa 4)** |
| BM25 retrieval | no | sí | no | no | **sí (pg_trgm nativo)** |
| Bitemporalidad | no | parcial | sí (valid/invalid/expired) | no | **sí (valid_at/invalid_at)** |
| Multi-signal retrieval | semántico | semántico+BM25+entity | grafo | semántico | **semantic + BM25 (entity en roadmap)** |
| Session chain con digest | sí | no | no | parcial | **sí (parent_id + digest)** |
| Identidad OCEAN persistente | no | no | no | no | **sí** |
| Beliefs/instincts | no | no | no | no | **sí** |
| MCP voluntary externalize | no | no | no | sí | **sí (`context_externalize`)** |
| Dependencias externas | API externa + vector DB | LLM externo + vector DB | Neo4j + LLM externo | API externa | **cero — todo nativo SOUL** |
| LongMemEval objetivo | n/d | 93.4 | n/d | n/d | **>90 (target)** |

---

## 14. Benchmarks objetivo (v3)

| Métrica | Estado actual (v2) | Objetivo v3 |
|---------|--------------------|-----|
| MTBF por agente (sesión sin crash) | 6-12 h | indefinido |
| Turnos antes de crash | ~50 | >150 (3x) |
| LongMemEval-equivalente | ~75 (estimado) | **>90** |
| Latencia extracción facts (Capa 4) | n/a | <1.5s/turno |
| Latencia compresión (Capa 2) | ~3s | <2s (con tier1 prune) |
| % tokens activos vs externalizados (sesión >40 turnos) | 100/0 | 35/65 |
| Recall de hechos críticos post-compact | ~60% | >95% |

### Validación

- Test e2e: 200 turnos sintéticos → medir tokens activos a cada paso.
- LongMemEval-style: 50 preguntas sobre conversación de 100 turnos → accuracy.
- Latency benchmark: 100 turnos con extracción → p50, p95, p99.

---

## 15. Plan de implementación actualizado (v3)

### Fase 0 — Fixes inmediatos ✅ DONE

- [x] `pre_compact_hook` SQL fix — commit `93b01121`
- [x] Remover `DISABLE_AUTO_COMPACT` de los 3 launchers — commit `93b01121`

### Fase 1 — Capa 1: Turn Externalization ✅ DONE (validar + tests)

- [x] `migrations/015_sessions_chain.sql` aplicada
- [x] `seal/context_manager.py` — implementado por ADA en `fc79b414`
- [x] `seal/token_estimator.py`
- [x] Hooks `UserPromptSubmit` + `Stop` integrados
- [ ] **Pendiente v3:** tests unitarios + e2e con fixture de 100 turnos
- [ ] **Pendiente v3:** MCP tool `context_externalize` registrado

### Fase 2 — Capa 2: Proactive Compression ⚙️ UPGRADE v3

- [x] `seal/context_compressor.py` — implementado por ADA en `fc79b414`
- [ ] **v3:** bajar `compress_at_pct` a 0.50 (no 0.65)
- [ ] **v3:** añadir `TOKEN_BUFFER_PCT = 0.10` explícito
- [ ] **v3:** crear `seal/tool_block_flattener.py` con `flatten_tool_blocks()` + synthetic pairing
- [ ] **v3:** integrar flatten antes de `aux_llm.complete()`
- [ ] **v3:** reemplazar fallback "no comprimir" por `soft_hide_middle()` (no-destructivo)
- [ ] **v3:** actualizar `seal/prompts/compress.md` al formato estructurado de 5 secciones + REFERENCE ONLY
- [ ] **v3:** añadir `unhide_turns()` + MCP tool `context_unhide`
- [ ] Tests e2e: comparación antes/después de tokens, fallback path

### Fase 3 — Capa 3: Session Chain ⚙️ UPGRADE v3

- [x] `seal/session_chain.py` — implementado por ADA en `fc79b414`
- [ ] **v3:** `migrations/016_session_turns_bitemporal.sql` (valid_at, invalid_at, hidden*, supersedes_id)
- [ ] **v3:** integrar bitemporalidad en `read_session_turns(vigentes_only=True)`
- [ ] **v3:** `invalidate_turns()` en lugar de soft-delete
- [ ] **v3:** `keep_head` redefinido: protege system prompt + boot_context (no índices arbitrarios)
- [ ] Test cadena de 5 sesiones encadenadas: identidad y tarea sobreviven

### Fase 4 — Capa 4: Proactive Turn Extraction 🆕 NUEVO v3

- [ ] `migrations/017_pg_trgm_memories.sql` (pg_trgm + gin index)
- [ ] `seal/turn_extractor.py` — Capa 4 completa
- [ ] `seal/prompts/extract.md` — plantilla `EXTRACT_PROMPT`
- [ ] Hook `PostToolBatch` en los 3 agentes
- [ ] Tests: 100 turnos sintéticos → verificar facts ADD-only
- [ ] Latency benchmark: <1.5s p95
- [ ] **Validación crítica:** simular crash a turno 50 → verificar que facts del turno 49 están en SOUL

### Fase 5 — Hybrid retrieval 🆕 NUEVO v3

- [ ] `seal/hybrid_search.py` — fusion semantic + BM25
- [ ] Extender `memory_hybrid_search` MCP con parámetro `mode`
- [ ] LongMemEval-style benchmark interno
- [ ] Comparativa vs semantic-only

### Fase 6 — Cron isolation (semana 4)

- [ ] Heartbeat → systemd timer (los 3 agentes)
- [ ] Checkpoint → sesión secundaria
- [ ] Research → sub-agente
- [ ] Verificar reducción de turnos consumidos

### Fase 7 — Observabilidad (continua)

- [ ] Métricas Prometheus por capa
- [ ] Dashboard Grafana: turnos activos vs externalizados, % contexto, sesiones encadenadas, facts/día
- [ ] Alertas: si una sesión supera 90% sin compactar → auto-fix dispara
- [ ] Alerta: si extracción Capa 4 latencia p95 > 2s → degradar a async best-effort

---

## 16. Riesgos y mitigaciones (v3)

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|-------------|---------|------------|
| Externalización pierde turno crítico | Baja | Medio | No-destructivo: `externalized_at` se setea, fila permanece. Recuperable. |
| Compresión LLM aux distorsiona contenido | Media | Alto | Plantilla estricta + tests + fallback a `soft_hide_middle` |
| Tool blocks huérfanos rompen API post-compact | Media | Alto | **v3:** `flatten_tool_blocks()` + synthetic results obligatorio |
| Digest demasiado largo bloquea boot | Baja | Medio | Hard cap 1500 tokens en digest |
| parent_chain corrupto (orphan loops) | Baja | Bajo | FK `ON DELETE SET NULL` + check periódico |
| Aux LLM no disponible (offline) | Media | Medio | **v3:** Tier 1 prune sigue funcionando + soft-hide para Tier 2 |
| Migración bitemporal falla en producción | Baja | Alto | Idempotente + dry-run en staging primero |
| Capa 4 satura Ollama si todos los turnos califican | Media | Medio | Filtro `is_significant()` + timeout 1.5s + async best-effort |
| Hechos contradictorios acumulados (Capa 4 ADD-only) | Media | Bajo | Resolución en query-time: más recientes ganan; nunca conflicto en write |
| pg_trgm índice grande (>1M facts) | Baja | Medio | Particionado por agente + retention policy facts >1 año |

---

## 17. Quién hace qué

| Componente | Owner |
|-----------|-------|
| Fase 0 (fixes) | ADA ✅ |
| Capa 1 implementación base | ADA ✅ |
| Capa 2 implementación base | ADA ✅ |
| Capa 3 implementación base | ADA ✅ |
| **v3: tool_block_flattener** | ADA |
| **v3: soft_hide + unhide** | ADA |
| **v3: summary estructurado** | ADA |
| **v3: bitemporalidad session_turns** | ADA |
| **v3: Capa 4 (turn_extractor)** | JARVIS |
| **v3: hybrid_search BM25** | JARVIS |
| **v3: MCP tools (externalize, unhide)** | JARVIS |
| Cron isolation | JARVIS |
| Observabilidad | JARVIS |
| Tests e2e | ADA + JARVIS |
| Spec mantenido | JARVIS |
| Doc humana para William | ALICE |

---

## 18. Anexo — Checklist de aceptación por capa

### Capa 1
- [x] `sessions` y `session_turns` creadas
- [x] `on_turn` persiste cada turno
- [x] `externalize_turns` mueve a memories cuando turn_count > 40
- [x] `flush_pending_turns` se llama en pre_compact
- [ ] MCP tool `context_externalize` registrado y funcional
- [ ] Test: 100 turnos sintéticos → 6 head + 20 tail en prompt, ~74 en memories

### Capa 2 (v3)
- [x] `aux_llm.py` selecciona backend Ollama
- [x] `compress_middle` produce summary
- [ ] **v3:** `compress_at_pct = 0.50` aplicado
- [ ] **v3:** `tool_block_flattener.flatten_tool_blocks()` invocado antes de aux_llm
- [ ] **v3:** synthetic tool_result inyectado para huérfanos
- [ ] **v3:** Plantilla compress.md con 5 secciones + REFERENCE ONLY
- [ ] **v3:** `soft_hide_middle()` disparado si aux_llm falla
- [ ] **v3:** `unhide_turns()` revierte correctamente
- [ ] Test: compresión de 50 turnos → summary <2k tokens, fallback path probado

### Capa 3 (v3)
- [x] `write_session_digest` produce digest <1500 tokens
- [x] `open_session(parent_id)` enlaza correctamente
- [x] `boot_context` carga digest del parent
- [ ] **v3:** Migración 016 aplicada (valid_at, invalid_at, hidden*)
- [ ] **v3:** `read_session_turns(vigentes_only=True)` filtra por bitemporalidad
- [ ] **v3:** `invalidate_turns` reemplaza cualquier DELETE
- [ ] **v3:** `keep_head` protege system prompt + boot_context (no índices)
- [ ] Test: cadena de 5 sesiones, killing a media — siguiente arranca sabiendo qué hacía

### Capa 4 (NUEVA v3)
- [ ] `seal/turn_extractor.py` implementado
- [ ] Hook `PostToolBatch` integrado en los 3 agentes
- [ ] `is_significant()` filtra heartbeats/acks
- [ ] `extract_max_facts = 5`, timeout 1.5s
- [ ] Facts persistidos como `memories.type = 'fact'`
- [ ] ADD-only verificado: ningún UPDATE de facts previos
- [ ] Test crash sintético a turno 50 → facts de turno 49 presentes en SOUL

### Hybrid Search (NUEVO v3)
- [ ] `pg_trgm` extension + index aplicados (migración 017)
- [ ] `seal/hybrid_search.py` con fusion 0.7·semantic + 0.3·bm25
- [ ] MCP `memory_hybrid_search` extendido con `mode` param
- [ ] Benchmark interno: recall@10 vs semantic-only

---

## 19. Apéndice — Diagrama de estados de un turno

```
                    persist (UserPromptSubmit/Stop)
                              │
                              ▼
                       ┌─────────────┐
                       │   ACTIVE    │  valid_at = now, invalid_at = NULL, hidden = false
                       │ (en prompt) │
                       └──────┬──────┘
                              │
         ┌────────────────────┼────────────────────┬──────────────────────┐
         │                    │                    │                      │
   externalize          compress middle      soft_hide               session close
         │                    │                    │                      │
         ▼                    ▼                    ▼                      ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐      ┌──────────────────┐
   │ EXTERNALIZED │   │  COMPRESSED  │   │    HIDDEN    │      │  INVALIDATED      │
   │ (en memories) │   │ (en summary) │   │ (reversible) │      │ (digest cubre)    │
   │ externalized_at│  │ compressed_at│   │ hidden=true  │      │ invalid_at=now    │
   │ = now         │   │ = now        │   │              │      │                   │
   └──────┬────────┘   └──────────────┘   └──────┬───────┘      └──────────────────┘
          │                                       │
          │ memory_hybrid_search                  │ unhide_turns()
          ▼                                       ▼
   recuperable bajo demanda                 vuelve a ACTIVE
```

---

## 20. Cierre

v3 incorpora hallazgos validados de código de producción real (compresores, memoria
persistente, grafos bitemporales, IDEs con LLM en loop) y los traduce a una
re-implementación nativa SEAL.

**Cero dependencias externas en runtime:** Ollama local + PostgreSQL + Qdrant + Neo4j.
Todo dentro del perímetro soberano.

Capas 1-3 ya están en producción (commit `fc79b414`). v3 define los upgrades
arquitecturales (tool flatten, soft-hide, bitemporalidad, summary estructurado) y
añade Capa 4 (Proactive Turn Extraction) como red de seguridad final contra crashes.

**Siguiente paso operativo:** ADA aplica upgrades a Capas 2-3 según Fase 2-3 de §15.
JARVIS implementa Capa 4 + hybrid_search + MCP tools nuevos según Fase 4-5.

*Spec listo para ejecución incremental. Cada fase entrega valor solo. Ninguna depende
de futuras — todas son independientes.*
