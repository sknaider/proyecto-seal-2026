# SPEC_07_MCP_PLUGINS_QUERY — Extracted Analysis

I now have a comprehensive understanding of all three subsystems. Here is the technical analysis.

---

# Analisis Tecnico: MCP Client, Plugins y Query Engine de Claude Code

## 1. MCP CLIENT

### 1.1 Arquitectura General

El sistema MCP es un cliente completo del protocolo Model Context Protocol que conecta a multiples servidores simultaneamente. La implementacion central reside en `services/mcp/client.ts` (~3,348 lineas) con soporte de tipos en `types.ts`, configuracion en `config.ts`, autenticacion en `auth.ts`, y utilidades en `utils.ts`.

### 1.2 Tipos de Transporte

El cliente soporta 8 tipos de transporte, definidos como un union discriminado en `types.ts`:

- **stdio** (default): Lanza un subprocess local via `StdioClientTransport`. Soporta `CLAUDE_CODE_SHELL_PREFIX` para wrapper shells. Captura stderr con cap de 64MB para diagnostico.
- **sse**: Server-Sent Events via `SSEClientTransport`. Usa `ClaudeAuthProvider` para OAuth. El stream SSE (GET) NO tiene timeout (long-lived); solo los POST tienen timeout de 60s.
- **http**: Streamable HTTP via `StreamableHTTPClientTransport`. Mismo modelo de auth que SSE. Agrega header `Accept: application/json, text/event-stream` obligatorio por spec MCP.
- **ws**: WebSocket nativo. Soporta Bun y Node.js (`ws` package). Headers custom, proxy, y mTLS.
- **sse-ide / ws-ide**: Transportes internos para extensiones IDE (VSCode). Solo exponen un subset de tools (`executeCode`, `getDiagnostics`).
- **sdk**: In-process via `SdkControlClientTransport`. Para servers embebidos.
- **claudeai-proxy**: Proxy transparente a traves de claude.ai para connectors web. Usa OAuth bearer tokens de claude.ai con retry automatico en 401.

### 1.3 Gestion de Conexiones

**Memoizacion**: `connectToServer` esta memoizado por `getServerCacheKey(name, config)`. El cache se invalida automaticamente via `client.onclose` handler, que tambien limpia caches de `fetchToolsForClient`, `fetchResourcesForClient`, `fetchCommandsForClient`.

**Batching de conexiones**: Los servers se particionan en locales (stdio/sdk, concurrencia 3 por default) y remotos (SSE/HTTP/WS, concurrencia 20). Se usa `pMap` en lugar de batches secuenciales para evitar que un server lento bloquee un batch entero.

**Timeout de conexion**: 30s por default, configurable via `MCP_TIMEOUT` env var.

**Deteccion de desconexion y reconexion**: El cliente intercepta `onerror` y `onclose` del SDK. Para errores terminales (ECONNRESET, ETIMEDOUT, EPIPE, EHOSTUNREACH, ECONNREFUSED), cuenta errores consecutivos y dispara `client.close()` tras 3 fallos. Para HTTP, detecta session-expired (404 + JSON-RPC -32001) y reconecta. Cuando el SDK agota SSE reconnect attempts ("Maximum reconnection attempts"), cierra el transport.

**Cleanup de procesos stdio**: Escalation gradual: SIGINT (100ms) -> SIGTERM (400ms) -> SIGKILL. Failsafe total de 600ms.

### 1.4 Resolucion de Tools

Cada tool MCP se registra con nombre `mcp__<serverNormalizado>__<toolName>`. La normalizacion reemplaza caracteres no-alfanumericos con underscore. Para servers claude.ai, colapsa underscores consecutivos.

Los tools se obtienen via `tools/list` JSON-RPC, se sanitizan (unicode), y se envuelven en un objeto `Tool` que:
- Aplica `maxResultSizeChars: 100_000` por default
- Trunca descripciones a 2,048 chars
- Extrae annotations MCP (`readOnlyHint`, `destructiveHint`, `openWorldHint`, `title`)
- Soporta `_meta.anthropic/searchHint` para deferred tool search
- Soporta `_meta.anthropic/alwaysLoad` para forzar carga

**Invocacion**: Cada `tool.call()` hace `ensureConnectedClient` (reconecta si cache vacio), llama `callMCPToolWithUrlElicitationRetry`, con retry de session-expired (1 intento). Reporta progreso via `onProgress` callback.

### 1.5 Resources y Prompts

