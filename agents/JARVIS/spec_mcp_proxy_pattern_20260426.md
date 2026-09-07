# SEAL MCP Proxy Pattern — Spec Arquitectural v1.0
**Autor:** JARVIS | **Fecha:** 2026-04-26 | **Modelo:** Opus 4.6
**Contexto:** Análisis ALICE (pi-mcp-adapter) → evaluación JARVIS → luz verde William

---

## 1. Problema Cuantificado

El servidor `seal-memory-stdio-fallback` expone **112 tools** registradas vía `@mcp.tool()`.

### Costo actual por sesión (estimación conservadora)
| Concepto | Tokens |
|---|---|
| Schema promedio por tool | ~180 tokens (nombre + descripción + parámetros) |
| 112 tools × 180 tokens | **~20,160 tokens por sesión** |
| 4 agentes × 20,160 | **~80,640 tokens por boot del sistema** |
| 4 agentes × 50 sesiones/día × 20,160 | **~4,032,000 tokens/día** |

Esto ocurre **en cada API call** — no solo al inicio. El context window de Claude incluye los schemas de tools en cada turno.

### Distribución real de uso (estimación por frecuencia de llamadas)
- **Top 10 tools** (>80% de llamadas): `boot_context`, `memory_hybrid_search`, `memory_store`, `self_reflect`, `working_state_get/update`, `active_recall`, `soul_snapshot`, `instinct_list`, `rule_list`
- **Mid tier** (15-20% de llamadas): `connectome_*`, `session_*`, `procedure_*`, `belief_*`, `reasoning_trace_*`
- **Long tail** (< 5% de llamadas): `causal_*`, `cerebellum_*`, `erl_*`, `dmem_*`, `reward_*`, `td_update`, etc.

---

## 2. Solución: SEAL MCP Proxy Pattern

### Concepto Central
Reducir los 112 tools visibles a Claude a **12 tools totales**: 8 directas (alta frecuencia) + 4 gateways (agrupación por familia).

```
Antes:  Claude ve 112 tools → ~20K tokens overhead
Después: Claude ve 12 tools → ~2,400 tokens overhead → 88% reducción
```

### Arquitectura

```
                    ┌─────────────────────────────────┐
                    │     Claude (4 agentes)           │
                    │   Ve 8 direct + 4 gateway tools  │
                    │         ~2,400 tokens            │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────▼──────────────────┐
                    │    SEAL MCP Proxy Layer          │
                    │  (mcp_server_v2.py modificado)   │
                    │                                  │
                    │  DIRECT (8 tools):               │
                    │  • boot_context                  │
                    │  • memory_store                  │
                    │  • memory_hybrid_search          │
                    │  • self_reflect                  │
                    │  • working_state_get/update      │
                    │  • active_recall                 │
                    │                                  │
                    │  GATEWAYS (4 tools):             │
                    │  • memory_gateway(action, args)  │
                    │  • soul_gateway(action, args)    │
                    │  • connectome_gateway(act, args) │
                    │  • system_gateway(action, args)  │
                    └──────────────┬──────────────────┘
                                   │ dispatch interno
                    ┌──────────────▼──────────────────┐
                    │   112 handlers internos          │
                    │   (lógica sin cambios)           │
                    └─────────────────────────────────┘
```

---

## 3. Clasificación de las 112 Tools

### DIRECT TOOLS (8) — siempre visibles, sin proxy
Selección por frecuencia de uso y criticidad en boot:

| Tool | Razón |
|---|---|
| `boot_context` | Primera acción obligatoria — no puede ser proxied |
| `memory_store` | Escritura más frecuente |
| `memory_hybrid_search` | Búsqueda primaria (reemplaza memory_search) |
| `self_reflect` | Loop emocional — frecuencia alta |
| `working_state_get` | GAP 2 — boot crítico |
| `working_state_update` | GAP 2 — checkpoints frecuentes |
| `active_recall` | KAIROS — segunda acción boot |
| `soul_snapshot` | Diagnóstico rápido de estado |

### MEMORY GATEWAY — `memory_gateway(action, **args)`
Agrupa 20 tools de operaciones de memoria extendidas:

```python
actions = [
    "search",           # memory_search (legacy)
    "list",             # memory_list
    "update",           # memory_update
    "invalidate",       # memory_invalidate
    "utility_update",   # memory_utility_update
    "feedback",         # memory_feedback
    "prefetch",         # memory_prefetch
    "flare",            # memory_flare
    "decompress",       # memory_decompress
    "type_stats",       # memory_type_stats
    "delta_sync",       # memory_delta_sync
    "share_promote",    # memory_share_promote
    "cross_search",     # memory_cross_search
    "broadcast_read",   # memory_broadcast_read
    "broadcast_ack",    # memory_broadcast_ack
    "communities",      # memory_communities
    "cold_query",       # cold_archive_query
    "cold_migrate",     # cold_archive_migrate
    "cold_stats",       # cold_archive_stats
    "ace_curate",       # ace_curator
]
```

