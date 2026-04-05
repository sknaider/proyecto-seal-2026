# Analisis de Agentes y Swarm en Claude Code v2.1.88

Analisis del source map `cli.js.map` de Claude Code v2.1.88.
96 archivos extraidos de `../src/` con "agent" o "swarm" en su path.

---

## 1. Arquitectura General de Sub-Agentes

Claude Code tiene **tres modos** para lanzar sub-agentes:

| Modo | Proceso | Aislamiento | Comunicacion |
|------|---------|-------------|--------------|
| **Subagent sincrono** | In-process (mismo Node.js) | Contexto aislado via `createSubagentContext()` | Retorno directo del resultado |
| **Subagent asincrono (background)** | In-process (mismo Node.js) | Contexto aislado, AbortController independiente | Notificacion cuando completa |
| **Teammate (swarm)** | Proceso separado (tmux/iTerm2) O in-process | Proceso/contexto completamente separado | File-based mailbox |

### Como lanza sub-agentes

**NO hay fork de proceso ni spawn de child process para subagentes normales.** Todo corre en el mismo proceso Node.js.

El flujo es:
1. `AgentTool.tsx` recibe la invocacion con `prompt`, `subagent_type`, etc.
2. Resuelve el `AgentDefinition` (built-in o custom)
3. Llama a `runAgent()` en `runAgent.ts` que:
   - Crea un contexto aislado via `createSubagentContext()` (archivo `forkedAgent.ts`)
   - Ejecuta `query()` (el loop principal de LLM) con ese contexto
   - Itera sobre mensajes del stream y los acumula
4. El resultado se retorna al agente padre como tool_result

Para agentes **asincronos** (background), el flujo es similar pero se registra como `LocalAgentTask` en AppState y se ejecuta en paralelo. El padre recibe una notificacion cuando completa.

### Fork Subagent (experimental)

Existe un modo "fork" (feature flag `FORK_SUBAGENT`) donde:
- Se omite `subagent_type` para heredar el contexto completo del padre
- El hijo recibe el system prompt identico del padre (byte-exact para cache hits)
- Se construyen mensajes fork con `buildForkedMessages()` que clona el mensaje assistant completo + placeholder tool_results + directiva unica por hijo
- Se bloquea el fork recursivo (un hijo no puede volver a forkear)
- Se inyecta un prompt rigido al hijo: "You are a forked worker process. Do NOT spawn sub-agents."

**Optimizacion clave:** Los forks comparten prompt cache porque el prefijo de la request API es byte-identico entre hijos. Solo difiere el bloque final de texto con la directiva.

---

## 2. Modelo de Aislamiento

### createSubagentContext() (forkedAgent.ts)

Funcion central que crea un `ToolUseContext` aislado para cualquier subagente:

**Estado clonado (aislado por defecto):**
- `readFileState` - cache de archivos leidos (clonado)
- `contentReplacementState` - estado de reemplazos (clonado)
- `nestedMemoryAttachmentTriggers` - Set nuevo vacio
- `toolDecisions` - undefined nuevo
- `abortController` - hijo del padre (abort del padre propaga, pero hijo puede abortar independientemente)

**Callbacks en no-op por defecto:**
- `setAppState` - no-op (el hijo no modifica estado del padre)
- `setInProgressToolUseIDs` - no-op
- `setResponseLength` - no-op
- Callbacks de UI (`addNotification`, `setToolJSX`, etc.) - undefined

**Excepcion critica:** `setAppStateForTasks` siempre apunta al store raiz. Esto permite que tareas async registren/maten bash tasks correctamente (evita zombies PID=1).

**Opt-in para compartir:**
- `shareSetAppState: true` - comparte mutacion de estado
- `shareAbortController: true` - comparte controller (para agentes interactivos)
- `shareSetResponseLength: true` - comparte metricas

### Permisos de herramientas

Los subagentes heredan un subconjunto de permisos:
- Cada `AgentDefinition` puede definir `permissionMode` (ej: `'bubble'` para subir prompts al padre)
- Agentes async tienen `shouldAvoidPermissionPrompts: true` (auto-deny)
- Se pueden pasar `allowedTools` que reemplazan las reglas de sesion del padre

---

## 3. Swarm Orchestration (Agent Teams)

### Habilitacion