- **Resources**: Se obtienen via `resources/list`. Se agregan tools `ListMcpResourcesTool` y `ReadMcpResourceTool` si algun server declara capability de resources.
- **Prompts**: Se obtienen via `prompts/list`, se convierten a `Command` con nombre `mcp__<server>__<prompt>`. La ejecucion usa `getPrompt()` con argumentos posicionales.
- **Skills**: Feature-gated (`MCP_SKILLS`), se obtienen de resources especiales.

### 1.6 Auth OAuth

`ClaudeAuthProvider` implementa `OAuthClientProvider` del SDK MCP con:

- Storage seguro via keychain (macOS) o filesystem encriptado
- PKCE flow completo con server HTTP local temporal para callback
- Soporte para refresh token con retry (3 intentos para errores transientes)
- Normalizacion de errores no-estandar de Slack (`invalid_refresh_token` -> `invalid_grant`)
- Step-up detection (403 durante tool calls -> re-auth transparente)
- XAA (Cross-App Access / SEP-990): Federation via IdP externo con id_token exchange
- Cache de auth de 15 minutos para evitar probes redundantes a servers que requieren auth
- Lockfile para serializar escrituras concurrentes de tokens

### 1.7 Configuracion Multi-Scope

La configuracion MCP se resuelve desde 7 scopes con prioridad:
1. `enterprise` (managed-mcp.json)
2. `user` (global config)
3. `project` (.mcp.json en raiz del proyecto)
4. `local` (config privada por proyecto)
5. `dynamic` (CLI flags, runtime)
6. `claudeai` (connectors de claude.ai)
7. `managed` (enterprise managed)

Cada server lleva su `scope` como metadata. Los servers de `.mcp.json` (project scope) requieren aprobacion del usuario excepto en non-interactive mode con projectSettings habilitado.

**Enterprise policies**: Soporta allowlist/denylist por nombre, comando, o URL pattern con wildcards.

**Deduplicacion**: Servers de plugins se deduplicn contra servers manuales por signature (`stdio:command` o `url:baseUrl`). Connectors de claude.ai se deduplicn contra servers manuales habilitados.

---

## 2. SISTEMA DE PLUGINS

### 2.1 Arquitectura

Los plugins son paquetes que extienden Claude Code con skills (slash commands), hooks (lifecycle), MCP servers, y agents. El sistema tiene tres capas:

1. **Built-in plugins** (`plugins/builtinPlugins.ts`): Registrados en memoria al startup. ID format: `name@builtin`. Toggleables por usuario.
2. **Marketplace plugins**: Repositorios git clonados a `~/.claude/plugins/cache/{marketplace}/{plugin}/{version}/`. Format: `name@marketplace`.
3. **Session plugins**: Directorios locales via `--plugin-dir`. Format: `name@inline`.

### 2.2 Estructura de un Plugin

```
plugin/
  plugin.json          # Manifest (nombre, version, author, dependencies, etc.)
  commands/            # Slash commands (.md files)
  agents/              # Agent definitions (.md files)
  hooks/
    hooks.json         # Hook configurations
  mcp-servers.json     # MCP server configs (o campo mcpServers en plugin.json)
  *.mcpb / *.dxt       # MCP bundles (Desktop Extension format)
```

### 2.3 Manifest Schema

Validado con Zod v4 (`schemas.ts`). Campos clave:
- `name`: kebab-case, sin espacios
- `version`: semver opcional
- `description`, `author`, `homepage`, `repository`, `license`, `keywords`
- `dependencies`: Referencias a otros plugins (`pluginName` o `pluginName@marketplace`)
- `commands`: Array de paths relativos a .md o directorios con SKILL.md, o map de nombre->metadata con `source`/`content`/`description`
- `hooks`: Inline o referencia a JSON file, o array mixto
- `mcpServers`: Inline configs o referencia a MCPB files

### 2.4 Marketplace System

Marketplaces son repositorios git que contienen un `marketplace.json` con entries de plugins. Nombres oficiales reservados para Anthropic (`claude-code-marketplace`, `anthropic-marketplace`, etc.) con validacion de source GitHub org (`anthropics`). Proteccion contra homograph attacks (no-ASCII bloqueado).

**Auto-update** (`pluginAutoupdate.ts`):
- Al startup, en background (non-blocking)
- Solo para marketplaces con `autoUpdate: true` (default para oficiales, excepto `knowledge-work-plugins`)
- Secuencia: refresh marketplace (git pull) -> update installed plugins -> notify callback
- Actualizaciones son non-inplace (disk-only), requieren restart/reload

### 2.5 Installation y Cache

**Cache versionado**: `~/.claude/plugins/cache/{marketplace}/{plugin}/{version}/`. Soporta ZIP compression y seed directories (pre-populated caches para CCR).