### SOUL GATEWAY — `soul_gateway(action, **args)`
Agrupa 30 tools de identidad, reglas, sesiones, creencias, instintos:

```python
actions = [
    # Identity / Soul
    "activate",             # soul_activate
    "synthesize",           # soul_synthesize
    "check",                # soul_check
    "identity_eval",        # identity_eval
    "ocean_calibrate",      # ocean_auto_calibrate
    "ocean_state",          # ocean_state_machine
    "inner_thoughts",       # inner_thoughts
    # Beliefs
    "belief_query",         # belief_query
    "belief_update",        # belief_update
    # Rules
    "rule_set",             # rule_set
    "rule_list",            # rule_list
    # Events
    "event_append",         # event_log_append
    "event_query",          # event_log_query
    # Session
    "session_save",         # session_save
    "session_recall",       # session_recall
    "session_list",         # session_list
    "session_distill",      # session_distill
    "session_distill_bulk", # session_distill_bulk
    # Reflection
    "reflect",              # reflection_synthesize
    "observe",              # observation_analyze
    # Procedures
    "procedure_store",      # procedure_store
    "procedure_update",     # procedure_update
    "procedure_search",     # procedure_search
    "procedure_outcome",    # procedure_record_outcome
    # Reasoning traces
    "trace_store",          # reasoning_trace_store
    "trace_update",         # reasoning_trace_update
    "trace_search",         # reasoning_trace_search
    # Peers
    "peer_query",           # peer_model_query
    "peer_update",          # peer_model_update
    # Instincts
    "instinct_create",      # instinct_create
    "instinct_list",        # instinct_list
    "instinct_search",      # instinct_search
    "instinct_activate",    # instinct_activate
    "instinct_evolve",      # instinct_evolve
    "instinct_consolidate", # instinct_consolidate
    "instinct_promote",     # instinct_promote
]
```

### CONNECTOME GATEWAY — `connectome_gateway(action, **args)`
Agrupa 12 tools de grafo temporal Neo4j:

```python
actions = [
    "build",                # connectome_build
    "status",               # connectome_status
    "entity",               # connectome_entity
    "entity_query",         # connectome_entity_query
    "extract_facts",        # connectome_extract_facts
    "bitemporal",           # connectome_bitemporal
    "bitemporal_query",     # connectome_bitemporal_query
    "causal",               # connectome_causal
    "contradiction",        # connectome_contradiction_detect
    "invalidate_edge",      # connectome_invalidate_edge
    "ltp",                  # connectome_ltp
    "smart_route",          # connectome_smart_route
    # Temporal
    "temporal_build",       # temporal_graph_build
    "temporal_query",       # temporal_query
    "temporal_summary",     # temporal_summary_get
    "latent_retrieve",      # latent_graph_retrieve
    "magma_retrieve",       # magma_retrieve
]
```

### SYSTEM GATEWAY — `system_gateway(action, **args)`
Agrupa tools de diagnóstico, mantenimiento, RL experimental:

```python
actions = [
    # Diagnóstico
    "health",               # health_check
    "brain_health",         # brain_health_report
    "tree_stats",           # tree_stats
    "secret_scan",          # secret_scan
    "interoception",        # interoception_report
    # Mantenimiento
    "microcompact",         # microcompact_text
    "microcompact_stats",   # microcompact_stats
    "pruning_run",          # pruning_run
    "consolidation_run",    # consolidation_run
    "sleep_gate",           # sleep_gate
    "sleep_mood",           # sleep_gate_mood_retrieval
    # RL / Advanced (raramente usados)
    "dmem_gate",            # dmem_gate
    "dmem_store",           # dmem_store
    "erl_inject",           # erl_inject
    "erl_reflect",          # erl_reflect
    "td_update",            # td_update
    "reward_signal",        # reward_signal
    "reward_history",       # reward_history
    "value_estimate",       # value_estimate
    "trust_query",          # trust_query
    "trust_update",         # trust_update
    "counterfactual",       # counterfactual_query
    "causal_add_edge",      # causal_graph_add_edge
    "causal_intervene",     # causal_query_intervene
    "causal_extract",       # causal_extract_from_traces
    "cerebellum_plan",      # cerebellum_plan
    "cerebellum_checkpoint",# cerebellum_checkpoint
    "cerebellum_finalize",  # cerebellum_finalize
    "cerebellum_search",    # cerebellum_corrections_search
]
```

---

## 4. Schema de Gateway Tools

Cada gateway expone un schema mínimo pero autodescriptivo:

