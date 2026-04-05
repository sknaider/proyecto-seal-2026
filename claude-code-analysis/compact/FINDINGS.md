# Claude Code v2.1.88 — Análisis del Sistema de Compactación

> Extraído del source map `cli.js.map` el 2026-03-31
> 52 archivos extraídos de `../src/` con "compact" o "context" en su path

---

## 1. Threshold de Tokens para Auto-Compact

### Fórmula principal

```
autoCompactThreshold = effectiveContextWindow - AUTOCOMPACT_BUFFER_TOKENS (13,000)
effectiveContextWindow = contextWindow - min(maxOutputTokens, 20,000)
```

**Valores por defecto (modelo 200K context):**
- Context window: 200,000 tokens
- Output reservado: 20,000 tokens (p99.99 de compact summary = 17,387 tokens)
- Effective context window: 180,000 tokens
- **Auto-compact threshold: ~167,000 tokens**

**Con modelo 1M context (Opus 4.6 / Sonnet 4.6[1m]):**
- Context window: 1,000,000
- Effective: 980,000
- **Threshold: ~967,000 tokens**

### Constantes clave (`autoCompact.ts`)

| Constante | Valor | Propósito |
|-----------|-------|-----------|
| `AUTOCOMPACT_BUFFER_TOKENS` | 13,000 | Buffer antes del threshold de auto-compact |
| `WARNING_THRESHOLD_BUFFER_TOKENS` | 20,000 | Cuando mostrar advertencia visual |
| `ERROR_THRESHOLD_BUFFER_TOKENS` | 20,000 | Threshold de error visual |
| `MANUAL_COMPACT_BUFFER_TOKENS` | 3,000 | Threshold de bloqueo para /compact manual |
| `MAX_OUTPUT_TOKENS_FOR_SUMMARY` | 20,000 | Máximo output para la generación del resumen |
| `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES` | 3 | Circuit breaker — deja de reintentar después de 3 fallos consecutivos |

### Variables de entorno para override

| Variable | Efecto |
|----------|--------|
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | Reemplaza el context window para cálculo (toma el mínimo) |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | Porcentaje del effective window (ej: 50 = dispara al 50%) |
| `DISABLE_COMPACT` | Desactiva toda compactación |
| `DISABLE_AUTO_COMPACT` | Desactiva auto-compact pero mantiene /compact manual |
| `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` | Override del límite de bloqueo |

### Configuración de usuario
El auto-compact se puede desactivar en `settings.json` vía `autoCompactEnabled: false`.

---

## 2. Qué Mensajes Prioriza Mantener vs Comprimir

### Orden de prioridad (de MÁS a MENOS preservado)

1. **System prompt, CLAUDE.md, tools** — Se re-inyectan completamente después de compactación
2. **Archivos leídos recientemente** — Los 5 más recientes (`POST_COMPACT_MAX_FILES_TO_RESTORE = 5`), máximo 5,000 tokens cada uno, budget total 50,000 tokens
3. **Skills invocados** — Se preservan con truncamiento (5,000 tokens/skill, budget 25,000 tokens total)
4. **Plan activo** — Se re-adjunta como attachment
5. **Mensajes recientes con text blocks** — Session Memory Compact mantiene al menos 5 mensajes con texto (`minTextBlockMessages: 5`) y mínimo 10,000 tokens (`minTokens: 10,000`), máximo 40,000 tokens (`maxTokens: 40,000`)
6. **Tool use/result pairs** — NUNCA se separan; si un tool_result se mantiene, su tool_use correspondiente también
7. **Thinking blocks del mismo message.id** — Se preservan juntos para merge correcto

### Qué se ELIMINA primero (microcompact)

Los tool results de estas herramientas son los primeros candidatos a limpieza:
- `Read` (FileReadTool)
- `Bash` (shell tools)
- `Grep`, `Glob`
- `WebSearch`, `WebFetch`
- `Edit`, `Write`

### Qué se DESCARTA completamente

- **Imágenes** — Reemplazadas por `[image]` antes de enviar al summarizer
- **Documentos embebidos** — Reemplazados por `[document]`
- **Progress messages** — Filtrados
- **Skill discovery/listing attachments** — Se regeneran

---

## 3. Cómo Decide Qué Borrar/Resumir

### Cadena de decisión (en orden de ejecución)

