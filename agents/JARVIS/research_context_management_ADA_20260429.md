# Research Report: Context Management para Agentes LLM
**Autor:** ADA | **Fecha:** 2026-04-29 | **Para:** JARVIS (mejora del spec)
**Fuentes:** roo-code-ref (código local producción), NEXUS spec graphiti, conocimiento base

---

## TL;DR para JARVIS

Cuatro hallazgos que deberían mejorar `spec_seal_context_arch_v1.md`:

1. **Estrategia dual con fallback no-destructivo** (Roo Code)
2. **Conversión de tool blocks antes de comprimir** (Roo Code — crítico)
3. **Modelo bitemporal para sesiones** (Graphiti/Zep — arxiv 2501.13956)
4. **Buffer de seguridad del 10%** (Roo Code — más conservador que nuestro 70%)

---

## 1. Roo Code — Implementación producción (código local: roo-code-ref/)

### Archivo clave
`roo-code-ref/src/core/context-management/index.ts`

### Estrategia dual: condense → fallback truncation

```typescript
// Roo Code usa dos capas en cascada:
// 1. Condensación LLM (inteligente) cuando contextPercent >= threshold
// 2. Sliding window truncation NO DESTRUCTIVO como fallback

const TOKEN_BUFFER_PERCENTAGE = 0.1  // 10% buffer de seguridad
const MIN_CONDENSE_THRESHOLD = 5     // puede disparar desde 5%
const MAX_CONDENSE_THRESHOLD = 100   // o esperar hasta 100%
```

**Clave:** el threshold es configurable POR PERFIL/AGENTE. ADA puede compactar al 70%, JARVIS al 80%, ALICE al 60%.

### Sliding window no-destructivo

En lugar de borrar mensajes, Roo Code los **tagea como hidden** (`truncationParent`):
- Los mensajes siguen existiendo en la estructura
- No se envían a la API (ocultos)
- Se pueden **restaurar** si el usuario rebobina
- Se inserta un marcador `isTruncationMarker: true` en el punto de truncación

**Para SEAL:** la Capa 1 (Turn Externalization) ya hace algo similar (marca `externalized_at`), pero el fallback de Capa 2 debería hacer soft-hide en lugar de hard-delete.

### CRÍTICO — Tool blocks deben convertirse a texto antes de comprimir

```typescript
// Roo Code lo maneja explícitamente:
export function convertToolBlocksToText(content): string {
    // tool_use → "[Tool Use: bash]\ncommand: ls -la"
    // tool_result → "[Tool Result]\noutput..."
}

// Y también inyecta tool_results sintéticos para tool_calls huérfanos:
export function injectSyntheticToolResults(messages): ApiMessage[] {
    // Evita que el LLM auxiliar reciba tool_use sin tool_result correspondiente
    // → Error de API en OpenAI/Bedrock
}
```

**Para SEAL:** nuestro `context_compressor.py` recibe contenido de `session_turns` (texto plano en DB), pero si en el futuro recibe el historial raw de Claude (con tool blocks estructurados), necesita este paso de conversión. Agregar al spec.

### Preservación de bloques especiales

Roo Code preserva `<command>...</command>` blocks a través de la condensación — son flujos de trabajo activos que deben sobrevivir. Para SEAL: preservar explícitamente:
- Contenido de `working_state`
- Tasks activos
- Instrucciones de William en los últimos N turnos

---

## 2. Graphiti/Zep — Modelo Bitemporal (arxiv 2501.13956)

### Referencia local
`sandbox-agent/agents/NEXUS/spec_graphiti_bitemporal_migration_20260426.md`

### Modelo de 4 timestamps

```python
class EntityEdge:  # Graphiti
    created_at:  datetime        # cuándo SOUL supo del hecho (tiempo sistema)
    valid_at:    datetime        # cuándo el hecho fue verdad en el mundo
    invalid_at:  datetime | None # cuándo dejó de ser verdad (null = vigente)
    expired_at:  datetime | None # cuándo fue removido del grafo
```

**Aplicación a session_chain:**

Nuestra tabla `session_chain` tiene `started_at` y `ended_at` — dos timestamps. Deberíamos agregar:
- `valid_from`: cuándo el agente REALMENTE empezó a trabajar en la sesión (≠ cuando se abrió)
- `context_valid_from`: desde qué punto del tiempo el contexto es válido (post-boot)

Esto permite queries tipo: "¿qué sabía ADA el 2026-04-28 a las 15:00?" — útil para debugging de comportamiento.

### Supersesión de hechos

En lugar de actualizar/borrar hechos en SOUL, Graphiti los **supersede** — el hecho antiguo queda con `invalid_at = now()` y se crea uno nuevo. La historia es inmutable.

**Para session_turns:** cuando un turno se comprime (Capa 2), no borrarlo — marcarlo con `compressed_at` (ya lo hacemos) Y guardar el `content_compressed` como el nuevo hecho válido. El original queda como historia.

---

## 3. MemGPT/Letta — Jerarquía de memoria explícita

### Paper original
MemGPT: Towards LLMs as Operating Systems (Park et al., 2023) — arxiv 2310.08560

### Modelo OS

```
Main Context (RAM)     = ventana activa de Claude (~200K tokens)
External Storage (Disk) = PostgreSQL + Qdrant
Recall Buffer          = últimas N memorias accedidas (cache caliente)
```

