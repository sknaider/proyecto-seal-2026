# Analisis de Hooks y Memory — Claude Code v2.1.88

> Extraido del source map `cli.js.map` el 2026-03-31
> 71 archivos fuente extraidos y analizados

---

## 1. Sistema de Hooks

### 1.1 Eventos disponibles (HookEvent)

Los eventos se definen en `src/entrypoints/agentSdkTypes.js` (referenciados por `HOOK_EVENTS`). Del analisis del codigo, los eventos son:

| Evento | Trigger | Matcher |
|--------|---------|---------|
| `PreToolUse` | Antes de ejecutar una herramienta | `tool_name` (ej: "Write", "Bash") |
| `PostToolUse` | Despues de ejecutar una herramienta | `tool_name` |
| `PostToolUseFailure` | Cuando una herramienta falla | `tool_name` |
| `PermissionRequest` | Cuando se solicita un permiso | `tool_name` |
| `PermissionDenied` | Cuando se deniega un permiso | `tool_name` |
| `UserPromptSubmit` | Cuando el usuario envia un prompt | - |
| `SessionStart` | Al iniciar sesion | `source` (startup/resume/clear/compact) |
| `SessionEnd` | Al terminar sesion | `reason` |
| `Setup` | Configuracion inicial | `trigger` (init/maintenance) |
| `Stop` | Cuando el modelo deja de generar | - |
| `StopFailure` | Cuando Stop falla | `error` |
| `SubagentStart` | Al iniciar un subagente | `agent_type` |
| `SubagentStop` | Al detener un subagente | `agent_type` |
| `PreCompact` | Antes de compactacion | `trigger` |
| `PostCompact` | Despues de compactacion | `trigger` |
| `Notification` | Notificaciones internas | `notification_type` |
| `TeammateIdle` | Cuando un teammate esta idle | - |
| `TaskCreated` | Al crear una tarea | - |
| `TaskCompleted` | Al completar una tarea | - |
| `ConfigChange` | Cambio de configuracion | `source` |
| `CwdChanged` | Cambio de directorio de trabajo | - |
| `FileChanged` | Cambio en archivo vigilado | `basename(file_path)` |
| `InstructionsLoaded` | Al cargar instrucciones | `load_reason` |
| `Elicitation` | Solicitud de informacion al usuario | `mcp_server_name` |
| `ElicitationResult` | Resultado de elicitacion | `mcp_server_name` |
| `WorktreeCreate` | Al crear worktree git | - |

### 1.2 Tipos de hooks

Hay **4 tipos persistentes** (configurables en settings.json) y **2 tipos en memoria**:

**Persistentes (settings.json):**
1. **`command`** — Ejecuta un comando shell (bash/powershell)
   - Campos: `command`, `shell` (bash/powershell), `timeout`, `if` (condicion), `async`, `asyncRewake`, `once`
2. **`prompt`** — Evalua un prompt con un LLM
   - Campos: `prompt`, `model` (default: modelo rapido pequeno), `timeout`, `if`, `once`
3. **`agent`** — Ejecuta un agente verificador
   - Campos: `prompt`, `model` (default: Haiku), `timeout`, `if`, `once`
4. **`http`** — POST a una URL
   - Campos: `url`, `headers`, `allowedEnvVars`, `timeout`, `if`, `once`

**Solo en memoria (no persistentes):**
5. **`callback`** — Callback TypeScript interno
6. **`function`** — Funcion de validacion TypeScript (session-scoped)

### 1.3 Donde se registran

Hooks se cargan de estas fuentes (en orden de prioridad):
- `policySettings` — Politicas organizacionales
- `userSettings` — `~/.claude/settings.json`
- `projectSettings` — `.claude/settings.json` (en el repo)
- `localSettings` — `.claude/settings.local.json`
- `pluginHook` — Plugins (`~/.claude/plugins/*/hooks/hooks.json`)
- `sessionHook` — Hooks de sesion (en memoria, temporales)
- `builtinHook` — Hooks internos de Claude Code

La estructura en settings.json es:
```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write",
        "hooks": [
          { "type": "command", "command": "echo 'antes de escribir'" }
        ]
      }
    ]
  }
}
```

### 1.4 Flujo de ejecucion

1. `getMatchingHooks()` busca hooks configurados para el evento + matcher
2. Filtra por condicion `if` (usa sintaxis de reglas de permisos, ej: `Bash(git *)`)
3. Para cada hook:
   - **command**: Spawns un proceso shell, pasa el input como JSON via stdin/env
   - **prompt**: Ejecuta sideQuery con un modelo LLM
   - **agent**: Ejecuta un agente verificador con herramientas
   - **http**: POST al URL con el input JSON