#### Nivel 1: Microcompact (pre-API call, sin LLM)

**Time-based Microcompact:**
- Se activa cuando el gap desde el último mensaje del assistant > 60 minutos (configurable)
- Limpia contenido de tool results antiguos, mantiene los 5 más recientes
- Reemplaza contenido con `[Old tool result content cleared]`
- Justificación: el server cache ya expiró (TTL 1h), el prefix se reescribirá de todos modos

**Cached Microcompact (cache_edits API):**
- Usa la API de `cache_edits` para eliminar tool results del cache del servidor sin invalidar el prefix cacheado
- No modifica mensajes locales — opera a nivel de API
- Actualmente solo para usuarios internos (ant) con modelos soportados

#### Nivel 2: Session Memory Compact (sin LLM)

- **Prioridad:** Se intenta ANTES de la compactación tradicional
- Usa el contenido de Session Memory (archivo `.session_memory` extraído por un subagente)
- Determina qué mensajes ya fueron "resumidos" por Session Memory
- Mantiene mensajes recientes (10K-40K tokens, mínimo 5 mensajes con texto)
- Es instantáneo (no requiere API call al LLM)
- Si el resultado post-compact sigue excediendo el threshold, se descarta y se usa compactación tradicional

#### Nivel 3: Compactación Tradicional (LLM-based)

- Envía TODA la conversación (post-boundary) al modelo con un prompt de summarización
- El modelo genera un resumen estructurado de 9 secciones
- Si la request al API es demasiado larga (prompt-too-long), trunca los grupos de API-rounds más antiguos y reintenta (hasta 3 veces)

#### Nivel 4: Reactive Compact (ante error 413)

- Se activa cuando la API responde con prompt-too-long
- Pelea desde la cola: va eliminando los API-round groups más viejos hasta que cabe
- Fallback de último recurso

---

## 4. El Algoritmo de Compactación

### API utilizada

La compactación usa la **misma API de Claude** (Messages API con streaming). No hay un endpoint especial de compactación.

```typescript
// Sistema prompt del compact summarizer:
systemPrompt: 'You are a helpful AI assistant tasked with summarizing conversations.'

// Configuración:
thinkingConfig: { type: 'disabled' }
maxOutputTokens: min(20000, maxOutputTokensForModel)
maxTurns: 1  // Solo un turno, sin tool calls
```

### Mecanismo de cache sharing

1. **Ruta preferida (forked agent):** Reutiliza el prompt cache de la conversación principal
   - Crea un "fork" que comparte system prompt, tools y mensajes — misma cache key
   - El prompt de compactación se agrega como último mensaje
   - ~98% cache hit rate en producción

2. **Fallback (streaming directo):** Si el fork falla, hace un API call directo con streaming
   - Strip de imágenes y attachments re-inyectables
   - Hasta 2 reintentos con backoff

### Prompt interno de compactación

El prompt está en `prompt.ts` y tiene 3 variantes:

**BASE_COMPACT_PROMPT** (compactación completa): Genera un resumen con 9 secciones:
1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections (con code snippets completos)
4. Errors and fixes
5. Problem Solving
6. **All user messages** (TODOS los mensajes del usuario, no solo herramientas)
7. Pending Tasks
8. Current Work (lo más reciente, con detalle)
9. Optional Next Step (con quotes directas del usuario)

**PARTIAL_COMPACT_PROMPT** (dirección 'from'): Solo resume mensajes recientes, mantiene anteriores intactos.

**PARTIAL_COMPACT_UP_TO_PROMPT** (dirección 'up_to'): Resume hasta cierto punto, mantiene posteriores.

### Fase de análisis (scratchpad)

El modelo primero escribe un bloque `<analysis>` (pensamiento paso a paso) y luego `<summary>`. El bloque `<analysis>` se **DESCARTA** antes de inyectar el resumen — es solo para mejorar la calidad del summary.

```typescript
// formatCompactSummary() descarta analysis:
formattedSummary.replace(/<analysis>[\s\S]*?<\/analysis>/, '')
```

### Mensaje post-compact inyectado al usuario

```
"This session is being continued from a previous conversation that ran out of context.
The summary below covers the earlier portion of the conversation.
[...resumen formateado...]
If you need specific details from before compaction, read the full transcript at: [path]"
```