Feature flag: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` + verificacion de plan de suscripcion via `isAgentSwarmsEnabled()`.

### Arquitectura Leader-Worker

- **Leader (team-lead):** El agente principal que el usuario ve. Coordina el equipo.
- **Workers (teammates):** Agentes que ejecutan tareas delegadas por el leader.
- **Comunicacion:** Via file-based mailbox (`~/.claude/teams/{teamName}/mailbox/`)

### Backends Disponibles

**Archivo clave:** `utils/swarm/backends/registry.ts`

Tres backends implementados:

#### a) TmuxBackend (`TmuxBackend.ts`)
- Usa tmux para gestion de paneles visuales
- **Dentro de tmux:** Split del window actual. Leader a la izquierda (30%), teammates a la derecha (70%)
- **Fuera de tmux:** Crea sesion `claude-swarm` con socket separado (`claude-swarm-{PID}`)
- Layout: `main-vertical` con leader, `tiled` sin leader
- Soporta hide/show de paneles (mover a sesion oculta `claude-hidden`)
- Lock de creacion de paneles para evitar race conditions en spawns paralelos

#### b) ITermBackend (`ITermBackend.ts`)
- Usa `it2` CLI de iTerm2 para split panes nativos
- Layout: Leader izquierda, teammates apilados verticalmente a la derecha
- NO soporta hide/show de paneles
- Recovery automatico: si un pane target esta muerto, prueba el siguiente
- Limitacion: cada llamada a `it2` es lenta (spawna proceso Python + API)
- Color y titulo son no-op por rendimiento

#### c) InProcessBackend (`InProcessBackend.ts`)
- Corre en el MISMO proceso Node.js con `AsyncLocalStorage` para aislamiento de contexto
- Comparte recursos (API client, MCP connections) con el leader
- Comunicacion via file-based mailbox (igual que pane-based)
- Terminacion via AbortController (no kill-pane)
- Siempre disponible (sin dependencias externas)

### Deteccion de Backend (Prioridad)

```
1. Dentro de tmux? -> TmuxBackend (nativo)
2. En iTerm2 con it2 CLI? -> ITermBackend (nativo)
3. En iTerm2 sin it2? -> TmuxBackend (fallback) + sugerir it2 setup
4. tmux disponible? -> TmuxBackend (sesion externa)
5. Nada disponible? -> InProcessBackend (fallback automatico)
```

Modo forzado via `--teammate-mode`:
- `'tmux'` - forzar tmux
- `'in-process'` - forzar in-process
- `'auto'` (default) - deteccion automatica

Sesiones no-interactivas (`-p` mode) siempre usan in-process.

### Spawn de Teammates

**Archivo clave:** `tools/shared/spawnMultiAgent.ts`

Para **pane-based** (tmux/iTerm2):
1. Crear pane en la vista swarm
2. Asignar color al borde/titulo
3. Construir comando CLI con flags heredados del padre:
   - `--dangerously-skip-permissions` (si aplica)
   - `--model` (si override)
   - `--settings` (si custom)
   - `--plugin-dir` (si plugins inline)
   - `--teammate-mode`
4. Enviar comando al pane via `send-keys` (tmux) o `session run` (iTerm2)
5. Variables de entorno propagadas: `CLAUDECODE=1`, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, provider vars, proxy vars, etc.

Para **in-process** (`spawnInProcess.ts`):
1. Crear `TeammateContext` via `createTeammateContext()`
2. Crear `AbortController` independiente (NO ligado al padre)
3. Registrar `InProcessTeammateTaskState` en AppState
4. Iniciar loop de agente via `startInProcessTeammate()` (fire-and-forget)
5. El runner (`inProcessRunner.ts`) envuelve `runAgent()` con `runWithTeammateContext()`

---

## 4. Comunicacion entre Agentes

### File-Based Mailbox

Todos los teammates (pane-based e in-process) usan mailbox basado en archivos:
- Path: `~/.claude/teams/{teamName}/mailbox/{agentName}/`
- Mensajes son JSON con campos: `text`, `from`, `color`, `timestamp`, `summary`
- `SendMessage` tool para enviar mensajes a teammates especificos o broadcast (`to: "*"`)

### Permission Sync (`permissionSync.ts`)

Cuando un worker necesita permisos:

**Flujo pane-based:**
1. Worker escribe permission_request en `~/.claude/teams/{teamName}/permissions/pending/`
2. Leader poll con `readPendingPermissions()`
3. Usuario aprueba/deniega en UI del leader
4. Leader escribe resolucion en `permissions/resolved/`
5. Worker poll la resolucion

**Flujo in-process:**
1. Worker usa `getLeaderToolUseConfirmQueue()` para acceder al dialogo ToolUseConfirm del leader
2. Se muestra en la UI del leader con badge de "worker"
3. Fallback a mailbox si el bridge no esta disponible

### Idle Notifications

Cuando un teammate termina su trabajo:
1. Hook `Stop` registrado en `teammateInit.ts`
2. Marca al teammate como inactivo en team file
3. Envia `idle_notification` al leader via mailbox

### Limitaciones de Comunicacion

- **No hay memoria compartida** entre agentes. Cada uno tiene su propio contexto LLM.
- **Subagentes normales** retornan un string de resultado truncado a 100K chars (`maxResultSizeChars`).
- **Fork children** no pueden leer el transcript del padre sin hacer Read explicito.
- **Teammates** solo ven mensajes enviados explicitamente via SendMessage/mailbox.
- **El resultado del subagente NO es visible al usuario** - el padre debe resumir manualmente.
- **Teammates no pueden spawn otros teammates** - el roster es plano.
- **In-process teammates no pueden lanzar agentes background** (ciclo de vida ligado al proceso).

---

## 5. Isolation: Worktree

**Archivo clave:** `utils/worktree.ts` (no extraido en el subset agent/swarm)

El parametro `isolation: "worktree"` en AgentTool crea un **git worktree temporal** para el agente:

### Flujo

1. `createAgentWorktree(slug)` crea worktree con `git worktree add`
2. Se genera un branch unico para el worktree
3. Symlinks de directorios pesados (ej: `node_modules`) desde el repo principal para evitar duplicacion en disco
4. El `cwd` del agente se override al path del worktree
5. Para **forks + worktree**, se inyecta `buildWorktreeNotice()` que dice:
   - "Estas en un worktree aislado en {worktreePath}"
   - "Paths del contexto heredado son del directorio padre, traduce al worktree"
   - "Re-lee archivos antes de editar"
   - "Tus cambios no afectan los archivos del padre"

### Cleanup

Al completar el agente:
1. `hasWorktreeChanges()` verifica si hay cambios (git diff vs HEAD original)
2. Si NO hay cambios: `removeAgentWorktree()` limpia worktree + branch
3. Si HAY cambios: worktree se conserva. Se reporta `worktreePath` y `worktreeBranch` al padre
4. Worktrees basados en hooks siempre se conservan

### Isolation: Remote (solo ant-internal)

Existe `isolation: "remote"` que lanza el agente en un entorno CCR (Claude Code Remote). Solo disponible para builds internos de Anthropic (`USER_TYPE === 'ant'`). Usa `teleportToRemote()` para crear sesion remota.

---

## 6. Agentes Built-In

| Agente | Proposito | Tools |
|--------|-----------|-------|
| `general-purpose` | Default cuando no se especifica tipo | Todos |
| `Explore` | Busqueda/lectura de codigo (read-only) | Read, Glob, Grep, Bash |
| `Plan` | Planificacion de tareas (read-only) | Read, Glob, Grep, Bash |
| `verification` | Verificacion de codigo (feature flag) | - |
| `claude-code-guide` | Guia de uso de Claude Code | - |
| `statusline-setup` | Configuracion de statusline | - |

Explore y Plan tienen optimizaciones:
- `omitClaudeMd: true` - no reciben CLAUDE.md del padre (ahorra ~5-15 Gtok/semana)
- No reciben `gitStatus` del padre (stale, ahorra ~1-3 Gtok/semana)
- Son `ONE_SHOT_BUILTIN_AGENT_TYPES` - skip del trailer agentId/SendMessage

### Custom Agents

Se pueden definir agentes personalizados via:
- Archivos `.md` en directorios de agentes (frontmatter YAML)
- Plugins (`loadPluginAgents.ts`)
- Cada agente define: `tools`, `disallowedTools`, `whenToUse`, `model`, `permissionMode`, `maxTurns`, `color`, `mcpServers`, `isolation`, `background`

---

## 7. Resumen de Archivos Clave

| Archivo | Responsabilidad |
|---------|----------------|
| `tools/AgentTool/AgentTool.tsx` | Tool principal. Schema, routing fork/normal/swarm, worktree setup |
| `tools/AgentTool/runAgent.ts` | Loop de ejecucion del agente (query loop + MCP + permisos) |
| `tools/AgentTool/forkSubagent.ts` | Fork experiment: mensajes, boilerplate, worktree notice |
| `tools/AgentTool/prompt.ts` | System prompt del AgentTool (cuando forkear, ejemplos) |
| `utils/forkedAgent.ts` | `createSubagentContext()`, `runForkedAgent()`, cache-safe params |
| `utils/swarm/backends/types.ts` | Interfaces: `PaneBackend`, `TeammateExecutor`, `TeammateSpawnConfig` |
| `utils/swarm/backends/registry.ts` | Deteccion y seleccion de backend |
| `utils/swarm/backends/TmuxBackend.ts` | Implementacion tmux completa |
| `utils/swarm/backends/ITermBackend.ts` | Implementacion iTerm2 via it2 CLI |
| `utils/swarm/backends/InProcessBackend.ts` | Backend in-process con AsyncLocalStorage |
| `utils/swarm/spawnInProcess.ts` | Spawn de teammate in-process |
| `utils/swarm/inProcessRunner.ts` | Runner del agente in-process (runAgent wrapper) |
| `utils/swarm/permissionSync.ts` | Sincronizacion de permisos leader-worker |
| `utils/swarm/teammateInit.ts` | Hooks de inicializacion de teammate (idle notification) |
| `utils/swarm/spawnUtils.ts` | CLI flags heredados, env vars para tmux spawn |
| `tools/shared/spawnMultiAgent.ts` | Logica compartida de spawn (pane + in-process) |

---

## 8. Datos Cuantitativos

- **34M+** spawns de Explore por semana (mencionado en comentarios de codigo)
- **100,000 chars** limite de resultado de subagente
- **200 turns** maximo para fork children
- **120 segundos** timeout para auto-background de agentes
- **30 segundos** timeout esperando MCP servers pendientes
- **200ms** delay para inicializacion de shell en paneles tmux
- **500ms** intervalo de polling para permisos
- **135 chars** ahorrados por skip de trailer en agentes one-shot

---

*Extraido del source map de Claude Code v2.1.88 el 2026-03-31*
*96 archivos fuente analizados desde `cli.js.map`*