4. Parsea la salida (JSON o texto plano)
5. Agrega resultados

### 1.5 Timeouts

| Contexto | Timeout default |
|----------|----------------|
| Hooks de herramientas (Pre/Post) | **10 minutos** (`TOOL_HOOK_EXECUTION_TIMEOUT_MS = 10 * 60 * 1000`) |
| SessionEnd hooks | **1.5 segundos** (`SESSION_END_HOOK_TIMEOUT_MS_DEFAULT = 1500`) |
| Async hooks | **15 segundos** (default `asyncTimeout`) |
| Function hooks | **5 segundos** (default) |

El SessionEnd timeout es configurable via `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS`.

### 1.6 Hooks async

Un hook puede responder con `{"async": true, "asyncTimeout": 15000}` para ejecutarse en background:
- Se registra en `AsyncHookRegistry` (mapa global de pending hooks)
- El query loop principal hace polling con `checkForAsyncHookResponses()`
- Si `asyncRewake: true`: al terminar con exit code 2, despierta al modelo via notificacion

### 1.7 Respuesta de hooks (JSON)

Un hook puede controlar el flujo respondiendo JSON por stdout:

```json
{
  "continue": false,          // Detener ejecucion
  "stopReason": "...",        // Razon mostrada al usuario
  "decision": "approve|block", // Aprobar o bloquear
  "reason": "...",            // Explicacion
  "systemMessage": "...",     // Mensaje al usuario
  "suppressOutput": true,     // Ocultar stdout del transcript
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow|deny|ask",
    "updatedInput": {},        // Modificar input de herramienta
    "additionalContext": "..." // Contexto extra inyectado al modelo
  }
}
```

### 1.8 Seguridad y Trust

- **TODOS los hooks requieren workspace trust** — Si el usuario no ha aceptado el trust dialog, los hooks se saltan
- Defensa en profundidad: incluso si los hooks se capturan antes del trust dialog, no se ejecutan
- `shouldSkipHookDueToTrust()` verifica `checkHasTrustDialogAccepted()`
- En modo no interactivo (SDK), el trust es implicito
- `allowManagedHooksOnly` (policy setting) bloquea hooks de user/project/local

---

## 2. Sistema de Memory

### 2.1 Arquitectura general

Claude Code tiene **4 sistemas de memoria** distintos:

1. **Auto Memory (memdir)** — Memoria persistente basada en archivos `.md`
2. **Session Memory** — Notas automaticas de la sesion actual
3. **Agent Memory** — Memoria para subagentes
4. **CLAUDE.md / Rules** — Instrucciones del usuario (no "auto-managed")

### 2.2 Auto Memory (memdir) — el "MEMORY.md"

**Ubicacion:** `~/.claude/projects/<sanitized-git-root>/memory/`

**Resolucion de path (orden de prioridad):**
1. `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` (env var)
2. `autoMemoryDirectory` en settings.json (solo fuentes confiables: policy/local/user — NO project)
3. `<memoryBase>/projects/<sanitized-cwd>/memory/`

**Entrypoint:** `MEMORY.md` dentro del directorio de memoria.

**Limites:**
- `MEMORY.md` tiene un maximo de **200 lineas** (`MAX_ENTRYPOINT_LINES`)
- Maximo **25,000 bytes** (`MAX_ENTRYPOINT_BYTES`)
- Si excede, se trunca con warning
- Maximo **200 archivos** de memoria escaneados (`MAX_MEMORY_FILES`)
- Frontmatter leido hasta **30 lineas** por archivo

**Tipos de memoria (taxonomia cerrada de 4 tipos):**
| Tipo | Descripcion |
|------|-------------|
| `user` | Info sobre el usuario (rol, preferencias, conocimiento) |
| `feedback` | Correcciones y confirmaciones del usuario |
| `project` | Contexto de proyecto no derivable del codigo |
| `reference` | Punteros a sistemas externos |

**Formato de archivos de memoria:**
```markdown
---
name: {{nombre}}
description: {{descripcion de una linea}}
type: {{user, feedback, project, reference}}
---

{{contenido de la memoria}}
```

**Habilitacion/deshabilitacion:**
- `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` — desactiva
- `CLAUDE_CODE_SIMPLE` (--bare) — desactiva
- `settings.autoMemoryEnabled = false` — desactiva por proyecto
- Default: habilitado