**Reconciliacion**: Al startup, compara marketplaces declarados en settings vs materializados en disco. Instala faltantes, actualiza cambiados. Post-install, auto-refresh de plugins activos.

**Dependency resolution**: `dependencyResolver.ts` implementa `verifyAndDemote` para validar dependencias entre plugins.

### 2.6 Plugin Lifecycle

1. **Discovery**: `pluginLoader.ts` escanea settings para plugin IDs, resuelve cada uno contra marketplace cache
2. **Validation**: Manifest parseado con Zod, paths relativos validados contra base (path traversal prevention)
3. **Loading**: Commands cargados de `commands/`, hooks de `hooks/hooks.json` + manifest, MCP servers de manifest + `mcpPluginIntegration.ts`
4. **Enable/Disable**: Persistido en user settings (`enabledPlugins` map). Built-in plugins usan `defaultEnabled`
5. **MCPB handling**: Archivos `.mcpb`/`.dxt` se descargan, extraen, y convierten a MCP config. Soportan user config interactivo via `/plugin` menu

### 2.7 Integracion MCP-Plugin

`mcpPluginIntegration.ts` carga MCP servers de plugins habilitados, expande variables de entorno (`CLAUDE_PLUGIN_ROOT`, opciones de usuario), y los inyecta al config MCP con scope `dynamic` y `pluginSource` tag. Los servers de plugin se deduplicn contra servers manuales por signature.

### 2.8 Seguridad

- Blocklist de plugins (`pluginBlocklist.ts`)
- Policy enforcement (`pluginPolicy.ts`): `isRestrictedToPluginOnly`, `isSourceAllowedByPolicy`, `isSourceInBlocklist`
- Marketplace name impersonation protection (regex + org verification)
- Path traversal validation en todas las rutas relativas
- Plugin flagging system (`pluginFlagging.ts`)

---

## 3. QUERY ENGINE

### 3.1 Arquitectura de Dos Capas

El query engine tiene dos capas:

- **QueryEngine** (`QueryEngine.ts`): Clase que posee el estado de sesion (messages, file cache, usage, permissions). Metodo principal: `submitMessage()`. Usado por el SDK/headless path.
- **query()** / **queryLoop()** (`query.ts`): Generator asincrono que implementa el loop interno de conversacion. Cada llamada a `submitMessage()` eventualmente invoca `query()`.

### 3.2 Flujo de submitMessage (QueryEngine)

1. **processUserInput**: Parsea input del usuario, detecta slash commands, genera messages
2. **System prompt assembly**: `fetchSystemPromptParts()` recopila prompt default + custom + append. Se inyecta memory mechanics prompt si hay override de memoria
3. **User context**: Base context + coordinator context (si coordinator mode activo)
4. **Plugin/Skill loading**: Cache-only load de plugins + slash command skills
5. **System init message**: Yield metadata (tools, model, permissions, skills, plugins)
6. **Transcript persistence**: Record messages antes de query para resumability
7. **query() invocation**: Delega al generator interno

### 3.3 Flujo del Query Loop (query.ts)

El loop principal es un `while(true)` con state machine explicita:

```
State = {
  messages, toolUseContext, autoCompactTracking,
  maxOutputTokensRecoveryCount, hasAttemptedReactiveCompact,
  maxOutputTokensOverride, pendingToolUseSummary,
  stopHookActive, turnCount, transition
}
```

**Cada iteracion:**

1. **Prefetch**: Skill discovery prefetch (background), memory relevance prefetch (background, `using` para auto-dispose)
2. **Query tracking**: Chain ID + depth para analytics y correlacion
3. **Messages post-compact boundary**: Filtra solo mensajes despues de la ultima compactacion
4. **Tool result budget**: `applyToolResultBudget()` limita tamano agregado de tool results por mensaje
5. **Snip compaction** (feature-gated `HISTORY_SNIP`): Recorte rapido de historial antes de microcompact
6. **Microcompact**: Compresion incremental de tool results antiguos. Soporta cache editing (feature `CACHED_MICROCOMPACT`)
7. **Context collapse** (feature-gated): Proyeccion colapsada del contexto, comprometiendo collapses staged
8. **Auto-compact**: Si el contexto excede threshold, compacta via LLM (genera summary). Tracking de failures para circuit breaker
9. **Blocking limit check**: Si auto-compact esta OFF y tokens exceden limite, bloquea con error
10. **Model call**: `deps.callModel()` (streaming). Soporta model fallback, fast mode, task budget, effort value, advisor model
11. **Stream processing**: Itera eventos del stream. Streaming tool executor procesa tool_use blocks en paralelo mientras el modelo sigue generando. Withholding de errores recuperables (prompt-too-long, max-output-tokens, media-size)
12. **Post-sampling hooks**: Fire-and-forget tras respuesta
13. **Abort handling**: Si abortado, genera synthetic tool_results para tools pendientes
14. **Recovery loops**:
    - **Prompt-too-long**: Context collapse drain -> reactive compact -> surface error
    - **Max output tokens**: Escalate 8k -> 64k, luego meta-message recovery (hasta 3 intentos)
    - **Media size**: Reactive compact strip-retry
    - **Model fallback**: Si `FallbackTriggeredError`, switch model y retry