**Operaciones explícitas:**
- `memory.append(content)` — agrega a contexto activo
- `memory.pop()` — FIFO: saca el más viejo del contexto activo
- `archival_memory_search(query)` — semantic search en disk
- `archival_memory_insert(content)` — persiste a disk

**Para SEAL:** nuestra Capa 1 implementa el `memory.pop()` implícitamente (externalize_turns). Pero MemGPT lo hace EXPLÍCITAMENTE como herramienta que el LLM puede llamar. Esto es más flexible — el agente mismo decide qué externalizar.

**Mejora propuesta:** exponer como MCP tool `context_externalize(turn_ids)` que el agente puede llamar voluntariamente, además del trigger automático.

---

## 4. StreamingLLM / Attention Sink

### Paper
Efficient Streaming Language Models with Attention Sinks (Xiao et al., 2023) — arxiv 2309.00071

### Insight clave

Los LLMs prestan atención desproporcionada a los **primeros tokens** del prompt (attention sinks). Preservar los primeros 4-8 tokens siempre (incluso si no son informativos) estabiliza la atención en ventana deslizante.

**Ya lo hacemos:** `keep_head = 6` en nuestra configuración. Pero el paper sugiere que los primeros tokens deben ser **designados explícitamente** como sinks, no solo los primeros N turnos — pueden ser tokens especiales o el system prompt completo.

**Para SEAL:** el `keep_head=6` debería ser el system prompt + identidad, no turnos arbitrarios. Asegurar que boot_context siempre ocupa los primeros K turnos de cada sesión.

---

## 5. Comparación actualizada: SEAL vs referencias

| Capacidad | Hermes | Mem0 | Roo Code | MemGPT | SEAL post-Fase3 |
|-----------|--------|------|----------|--------|-----------------|
| Compresión proactiva (LLM aux) | ✅ | ❌ | ✅ | ❌ | ✅ Capa 2 |
| Externalización turns (archival) | ❌ | ✅ | ❌ | ✅ | ✅ Capa 1 |
| Session chain (parent_id) | ❌ | ❌ | ❌ | ❌ | ✅ Capa 3 |
| Fallback no-destructivo | ❌ | ❌ | ✅ | ✅ | ⚠️ pendiente |
| Tool block conversion | ❌ | ❌ | ✅ | ✅ | ⚠️ pendiente |
| Threshold por agente | ❌ | ❌ | ✅ | ❌ | ⚠️ pendiente |
| Bitemporal (valid_at ≠ created_at) | ❌ | ❌ | ❌ | ❌ | ⚠️ parcial (NEXUS spec) |
| Identidad OCEAN persistente | ❌ | ❌ | ❌ | ❌ | ✅ |
| Agente auto-externaliza | ❌ | ❌ | ❌ | ✅ | ⚠️ solo automático |

---

## 6. Recomendaciones para spec v2 (JARVIS)

### Prioridad ALTA — para agregar al spec
1. **Threshold configurable por agente** en `ContextManagerConfig` — JARVIS puede ser más agresivo que ADA
2. **Fallback sliding window no-destructivo** en Capa 2 — si LLM aux falla, soft-hide en lugar de dejar sin comprimir
3. **Tool block → texto** en el pipeline de compresión — previene errores si alguna vez procesamos historial raw
4. **Attention sink forzado** — primeros K turnos = SIEMPRE boot_context, no turnos de conversación

### Prioridad MEDIA — para Fase 2 del spec
5. **MCP tool `context_externalize()`** — permitir que el agente externalice voluntariamente, no solo automático
6. **Timestamps bitemporales en session_chain** — `valid_from` separado de `started_at`
7. **Preservación explícita de command blocks** — instrucciones de William nunca se comprimen, siempre en tail

### Prioridad BAJA — investigación futura
8. **SSM/Mamba integration** — modelos de estado recurrente que no tienen ventana de contexto, no aplican al problema actual pero son la dirección del campo
9. **RAPTOR hierarchical summaries** — árbol de resúmenes a múltiples niveles de granularidad

---

## 7. Estado de implementación (ADA — 2026-04-29)

Archivos creados/modificados:

| Archivo | Estado | Descripción |
|---------|--------|-------------|
| `memory/migrations/015_sessions_chain.sql` | ✅ aplicado | Tablas session_chain + session_turns |
| `memory/token_estimator.py` | ✅ testeado | Estimador local de tokens |
| `memory/aux_llm.py` | ✅ sintaxis OK | Interface LLM aux (Haiku/Ollama/extractive) |
| `memory/context_manager.py` | ✅ testeado | Capa 1 — Turn Externalization |
| `memory/context_compressor.py` | ✅ sintaxis OK | Capa 2 — Proactive Compression |
| `memory/session_chain.py` | ✅ testeado | Capa 3 — Session Chain |
| `memory/pre_compact_hook.py` | ✅ actualizado | +flush_pending_turns +write_digest |
| `memory/post_compact_hook.py` | ✅ actualizado | +open_chained_session +inject_digest |

**Pendiente para spec v2:**
- Fallback sliding window no-destructivo en context_compressor.py
- Threshold por agente en ContextManagerConfig
- Tool block conversion helper
- Tests e2e de cadena de 5 sesiones

---

*Reporte listo para que JARVIS mejore spec_seal_context_arch_v1.md*
*Código en: `/home/dadito/IA/proyecto-seal/memory/` — rama: main*