### 2.3 Session Memory — notas automaticas de sesion

**Ubicacion:** `~/.claude/session-memory/<session-id>.md`

**Que es:** Un agente forkeado extrae automaticamente notas de la conversacion y las escribe en un archivo markdown estructurado. Funciona como un "resumen vivo" de la sesion.

**Template default (secciones):**
- Session Title
- Current State
- Task specification
- Files and Functions
- Workflow
- Errors & Corrections
- Codebase and System Documentation
- Learnings
- Key results
- Worklog

**Umbrales para extraccion automatica:**
| Parametro | Default | Descripcion |
|-----------|---------|-------------|
| `minimumMessageTokensToInit` | **10,000 tokens** | Tokens minimos antes de inicializar |
| `minimumTokensBetweenUpdate` | **5,000 tokens** | Crecimiento minimo entre actualizaciones |
| `toolCallsBetweenUpdates` | **3 tool calls** | Llamadas a herramientas entre actualizaciones |

**Logica de trigger** (`shouldExtractMemory`):
1. Primero debe alcanzar 10K tokens para inicializarse
2. Despues, extrae cuando:
   - Umbral de tokens (5K) Y umbral de tool calls (3) ambos se cumplen, O
   - Umbral de tokens se cumple Y el ultimo turno del asistente NO tiene tool calls (pausa natural)

**Limites de tamano:**
- Maximo **2,000 tokens** por seccion (`MAX_SECTION_LENGTH`)
- Maximo **12,000 tokens** total (`MAX_TOTAL_SESSION_MEMORY_TOKENS`)
- Si excede, el prompt de extraccion ordena condensar agresivamente

**Personalizacion:**
- Template custom: `~/.claude/session-memory/config/template.md`
- Prompt custom: `~/.claude/session-memory/config/prompt.md` (soporta `{{variables}}`)

**Feature gate:** Controlado por `tengu_session_memory` (GrowthBook). Solo funciona en main REPL thread, no en subagentes.

### 2.4 Recall inteligente (findRelevantMemories)

Al inicio de cada query, Claude Code puede buscar memorias relevantes:
1. `scanMemoryFiles()` lee el frontmatter de todos los `.md` (excepto MEMORY.md)
2. `selectRelevantMemories()` usa un side-query a Sonnet para seleccionar hasta **5 archivos** relevantes
3. Los archivos seleccionados se inyectan en el contexto

### 2.5 Extract Memories (modo autonomo)

Existe un modo `EXTRACT_MEMORIES` (feature flag `tengu_passport_quail`) donde un agente de fondo:
- Analiza la conversacion despues de cada turno
- Extrae memorias automaticamente sin intervencion del usuario
- Separado de Session Memory — es para memorias de largo plazo (auto memory / memdir)

### 2.6 Auto Dream

Adicionalmente, `executeAutoDream()` se ejecuta al final de cada turno (en `handleStopHooks`):
- Solo en main thread, no en subagentes
- No ejecuta en modo --bare
- Consolida memorias periodicamente

---

## 3. Hooks al inicio de sesion (SessionStart)

**Si, los hooks pueden ejecutar acciones automaticas al inicio de sesion.**

El evento `SessionStart` se dispara con estos sources:
- `startup` — Primera vez que se abre Claude Code
- `resume` — Al reanudar una sesion
- `clear` — Despues de limpiar conversacion
- `compact` — Despues de compactacion

**Capacidades especiales de SessionStart hooks:**
- Pueden devolver `additionalContext` que se inyecta como system-reminder
- Pueden devolver `initialUserMessage` que se usa como primer mensaje
- Pueden devolver `watchPaths` — rutas de archivos a vigilar para hooks `FileChanged`
- Pueden escribir a `CLAUDE_ENV_FILE` para setear variables de entorno para la sesion
- Soporte async completo — pueden ejecutarse en background

**Ejemplo en settings.json:**
```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "echo '{\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"additionalContext\":\"Contexto inyectado al inicio\"}}'",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

### Setup hooks

Existe un evento `Setup` separado (trigger: `init` | `maintenance`) que tambien ejecuta al inicio, despues de SessionStart.

---

## 4. Relacion Hooks-Permisos

### 4.1 PreToolUse hooks como gatekeepers

Los hooks `PreToolUse` pueden **controlar permisos directamente**:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",   // Aprobar sin preguntar
    "permissionDecisionReason": "Aprobado por hook"
  }
}
```