15. **Tool execution**: Si hay tool_use blocks, ejecuta tools (streaming o batch via `runTools`)
16. **Stop hooks**: `handleStopHooks()` ejecuta Stop hooks, TeammateIdle hooks, TaskCompleted hooks. Pueden producir blocking errors que fuerzan continuacion
17. **Token budget check** (feature `TOKEN_BUDGET`): Si budget restante > 10%, auto-continue con nudge message
18. **Continue decision**: Actualiza state y vuelve al inicio del loop

### 3.4 Dependencias Inyectables

`QueryDeps` (en `deps.ts`) abstrae 4 dependencias para testability:
- `callModel`: `queryModelWithStreaming`
- `microcompact`: `microcompactMessages`
- `autocompact`: `autoCompactIfNeeded`
- `uuid`: `randomUUID`

### 3.5 QueryConfig

Snapshot inmutable al inicio del query (en `config.ts`):
- `sessionId`
- Gates runtime: `streamingToolExecution`, `emitToolUseSummaries`, `isAnt`, `fastModeEnabled`
- Explicitamente excluye `feature()` gates (necesitan estar inline para tree-shaking de Bun)

### 3.6 Stop Hooks

`stopHooks.ts` implementa el sistema de hooks post-turno:

- **Stop hooks**: Ejecutados al final de cada turno. Pueden producir blocking errors (fuerzan retry) o preventContinuation (detiene el loop)
- **TeammateIdle hooks**: Para teammates (coordinator mode), ejecutados tras Stop hooks
- **TaskCompleted hooks**: Para tareas in-progress del teammate actual
- **Side effects**: Prompt suggestion (fire-and-forget), memory extraction (fire-and-forget), auto-dream (fire-and-forget), job classification (awaited), computer use cleanup

### 3.7 Token Budget

`tokenBudget.ts` implementa auto-continuation basada en presupuesto:
- **Threshold**: Continua si < 90% del budget consumido
- **Diminishing returns**: Para si < 500 tokens de progreso en 2 checks consecutivos, tras 3+ continuaciones
- **Nudge message**: Inyecta mensaje de continuacion con porcentaje y tokens usados

### 3.8 Compaction Pipeline

El pipeline de compactacion tiene 4 etapas independientes que pueden coexistir:

1. **Snip** (HISTORY_SNIP): Recorte rapido, sin LLM
2. **Microcompact**: Reemplaza tool results antiguos con summaries cacheados
3. **Context collapse**: Proyeccion colapsada, collapses staged que se drenan on-demand
4. **Auto-compact**: Full LLM summarization. Reactive variant dispara post-413

---

## Archivos Clave Analizados

- `/home/dadito/IA/proyecto-seal/claude-code-analysis/mcp/services/mcp/client.ts` - MCP client core (3,348 lineas)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/mcp/services/mcp/types.ts` - Type definitions (258 lineas)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/mcp/services/mcp/config.ts` - Config resolution (1,578 lineas)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/mcp/services/mcp/auth.ts` - OAuth provider (2,465 lineas)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/mcp/services/mcp/utils.ts` - Utilities (575 lineas)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/mcp/services/mcp/normalization.ts` - Name normalization
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/mcp/tools/MCPTool/MCPTool.ts` - Tool base definition
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/plugins/builtinPlugins.ts` - Built-in plugin registry
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/utils/plugins/schemas.ts` - Plugin/marketplace schemas
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/utils/plugins/pluginLoader.ts` - Plugin discovery/loading
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/utils/plugins/pluginAutoupdate.ts` - Auto-update system
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/utils/plugins/mcpPluginIntegration.ts` - Plugin-MCP bridge
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/services/plugins/PluginInstallationManager.ts` - Background installation
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/query.ts` - Query loop (~1,250+ lineas)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/QueryEngine.ts` - Session engine
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/query/config.ts` - Query config snapshot
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/query/deps.ts` - Injectable dependencies
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/query/stopHooks.ts` - Post-turn hook system
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/query/tokenBudget.ts` - Budget auto-continuation

---

