# IBM Bob Shell 2.0.2 — Motor del agente, ventana de contexto y compactación

Análisis por ingeniería inversa de código minificado, bundle legítimamente licenciado.
Método: grep dirigido sobre `literals.txt` (88.168 líneas, un literal/línea con offset de
byte `@N`) y sobre `chunks/bob_NNNN.js` (chunk = offset//116000 + 1). Toda cita es texto
literal extraído del bundle, con su ubicación.

---

## 1. Bucle del agente

**No es un grafo LangGraph ejecutado como tal.** El bucle real que hace correr los turnos
es una función imperativa hecha a mano, `mwo(t)` ("Agent loop starting" / "Agent loop
ended"), en `chunks/bob_0122.js` (offset ~14.13M, dentro del módulo que exporta
`HarnessSession`, `shouldCompact`, `compact`, `COMPACTION_PROMPT`).

Estructura del bucle (reconstruida de `mwo`):

```
d = 0  // contador de turno
while (task.shouldLoop() && !cancel.cancelled()) {
  d++
  R = await task.invoke(cancel)          // 1 turno = 1 llamada al modelo + tools
  if (compactó en el turno anterior && shouldCompact(...)) {
    // la compactación no bajó lo suficiente el contexto -> aborta la sesión
    push "Stopped: context limit reached..."; break
  }
  if (contextWindow && shouldCompact(contextTokens, contextWindow, threshold)) {
    if (autoCondense === false) {
      if (no hay onCompaction handler) -> loggea y sigue sin compactar
      else -> pide aprobación; si la rechazan, detiene la sesión
    }
    await compact(...)   // dispara la compactación (ver §2)
  }
}
```

`shouldLoop()` (en `chunks/bob_0050.js`, clase de tarea) es la condición de parada real:

```js
shouldLoop() {
  v = getLastMessage()
  return !( !v || v.stop || forceStopped
    || (maxTurns && turnCount >= maxTurns)      // -> onMaxTurnsReached, log "Reached maxTurns limit"
    || (maxCost && costs.cost >= maxCost) )      // -> onMaxCostReached, log "Reached maxCost limit"
}
```

- Avisos previos al límite (no CLI flags — son opciones del `Harness` que consume el
  bundle, `limitWarnings`): a **5 turnos** del `maxTurns` inyecta un mensaje de sistema
  "You are approaching the turn limit... Stop exploring and complete your work with what
  you have."; en costo, dispara al **máximo entre `(maxCost-1)` y `0.8·maxCost`** con
  "You are approaching the cost limit...".
- `maxTurns`/`maxCost` **no aparecen como flags `--max-turns`/`--max-cost` del CLI**
  (grep exhaustivo sobre `literals.txt` de esas cadenas: 0 resultados). Son parámetros
  del objeto de configuración del `Harness` (`HarnessSession`/`HarnessTaskManager`),
  es decir, opciones de la capa que embebe a Bob (IDE/extensión), no del `bob` binario
  interactivo.
- `loopExitReason` tiene 6 valores observados en el código: `"cancelled"`, `"stop"`,
  `"loop_condition"`, `"compaction_insufficient"`, `"compaction_rejected"`, y el
  default `"unknown"` si nada matchea.
- **Detección de bucle muerto ("doom loop")**, en la misma clase de tarea: guarda las
  últimas 5 llamadas a herramientas; si las **3 últimas** son idénticas
  (`isDoomLoop()`) o las **5 últimas** son idénticas (`isCriticalDoomLoop()`), lo marca.

**Sobre LangGraph (109 menciones confirmadas):** el bundle re-exporta el paquete
`langchain` v1 completo (`createAgent`, `StateGraph`, `contextEditingMiddleware`,
`summarizationMiddleware`, `toolCallLimitMiddleware`, etc. — bloque de export en
`chunks/bob_0122.js` offset ~12.98M). Pero el bucle principal de codificación **no lo
usa**: `mwo`/`shouldLoop`/`invoke` son código propio. El único sitio donde SÍ se
instancia `createAgent` de LangChain de verdad (`(0,XF.createAgent)({model,tools,
systemPrompt,middleware:[modelCallLimitMiddleware({runLimit:this.maxSteps})]})`,
`chunks/bob_0125.js`/vecinos, offset ~67.5–67.6k líneas de literals) es la capa cliente
MCP (paquete `mcp-use` embebido: "Created N LangChain items from client/connectors"),
para conectar Bob a servidores MCP externos — un subsistema aparte, no el loop
principal. Conclusión: **LangGraph está en el bundle como dependencia de una capa
MCP secundaria; el motor del agente de codificación es un `while` hecho a mano.**

---

## 2. Ventana de contexto y compactación

**Conteo de tokens:** no usa `tiktoken` propio para decidir compactar — las 5
apariciones de la cadena `tiktoken` en `literals.txt` (líneas 54969, 59740, 61048,
63176×2) son referencias de terceros dentro de paquetes vendorizados de conteo de
tokens (proveedor-agnóstico), no un contador central invocado por el loop. Lo que el
loop usa es **`contextTokens` reportado por el proveedor** (`getSpend().contextTokens`,
alimentado por el `usage`/`prompt_tokens` que devuelve cada respuesta del LLM), no una
estimación local. Hay además una función `countTokensApproximately` re-exportada de
`langchain` (visible en el bloque de exports de §1) pero no se ve invocada desde
`mwo`/`shouldCompact`.

**Umbral de disparo — hay tres números distintos, con roles distintos:**

| Umbral | Valor | Dónde vive | Rol |
|---|---|---|---|
| `getCompactionThreshold()` | **90%** (`compactionThresholdPercent ?? 90`, /100) | `HarnessSession` (chunks/bob_0122.js) | Default de librería si nadie configura nada |
| `shouldCompact(tokens, window, threshold=.8)` | **80%** | misma clase | Default interno de la función pura de comparación |
| `auto-condense-context-percent` | **70%** | `getFeatureFlagDefaults()` (chunks/bob_0125.js área, literals línea 35543) | **Default real de producto** — el feature flag que efectivamente se pasa como `compactionThresholdPercent` a la sesión |

Es decir: el código de librería trae sus propios defaults (90%/80%) por si se lo usa
suelto, pero **la app Bob shipea con el flag de producto en 70%** — con eso dispara la
compactación automática en el uso real.

`shouldCompact(tokens, window, pct)` es una función pura de una línea:
```js
function shouldCompact(t, e, n=.8) { return e<=0 ? !1 : t >= e*n }
```

**Qué se conserva / qué se descarta:** la compactación (`qvn`, la función `compact`
importada de `zvn.compact`) toma **todos los mensajes no marcados `_meta.compactedAt`**,
genera un resumen con el propio modelo de la tarea (no un modelo aparte — usa
`t.getProvider()`/`t.getModel()` de la sesión activa), y **reemplaza los mensajes con
ese resumen** (`task.replaceMessagesWithSummary(summary, {autoTriggered})`). El mensaje
de usuario más reciente no oculto se vuelve a inyectar tras el resumen para que la
conversación pueda continuar (`"No user message found to replay after compaction"` es
el error si no hay ninguno — literals línea 18611).

**Prompt de resumen — transcripción íntegra** (`COMPACTION_PROMPT`, `chunks/bob_0122.js`):

```
Provide a detailed summary for continuing this conversation.
Focus on information helpful for continuation: what was done, what is being worked on,
which files are involved, and what's next.

Use this template:
---
## Goal
[What goal(s) is the user trying to accomplish?]

## Instructions
[Important instructions the user gave that are still relevant]
[If there is a plan or spec, include info so next agent can continue using it]

## Discoveries
[Notable things learned during this conversation useful for continuing]

## Accomplished
[What work has been completed, what's in progress, what's left?]

## Relevant files / directories
[Structured list of relevant files read, edited, or created]
---
```

**Aviso al usuario:** sí. Al completar la compactación arma un mensaje de telemetría/UI
con formato exacto (`chunks/bob_0122.js`):
```
Compacted ${messagesBefore} messages (${contextTokens} / ${contextWindow} tokens, ${Math.round(threshold*100)}% threshold)
```
y dispara eventos de ciclo de vida (`onCompactionStart`, `onCompactionComplete`) que
la capa de tarea usa para poner el estado de la tarea en `"compacting"` en la UI/DB
(`store.updateTask(id,{status:"compacting"})`, ver §5, columna `status` de `tasks`).
También telemetriza el evento `CONTEXT_COMPACTION` (enum `TelemetryEventName`, literals
línea 3892/31185) con: `messagesBefore/After`, `summaryLength`, `contextTokens`,
`contextWindow`, `compactionThresholdPercent`, `compactionDurationMs`, `compactionCost`,
`model`.

**Si la compactación no alcanza:** el loop vuelve a chequear `shouldCompact` sobre el
resultado; si sigue por encima del umbral, **corta la sesión** con
`loopExitReason:"compaction_insufficient"` y el mensaje "Stopped: context limit reached
and compaction was unable to reduce the context enough to continue." — no reintenta
infinitamente.

**Clasificador previo (confirma la hipótesis del equipo):** hay un clasificador de
intención de tarea, `classifyIntent`, que llama **`openai/gpt-oss-20b`** con
`temperature:0, maxTokens:500` (`chunks/bob_0122.js`, función `OOu`), coincide con el
feature flag `"summary-model":"openai/gpt-oss-20b"` de `getFeatureFlagDefaults()`. Este
modelo NO resume la conversación (eso lo hace el modelo activo de la tarea, arriba) —
clasifica el mensaje del usuario en 9 categorías (A–I: Feature Development, Code
Explanation, Refactoring, Bug Detection, Test Generation, Documentation, DevOps/CI,
Specs & Planning, Other), con reglas de decisión ordenadas y ejemplos few-shot en el
prompt. Input truncado a **primeros 1000 + últimos 1000 caracteres** si el texto supera
2000 (función `Rwo`/`truncatePrompt`, constante `ibn=1e3`), con separador
`\n\n[... middle truncated ...]\n\n`. Mismo modelo (`gpt-oss-20b`) se reutiliza para
`"command-security-model"` (detección de comandos peligrosos antes de ejecutarlos).

---

## 3. Truncado de salidas de herramientas

Función genérica `truncateOutput` (`literals.txt` línea ~22436-22449, exportada junto a
`truncateAndSave`):

```js
function truncate(text, opts={}) {
  maxLines = opts.maxLines ?? 2000
  maxBytes = opts.maxBytes ?? 51200          // 50 KB
  // corta por lo que se cumpla primero (líneas o bytes), reporta cuál motivo aplicó
  // ("lines" | "bytes")
}
```

- **Default: 2.000 líneas O 51.200 bytes (50 KB)**, lo que se alcance primero.
- Mensaje de recorte por defecto: `"Refine your query to get more specific results."`
  (override visto en compactación de contexto: `"Output truncated."`).
- **`truncateAndSave`**: si el output se truncó, intenta `saveToolOutput(fullOutput)` —
  si el host lo soporta, **guarda la salida completa** en algún lado fuera del mensaje
  y adjunta `outputPath` como metadata del mensaje (`setMetadata({outputPath})`). Es
  decir: sí conserva el output íntegro, no lo pierde — lo saca del contexto del LLM pero
  lo deja recuperable.
- Truncado específico para salidas de `git` (usadas en menciones `@git-diff`,
  `@git-status`, etc., `chunks/bob_0125.js`): límite de **10.000 caracteres**
  (`C5u=1e4`), corta y agrega `\n[truncated]`. `git status --short` tiene además su
  propio `maxBuffer` de **30 KB** (30*1024) al nivel del `execFile`; el resto de
  comandos git usan `timeout:10000ms, maxBuffer:10485760` (10 MB) como default del
  wrapper `execFile`.

---

## 4. `environment_details`

Contradice parcialmente la hipótesis de partida: **no encontré** un bloque que arme
listado de directorio + hora + SO como parte de `environment_details` — puede estar en
una zona no cubierta por el grep dirigido, o vivir en la extensión IDE (VS Code) en vez
del bundle de `bobshell` (el "shell" es CLI; el enriquecimiento de contexto de IDE
podría ser un binario/paquete distinto no incluido en este dump). Lo que sí está
confirmado, texto literal del propio prompt de sistema (línea 18499 de `literals.txt`):

> "Each user message may contain an auto-appended `<environment_details>` section that
> includes potentially relevant information such as **git status, active file, and
> relevant files modified externally since your last interaction**. This section is
> automatically appended by the system... and is not part of the user's query."

Es decir: **es por mensaje de usuario, no por temporizador** — se re-adjunta en cada
turno de usuario, no en un ciclo aparte. Hay una función separada (`TTo`, línea 75155)
que **quita** el bloque `<environment_details>...</environment_details>` al parsear
salidas (p. ej. para mostrarlas limpias en UI o antes de mandarlas a otro consumidor).
La convención `<environment_details>` + `<attempt_completion><result>` (línea 18499 y
83675 de literals) es el mismo patrón de tags que usan Cline/Roo-Code — fuerte indicio
de arquitectura de prompting heredada/emparentada con esa familia de agentes, aunque
esto es **inferencia, no evidencia directa de origen**.

No pude confirmar en el material dado el tamaño exacto de 38-41 KB por turno ni el
listado completo de qué mete el bloque (dato del equipo, no verificado por mí en este
pase) — lo dejo marcado como pendiente, no lo afirmo.

---

## 5. Persistencia (SQLite)

Motor: **`node:sqlite`** nativo de Node (`DatabaseSync`, no `better-sqlite3` ni
`sql.js`), con `enableForeignKeyConstraints:true`. Esquema completo (`literals.txt`
líneas 74381-74527, migraciones subsiguientes incluidas):

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_id TEXT REFERENCES tasks(id),
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    first_message TEXT,
    directory TEXT NOT NULL,
    version TEXT,
    git_sha TEXT,
    git_branch TEXT,
    env TEXT,
    costs TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    time_archived INTEGER,
    locked_by TEXT,
    lock_lease_until INTEGER
    -- + migraciones: approval_config, message_queue, task_type ('normal'|'subtask'|'subagent'),
    --   last_error, is_pinned
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    data TEXT NOT NULL,          -- mensaje completo serializado (JSON), 1 fila por mensaje
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS attribution_logs (
    id TEXT PRIMARY KEY,
    file_uri TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    branch_name TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    contribution_text TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    task_id TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS task_pending_approvals (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (task_id, request_id)
);
CREATE TABLE IF NOT EXISTS key_value_store (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY, applied_at INTEGER NOT NULL);
```

**`attribution_logs` — hipótesis CONFIRMADA.** Es exactamente atribución de código
generado por IA: por cada contribución registra archivo (`file_uri`), repo, branch,
**qué herramienta la generó** (`tool_name`), el texto de la contribución, y el rango de
líneas exacto (`start_line`/`end_line`) — encaja con los feature flags
`"attribution-enabled":true` y `"llm-attribution-enabled":true` vistos en
`getFeatureFlagDefaults()`. Sirve para trazabilidad/compliance de código generado por
IA (auditoría, no telemetría de producto).

**`--resume`:** flag real de CLI, `-r, --resume [task-id]` — "Open the resume picker, or
resume a specific task id" (`literals.txt` línea 87705, definición Commander.js).
También hay `--list-tasks [limit]` (default 20). El mecanismo, a nivel de datos, es
directo: `messages` está indexado por `(task_id, created_at)` y cada fila de `tasks`
guarda `locked_by`/`lock_lease_until` (lock optimista para no correr dos sesiones sobre
la misma tarea) y `directory`/`git_sha`/`git_branch`/`env` para reconstruir el
workspace. `--resume` sin argumento imprime el picker; con `task-id` carga esa fila +
sus mensajes y retoma el loop de §1 desde ahí.

**Nota de migraciones:** hay pasos correctivos de normalización de rutas legadas
(`legacy-bob-code-%`) que reemplazan separadores `/` por `\` en `project_id` y en
`env.workspace` bajo condición `GLOB 'file:[A-Za-z]:/*'` — indicio de una migración
Windows-path que se hizo post-hoc sobre datos de una versión anterior del producto.

---

## 6. Prompt caching (`cache_control`)

Hay **`anthropicPromptCachingMiddleware`** (parte del paquete `langchain` re-exportado,
`chunks` en la zona 65419-65473 de literals), con esta lógica:

```js
if (modelo no es Anthropic) {
  console.warn("PromptCachingMiddleware: Skipping caching for ${model}. Consider
                switching to an Anthropic model for caching benefits.")
  return next(state)   // sin cache_control
}
return (state.messages.length + (systemPrompt?1:0)) < s
  ? next(state)                                          // aún no marca caché
  : next({...state, modelSettings:{...modelSettings, cache_control:{type:"...", ttl:o}}})
```

- **Solo aplica en modelos Anthropic** — para el resto, degrada con warning explícito.
- Marca `cache_control` **una vez que la cuenta de mensajes (+ system prompt) supera un
  umbral `s`** (no pude aislar el valor numérico exacto de `s` en el material
  disponible — queda **sin verificar**, es una variable capturada en closure fuera de
  la ventana del literal). El middleware fija tipo y `ttl` configurables (`o`).
- Además, a nivel de **capa OpenRouter** (`chunks` ~69955-69982 de literals, provider
  `Wbo`), `cache_control` se propaga por bloque de contenido (`image_url`, `video_url`,
  `input_audio`, `file`, mensajes de texto) cuando el proveedor lo soporta — es decir,
  el bundle vehiculiza `cache_control` tanto para Anthropic directo como cuando el
  tráfico pasa por OpenRouter hacia un modelo que lo soporte.
- Confirma explícitamente: **prompt caching es Anthropic-first**; con otros proveedores
  el diseño asume — y avisa — que no hay ahorro de caché.

---

## Tabla de números duros

| Número | Qué controla | Fuente |
|---|---|---|
| 2.000 líneas / 51.200 bytes (50 KB) | Truncado genérico de salida de herramienta (lo primero que se cumpla) | literals.txt ~22436 |
| 10.000 caracteres | Truncado de salidas de `git` en menciones `@git-*` | chunks/bob_0125.js (`C5u=1e4`) |
| 30 KB (30×1024) | `maxBuffer` de `git status --short` | chunks/bob_0125.js |
| 10 MB (10.485.760) | `maxBuffer` default del wrapper `execFile` para git | chunks/bob_0125.js |
| 10.000 ms | `timeout` default del wrapper `execFile` para git | chunks/bob_0125.js |
| 70% | `auto-condense-context-percent` — umbral real de producto para autocompactación | literals.txt línea 35543 |
| 90% | Default de librería de `getCompactionThreshold()` si no se configura nada | chunks/bob_0122.js |
| 80% (0.8) | Default interno de la función pura `shouldCompact(...)` | chunks/bob_0122.js |
| 5 turnos restantes | Umbral de aviso de `maxTurns` ("approaching the turn limit") | chunks/bob_0050.js |
| max(maxCost−1, 0.8·maxCost) | Umbral de aviso de `maxCost` ("approaching the cost limit") | chunks/bob_0050.js |
| 3 llamadas idénticas | `isDoomLoop()` | chunks/bob_0050.js |
| 5 llamadas idénticas | `isCriticalDoomLoop()` | chunks/bob_0050.js |
| 1.000 + 1.000 caracteres | Truncado (cabeza+cola) del input al clasificador de intención | chunks/bob_0122.js (`ibn=1e3`) |
| 500 tokens, temperature 0 | Parámetros de la llamada al clasificador `gpt-oss-20b` | chunks/bob_0122.js |
| 2.000 caracteres | Umbral bajo el cual el clasificador ve el texto completo (sin truncar) | chunks/bob_0122.js |
| $2.000/mes | `max-monthly-budget-allowance` (feature flag) | literals.txt línea 35543 |
| 250 / 400 / 400 ms | `completion-debounce-delay` / `next-edit-debounce-delay` / `completion-stream-cutoff-ms` | literals.txt línea 35543 |
| 20 | Default de `--list-tasks` sin argumento | literals.txt línea 87705 |

---

## Confianza / huecos declarados

- **Alta confianza (código citado literal, con ubicación):** §1 (loop, shouldLoop,
  doom-loop), §2 (umbrales de compactación, prompt de resumen íntegro, clasificador),
  §3 (truncado genérico y de git), §5 (esquema SQLite completo, `attribution_logs`
  confirmado).
- **Confianza media:** relación exacta entre el `auto-condense-context-percent` (70%)
  del feature-flags y el `compactionThresholdPercent` que efectivamente recibe
  `HarnessSession` en runtime — vi el flag y vi el parámetro consumido, pero no el cable
  que los une en una sola función; es la lectura más razonable, no un `grep` que los
  muestre en la misma línea.
- **Sin verificar / hueco declarado:** (a) tamaño de 38-41 KB de `environment_details`
  por turno y su listado exacto de contenido (SO, hora, directorio) — no lo encontré en
  este material, puede vivir en la extensión IDE no incluida en el dump; (b) valor
  numérico exacto del umbral `s` de `anthropicPromptCachingMiddleware`; (c) si
  `environment_details` se refresca en algún ciclo aparte del "por mensaje de usuario"
  que dice el propio system prompt.