Opciones de `permissionDecision`:
- `"allow"` — Aprueba la accion sin preguntarle al usuario
- `"deny"` — Bloquea la accion
- `"ask"` — Fuerza preguntarle al usuario (incluso si ya estaba auto-aprobado)

### 4.2 PermissionRequest hooks

El evento `PermissionRequest` se dispara cuando el sistema necesita un permiso. Los hooks pueden responder con:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow",
      "updatedInput": {},          // Modificar el input
      "updatedPermissions": []     // Agregar reglas de permiso permanentes
    }
  }
}
```

O denegar:
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "deny",
      "message": "No permitido por politica",
      "interrupt": true    // Interrumpir flujo completamente
    }
  }
}
```

### 4.3 Cadena de evaluacion

Cuando una herramienta se ejecuta:
1. **Reglas de permisos** (rules-based) se evaluan primero
2. **PreToolUse hooks** se ejecutan
3. Si un hook dice `allow` o `deny`, eso sobreescribe la decision de reglas
4. Si un hook dice `ask`, fuerza la pregunta al usuario
5. `PermissionRequest` hooks dan la oportunidad final de decidir

### 4.4 updatedInput (modificacion de input)

Tanto `PreToolUse` como `PermissionRequest` hooks pueden **modificar el input** de una herramienta antes de que se ejecute. Esto permite:
- Sanitizar rutas de archivos
- Agregar flags a comandos
- Reescribir queries

---

## 5. Archivos clave extraidos

### Hooks
- `src/utils/hooks.ts` — Motor principal (4900+ lineas), toda la logica de ejecucion
- `src/types/hooks.ts` — Tipos TypeScript y esquemas Zod
- `src/schemas/hooks.ts` — Esquemas de configuracion (HookCommandSchema, HookMatcherSchema)
- `src/utils/hooks/hookEvents.ts` — Sistema de eventos para broadcasting
- `src/utils/hooks/AsyncHookRegistry.ts` — Registro de hooks async
- `src/utils/hooks/sessionHooks.ts` — Hooks de sesion (in-memory)
- `src/utils/hooks/hooksSettings.ts` — Lectura de configuracion desde settings
- `src/utils/hooks/postSamplingHooks.ts` — Hooks post-muestreo (internos)
- `src/utils/hooks/execPromptHook.ts` — Ejecucion de hooks tipo prompt
- `src/utils/hooks/execAgentHook.ts` — Ejecucion de hooks tipo agent
- `src/utils/hooks/execHttpHook.ts` — Ejecucion de hooks tipo http

### Memory
- `src/memdir/memdir.ts` — Prompt builder para auto memory (incluye MEMORY.md)
- `src/memdir/paths.ts` — Resolucion de paths de memoria
- `src/memdir/memoryTypes.ts` — Taxonomia de 4 tipos de memoria
- `src/memdir/memoryScan.ts` — Escaneo de archivos de memoria
- `src/memdir/findRelevantMemories.ts` — Recall inteligente con Sonnet
- `src/services/SessionMemory/sessionMemory.ts` — Session memory automatica
- `src/services/SessionMemory/sessionMemoryUtils.ts` — Utilidades y umbrales
- `src/services/SessionMemory/prompts.ts` — Template y prompts de extraccion
- `src/services/compact/sessionMemoryCompact.ts` — Compactacion con session memory
- `src/utils/memoryFileDetection.ts` — Deteccion de archivos de memoria
- `src/commands/memory/memory.tsx` — Comando /memory (UI)

---

## 6. Implicaciones para Proyecto SEAL

### Hooks como punto de integracion
- **SessionStart hooks** pueden ejecutar boot_context automaticamente via `command` hook
- Los hooks `PreToolUse` pueden inyectar contexto SEAL antes de cada accion
- `UserPromptSubmit` hooks pueden preprocesar mensajes del usuario
- `Stop` hooks pueden triggerear guardado de memoria post-turno

### Memory system insights
- El sistema de auto memory con frontmatter tipado (user/feedback/project/reference) es mucho mas sofisticado que un simple MEMORY.md
- La extraccion de session memory usa un agente forkeado — consume tokens API
- El recall inteligente usa Sonnet como selector — maximo 5 archivos por query
- `MEMORY.md` es solo un indice (200 lineas max), las memorias reales van en archivos separados con frontmatter

### Limitaciones relevantes
- Session memory solo funciona en main REPL thread (no en subagentes)
- Los hooks de sesion se pierden al cerrar — no son persistentes
- El timeout de SessionEnd es muy agresivo (1.5s default)
- `allowManagedHooksOnly` en policy puede bloquear hooks custom