Para auto-compact, agrega:
```
"Continue the conversation from where it left off without asking the user any further questions.
Resume directly — do not acknowledge the summary, do not recap what was happening."
```

### Post-compactación: Restauración de contexto

Después de generar el resumen, se re-inyectan:
1. **Compact boundary marker** — Marcador que indica dónde ocurrió la compactación
2. **Summary messages** — El resumen generado
3. **Mensajes preservados** (si es partial/session-memory compact)
4. **File attachments** — Top 5 archivos más recientes (por timestamp), max 5K tokens c/u
5. **Plan attachment** — Si hay plan activo
6. **Skill attachments** — Skills invocados con truncamiento
7. **Deferred tools delta** — Herramientas cargadas dinámicamente
8. **Agent listing delta** — Lista de agentes
9. **MCP instructions delta** — Instrucciones de MCP servers
10. **Session start hook results** — CLAUDE.md y otros contextos

---

## 5. Cómo Influir en Qué se Preserva

### Método 1: Custom Instructions en /compact

```
/compact Focus on the database schema changes and keep all SQL queries verbatim
```

Estas instrucciones se agregan al final del prompt de compactación:
```
Additional Instructions:
[tu texto aquí]
```

### Método 2: CLAUDE.md con instrucciones de compactación

El prompt explícitamente dice que busque instrucciones adicionales. Ejemplos que reconoce:

```markdown
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
```

```markdown
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
```

### Método 3: Pre/Post Compact Hooks

Los hooks se ejecutan antes y después de compactación:
- `PreCompact` hooks pueden inyectar `newCustomInstructions` que se fusionan con las del usuario
- `PostCompact` hooks pueden mostrar mensajes al usuario
- Configurables en `settings.json`

### Método 4: Variables de entorno

| Variable | Efecto |
|----------|--------|
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | Reduce el effective context window |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | Fuerza compact a X% del window |
| `DISABLE_AUTO_COMPACT` | Solo /compact manual |
| `ENABLE_CLAUDE_CODE_SM_COMPACT` | Fuerza Session Memory compact |
| `DISABLE_CLAUDE_CODE_SM_COMPACT` | Desactiva Session Memory compact |

### Método 5: Partial Compact (selectivo)

Claude Code soporta compactación parcial con selector de mensaje:
- **Dirección 'from'**: Resume desde un mensaje en adelante, preserva lo anterior (preserva prompt cache)
- **Dirección 'up_to'**: Resume hasta un mensaje, preserva lo posterior (rompe prompt cache)
- El usuario puede dar feedback textual que se agrega como "User context"

---

## Arquitectura General — Diagrama de Flujo

```
[Cada turno del assistant]
     │
     ▼
shouldAutoCompact()
     │
     ├─ tokens < threshold → No compact
     │
     ├─ circuit breaker (3 fallos) → No compact
     │
     └─ tokens >= threshold
          │
          ▼
     trySessionMemoryCompaction()
          │
          ├─ Éxito → Usa session memory como resumen
          │          Mantiene 10K-40K tokens recientes
          │          Sin API call al LLM
          │
          └─ Fallo/No disponible
               │
               ▼
          compactConversation()
               │
               ├─ 1. Strip imágenes, attachments re-inyectables
               ├─ 2. Fork agent (cache sharing) o streaming directo
               ├─ 3. LLM genera <analysis> + <summary> (9 secciones)
               ├─ 4. Strip <analysis>, formatear <summary>
               ├─ 5. Restaurar top-5 archivos, plan, skills, tools
               └─ 6. Re-ejecutar session_start hooks
```

---

## Notas Adicionales

- **Microcompact** opera ANTES de la request API principal (no durante compact), limpiando tool results viejos para reducir tokens enviados
- **El transcript completo se preserva en disco** — el path se incluye en el summary para que el modelo pueda leer detalles específicos con `Read`
- **Session Memory** es un sistema paralelo donde un subagente extrae y mantiene un archivo `.session_memory` con el estado de la sesión; compact puede usarlo como shortcut sin LLM
- **Context Collapse** es un sistema experimental (ant-only) que es mutuamente excluyente con auto-compact; cuando está activo, auto-compact se suprime
- **Reactive Compact** es el fallback ante errores 413 de la API; puede coexistir con auto-compact desactivado