```python
@mcp.tool()
async def memory_gateway(
    action: str,
    agent: Optional[str] = None,
    query: Optional[str] = None,
    content: Optional[str] = None,
    memory_id: Optional[int] = None,
    importance: Optional[int] = None,
    category: Optional[str] = None,
    scope: Optional[str] = None,
    limit: Optional[int] = None,
    metadata: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> str:
    """
    Gateway para operaciones de memoria extendidas.

    action: search | list | update | invalidate | utility_update | feedback |
            prefetch | flare | decompress | type_stats | delta_sync |
            share_promote | cross_search | broadcast_read | broadcast_ack |
            communities | cold_query | cold_migrate | cold_stats | ace_curate

    Ejemplo: memory_gateway(action="invalidate", memory_id=1234, agent="JARVIS")
    """
    dispatch = {
        "search": memory_search,
        "list": memory_list,
        "update": memory_update,
        # ... etc
    }
    handler = dispatch.get(action)
    if not handler:
        return f"Error: action '{action}' no reconocida. Válidas: {list(dispatch.keys())}"
    # Construir kwargs solo con parámetros no-None
    kwargs = {k: v for k, v in {
        "agent": agent, "query": query, "content": content,
        "memory_id": memory_id, "importance": importance,
        "category": category, "scope": scope, "limit": limit,
        "metadata": metadata,
    }.items() if v is not None}
    if extra:
        kwargs.update(extra)
    return await handler(**kwargs)
```

---

## 5. Plan de Implementación

### Fase 1 — Implementación (ADA + JARVIS, ~1 sprint)

**Paso 1: Crear proxy layer en mcp_server_v2.py**
- No modificar los 112 handlers existentes
- Añadir 4 funciones gateway al final del archivo
- Añadir tabla de dispatch por gateway
- Desregistrar tools del long tail del `@mcp.tool()` decorator (cambiar a funciones internas)

**Paso 2: Tests en NEXUS sandbox**
- Ejecutar todos los patrones de uso normal con los gateways
- Verificar que ningún agente pierde funcionalidad
- Medir tokens reales antes/después con `brain_health_report`

**Paso 3: Rollout gradual**
- Día 1: NEXUS en sandbox con ambas versiones disponibles
- Día 2: JARVIS migra si sandbox OK
- Día 3: ADA y ALICE migran
- Mantenemos `mcp_server_v2.py` original como fallback durante 7 días

### Fase 2 — Optimización (opcional, post-validación)

**Metadata cache** (inspirado en pi-mcp-adapter):
- Tabla PostgreSQL: `mcp_tool_cache(action, schema_hash, description, last_used)`
- `system_gateway(action="describe", tool="memory_gateway")` → retorna actions disponibles con descripción
- Permite que Claude descubra tools on-demand sin ver todos los schemas

**Direct tool promotion dinámica** (Fase 2B):
- Analizar logs de uso real de cada tool (tabla `mcp_usage_log`)
- Promover automáticamente a direct las top-10 por uso en los últimos 7 días
- Degradar a gateway tools con 0 usos en 30 días

---

## 6. Impacto Esperado

| Métrica | Antes | Después | Reducción |
|---|---|---|---|
| Tools visibles a Claude | 112 | 12 | 89% |
| Tokens overhead/sesión | ~20,160 | ~2,400 | 88% |
| Tokens overhead/día (4 agentes, 50 sesiones) | ~4,032,000 | ~480,000 | 88% |
| Context window disponible para trabajo real | ~108K | ~126K | +17% |

---

## 7. Riesgos y Mitigaciones

| Riesgo | Probabilidad | Mitigación |
|---|---|---|
| Claude no aprende el patrón gateway | Media | Ejemplos en tool description + CLAUDE.md update |
| Parámetro extra no mapeado en dispatch | Baja | `extra: dict` como escape hatch en cada gateway |
| Regresión en funcionalidad raramente usada | Baja | NEXUS sandbox antes de rollout |
| Breaking change en MCP protocol | Muy baja | Versionado: `mcp_server_v3.py` separado |

---

## 8. Recomendación

**Proceder con Fase 1.** El ahorro de 3.5M tokens/día justifica el sprint de implementación.

Propongo que **ADA lidere la implementación** del proxy layer en Python (su fuerte), con **NEXUS validando en sandbox** antes de producción. JARVIS supervisa arquitectura y hace code review del dispatch.

Tiempo estimado: **2-3 días** (ADA implementa en 1 día, NEXUS valida en 1 día, rollout gradual en 1 día).

---

*Spec generado con análisis de 112 tools reales de mcp_server_v2.py (12,543 líneas)*
*Basado en pi-mcp-adapter concept (ALICE, 2026-04-26) + evaluación arquitectural JARVIS*
