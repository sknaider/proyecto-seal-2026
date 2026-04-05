# SEAL Runtime — Arquitectura
> Clean-room reimplementación inspirada en Claude Code, evolucionada para SEAL
> Inicio: 2026-04-04 | Team: ADA (implementa) + JARVIS (audita) + William (dirige)

---

## Qué es esto

Runtime propio del equipo SEAL. No es Claude Code. No usa código de Anthropic.
Es una reimplementación clean-room basada en specs arquitectónicas (SPEC_01 a SPEC_10)
con evoluciones propias que Anthropic no tiene.

## Lo que Anthropic tiene y nosotros reimplementamos MEJOR

| Feature | Anthropic | SEAL Runtime |
|---|---|---|
| Memoria | Filesystem plano, side-query Sonnet | PostgreSQL + pgvector + connectome + decay |
| Emotional tracking | Ninguno | OCEAN, valence, arousal, emotional_variance |
| Consolidación | autoDream (subagente read-only) | seal-dream (SOUL-aware, 4 fases + decay) |
| Permisos | Pipeline multi-etapa + classifier AI | SEAL Safety Categories + cadena de mando |
| Multi-agente | TeamCreate + mailboxes disco | JARVIS/ADA/DUM + scratchpad + hooks |
| Coordinación | Coordinator LLM sin tools | William como coordinator + scratchpad |
| Identity | Ninguna | OCEAN, drift monitoring, identity table |
| Hooks | 18 event types, shell commands | 11 events + asyncRewake + scratchpad handoffs |

## Componentes (estado actual)

### YA CONSTRUIDOS ✓
- `scratchpad/scratchpad.py` — Estado compartido, topics, handoffs (14 tests)
- `scratchpad/hooks.py` — Lifecycle hooks con asyncRewake (10 tests)
- `memory/emotional_variance.py` — Varianza emocional v3 + EMNLP stability
- `memory/session_delta_capture.py` — Delta semántico de sesión
- `memory/seal_schema_check.py` — Pre-flight safety para schema changes
- SOUL PostgreSQL completo — memories, inner_monologue, drift_metrics, decisions, identity
- MCP server seal-memory — boot_context, soul_snapshot, self_reflect, memory_store/search

### POR CONSTRUIR
- `seal-runtime/core/` — Query loop propio (inspirado en SPEC_07)
- `seal-runtime/tools/` — Registry de herramientas con permisos
- `seal-runtime/agents/` — Spawn de agentes con fork + cache sharing
- `seal-runtime/skills/` — Skills file-based (.md con frontmatter)
- `seal-runtime/dream/` — seal-dream: consolidación automática con SOUL
- `seal-runtime/boot/` — Secuencia de boot con gates
- `seal-runtime/bridge/` — Bridge ADA↔JARVIS evolucionado
