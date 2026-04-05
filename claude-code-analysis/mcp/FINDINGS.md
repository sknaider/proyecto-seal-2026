# Hallazgos: MCP Client en Claude Code v2.1.88

> Analisis del source map extraido de `cli.js.map` — 71 archivos MCP.
> Fecha: 2026-03-31

---

## Estructura de Archivos

### Core del MCP Client (`services/mcp/`)
| Archivo | Tamaño | Funcion |
|---------|--------|---------|
| `client.ts` | 119KB | **Archivo principal.** Conexion, tool calls, timeout, retry, batching, procesamiento de resultados |
| `types.ts` | 7KB | Schemas Zod para config (stdio, sse, http, ws, sdk, claudeai-proxy) y tipos de conexion |
| `config.ts` | 51KB | Lectura/merge de configuraciones MCP desde multiples scopes |
| `auth.ts` | 89KB | OAuth, ClaudeAuthProvider, step-up detection, token refresh |
| `useManageMCPConnections.ts` | 45KB | Hook React que maneja lifecycle completo: init, reconnect, state sync |
| `MCPConnectionManager.tsx` | 8KB | React Context provider para reconnect/toggle |
| `utils.ts` | 18KB | Filtrado de tools/commands/resources por servidor, hashing de configs |
| `normalization.ts` | 1KB | Normalizacion de nombres para prefijos MCP |
| `mcpStringUtils.ts` | 4KB | Parsing de nombres `mcp__server__tool` |
| `elicitationHandler.ts` | 10KB | Manejo de elicitation requests (formularios del servidor al usuario) |

### Tools
| Archivo | Funcion |
|---------|---------|
| `tools/MCPTool/MCPTool.ts` | Definicion base del tool MCP (buildTool). Schema passthrough, overrides en client.ts |
| `tools/MCPTool/UI.tsx` | Renderizado de tool use/result en la UI |
| `tools/MCPTool/classifyForCollapse.ts` | Clasificacion de tools para colapsar en la UI |
| `tools/ListMcpResourcesTool/` | Tool para listar recursos MCP |
| `tools/ReadMcpResourceTool/` | Tool para leer recursos MCP |
| `tools/McpAuthTool/` | Tool especial que aparece cuando un servidor necesita autenticacion |

---

## 1. Serializacion y Despacho de Tool Calls (JSON-RPC)

### Protocolo
Claude Code usa el **SDK oficial** `@modelcontextprotocol/sdk` como cliente MCP. No implementa JSON-RPC directamente — delega al SDK.

### Flujo de una tool call:

1. El modelo invoca un tool con nombre `mcp__<server>__<tool>` y argumentos JSON
2. `fetchToolsForClient()` (linea 1743) registra cada tool con un `.call()` override
3. `.call()` ejecuta `callMCPToolWithUrlElicitationRetry()` que envuelve `callMCPTool()`
4. `callMCPTool()` (linea 3029) invoca `client.callTool()` del SDK MCP:

```typescript
client.callTool(
  {
    name: tool,          // nombre original del tool (sin prefijo mcp__)
    arguments: args,     // argumentos del modelo
    _meta: meta,         // metadata (toolUseId para tracking)
  },
  CallToolResultSchema,  // schema de validacion de respuesta
  {
    signal,              // AbortSignal para cancelacion
    timeout: timeoutMs,  // timeout configurable
    onprogress,          // callback de progreso del SDK
  },
)
```

### Naming convention
- Formato wire: `mcp__<normalized_server>__<tool_name>`
- La normalizacion reemplaza caracteres no-alfanumericos con `_`
- El SDK mode puede omitir el prefijo con `CLAUDE_AGENT_SDK_MCP_NO_PREFIX=true`

---

## 2. Timeout Handling

### Tres niveles de timeout independientes:

| Nivel | Valor Default | Env Var | Funcion |
|-------|--------------|---------|---------|
| **Conexion** | 30,000ms (30s) | `MCP_TIMEOUT` | Tiempo maximo para establecer conexion al servidor |
| **Request HTTP** | 60,000ms (60s) | N/A | Timeout por request individual (POST). GET/SSE excluidos |
| **Tool call** | 100,000,000ms (~27.8h) | `MCP_TOOL_TIMEOUT` | Timeout para ejecucion de un tool. Practicamente infinito por defecto |

### Implementacion del timeout de conexion (linea 1048):

```typescript
const connectPromise = client.connect(transport)
const timeoutPromise = new Promise<never>((_, reject) => {
  const timeoutId = setTimeout(() => {
    transport.close().catch(() => {})
    reject(new Error(`connection timed out after ${getConnectionTimeoutMs()}ms`))
  }, getConnectionTimeoutMs())
  connectPromise.then(() => clearTimeout(timeoutId), () => clearTimeout(timeoutId))
})
await Promise.race([connectPromise, timeoutPromise])
```

### Implementacion del timeout de tool call (linea 3068):

Doble timeout: el propio del SDK (`timeout` parameter) + un `Promise.race` externo con `setTimeout`. El externo existe como fallback para casos donde el timeout interno del SDK no funciona (ej: stream SSE roto mid-request).

### Timeout de requests HTTP (linea 492):

`wrapFetchWithTimeout()` envuelve cada fetch con un `AbortController` + `setTimeout` de 60s. Usa `setTimeout` en vez de `AbortSignal.timeout()` para poder hacer `clearTimeout()` al completar — evita memory leak de 2.4KB/request en Bun.

**Excepcion critica:** GET requests NO tienen timeout porque en MCP son streams SSE de larga duracion.

---

## 3. Tool Call Batching

**No hay batching de tool calls individuales.** Cada tool call se ejecuta independientemente como una invocacion separada de `callMCPTool()`.

### Donde SI hay batching:

#### a) Conexion de servidores (linea 2218-2402)
Los servidores se conectan en batches concurrentes usando `pMap`:

| Tipo | Concurrencia Default | Env Var |
|------|---------------------|---------|
| **Local** (stdio/sdk) | 3 | `MCP_SERVER_CONNECTION_BATCH_SIZE` |
| **Remote** (sse/http/ws) | 20 | `MCP_REMOTE_SERVER_CONNECTION_BATCH_SIZE` |

Ambos grupos se procesan en paralelo (`Promise.all`). Dentro de cada grupo, `pMap` libera slots conforme terminan (no espera batch completo).

#### b) State updates en React (linea 207-308)
Las actualizaciones de estado MCP se acumulan durante 16ms (`MCP_BATCH_FLUSH_MS`) y se aplican en un solo `setAppState`:

```typescript
const MCP_BATCH_FLUSH_MS = 16  // ~1 frame
pendingUpdatesRef.current.push(update)
if (flushTimerRef.current === null) {
  flushTimerRef.current = setTimeout(flushPendingUpdates, MCP_BATCH_FLUSH_MS)
}
```

#### c) Fetch de tools+commands+resources (linea 2344)
Para cada servidor conectado, se hacen 4 fetches en paralelo:
```typescript
const [tools, mcpCommands, mcpSkills, resources] = await Promise.all([
  fetchToolsForClient(client),
  fetchCommandsForClient(client),
  fetchMcpSkillsForClient(client),
  fetchResourcesForClient(client),
])
```

---

## 4. Manejo de Errores de MCP Servers

### Clases de error custom:

| Clase | Proposito |
|-------|-----------|
| `McpAuthError` | Token OAuth expirado (401). Marca servidor como `needs-auth` |
| `McpSessionExpiredError` | Sesion HTTP expirada (404 + JSON-RPC -32001). Trigger reconnect |
| `McpToolCallError` | Tool retorno `isError: true`. Lleva `_meta` del resultado |

### Flujo de error en tool calls (linea 1910-1968):

1. **Session expired** (`McpSessionExpiredError`): Retry automatico 1 vez (`MAX_SESSION_RETRIES = 1`). Limpia cache de conexion, reconecta, y reintenta.

2. **URL Elicitation required** (error code -32042): Hasta 3 reintentos (`MAX_URL_ELICITATION_RETRIES = 3`). Muestra URL al usuario, espera completacion, reintenta.

3. **Auth error** (401 / `UnauthorizedError`): Lanza `McpAuthError` que el caller captura para marcar servidor como `needs-auth`.

4. **Connection closed** (McpError -32000): Si es en HTTP/claudeai-proxy, limpia cache de conexion para forzar re-init.

5. **MCP SDK errors** genericos: Se envuelven en `TelemetrySafeError` para telemetria segura (no enviar paths ni codigo).

6. **`isError: true` en resultado**: Extrae texto del primer content block como detalle de error.

7. **AbortError** (usuario presiona Esc): Se swallows silenciosamente, retorna `{ content: undefined }`.

### Error handling durante conexion:

- `UnauthorizedError` de SSE/HTTP: Retorna `{ type: 'needs-auth' }`, cachea por 15 min
- Timeout de conexion: Cierra transport, retorna `{ type: 'failed' }`
- Cualquier otro error: Retorna `{ type: 'failed', error: message }`

### Deteccion de connection drop (linea 1249-1365):

Errores terminales detectados por substring matching:
- `ECONNRESET`, `ETIMEDOUT`, `EPIPE`, `EHOSTUNREACH`, `ECONNREFUSED`
- `Body Timeout Error`, `terminated`
- `SSE stream disconnected`, `Failed to reconnect SSE stream`
- `Maximum reconnection attempts` (SDK agoto sus reintentos internos)

Despues de **3 errores terminales consecutivos** (`MAX_ERRORS_BEFORE_RECONNECT`), se cierra el transport forzosamente via `client.close()`, lo que rechaza todos los `callTool()` pendientes y activa el handler `onclose` para reconexion.

---

## 5. Connection Lifecycle

### Estados de conexion:

```
pending → connected → (onclose) → pending → connected  [exito]
pending → connected → (onclose) → pending → failed      [fallo tras reintentos]
pending → failed                                         [conexion inicial fallo]
pending → needs-auth                                     [requiere OAuth]
disabled                                                  [deshabilitado por usuario]
```

### Connect (linea 595-1641):

`connectToServer()` esta **memoizado** con `lodash.memoize` (cache key = `name-JSON(config)`).

Flujo:
1. Determinar tipo de transport (stdio, sse, http, ws, sdk, claudeai-proxy)
2. Crear transport con headers, auth provider, proxy config
3. Crear `Client` del SDK MCP con capabilities `{ roots: {}, elicitation: {} }`
4. Registrar handler para `ListRoots` (retorna `file://` del CWD)
5. `client.connect(transport)` con `Promise.race` vs timeout
6. Obtener capabilities, server version, instructions
7. Registrar handlers de error y close
8. Registrar cleanup en el cleanup registry global
9. Retornar `ConnectedMCPServer`

### Disconnect / Close (linea 1404-1570):

Para servers **stdio**, escalamiento de senales:
1. `SIGINT` → esperar 100ms
2. Si sigue vivo: `SIGTERM` → esperar 400ms
3. Si sigue vivo: `SIGKILL`
4. Failsafe: timeout absoluto de 600ms

Para **todos** los tipos:
- `client.close()` cierra el transport
- Se limpia la referencia del cleanup registry

### Reconnect (linea 87-462 en useManageMCPConnections.ts):

Solo para transports **remotos** (no stdio, no sdk):

- **Exponential backoff**: 1s, 2s, 4s, 8s, 16s (cap 30s)
- **Max intentos**: 5 (`MAX_RECONNECT_ATTEMPTS`)
- **Estado intermedio**: servidor se marca como `pending` con `reconnectAttempt/maxReconnectAttempts`
- **Cancelable**: timers se almacenan en un `Map<serverName, timer>` para cancelacion
- Se verifica si el servidor fue deshabilitado entre reintentos

### Cache invalidation en onclose (linea 1374-1401):

Cuando una conexion se cierra:
1. Limpiar cache de `connectToServer` (memoize key)
2. Limpiar caches de `fetchToolsForClient`, `fetchResourcesForClient`, `fetchCommandsForClient`, `fetchMcpSkillsForClient`
3. La proxima invocacion crea una conexion fresca

### Auth cache (linea 257-316):

- Servers que retornan 401 se cachean por **15 minutos** (`MCP_AUTH_CACHE_TTL_MS`)
- El cache se serializa como JSON a disco (`~/.claude/mcp-needs-auth-cache.json`)
- Las escrituras se serializan via promise chain para evitar race conditions
- `clearMcpAuthCache()` borra el archivo completo

### Procesamiento de resultados grandes (linea 2720-2799):

Cuando el output de un tool excede `MAX_MCP_OUTPUT_TOKENS` (default 25,000 tokens):
1. Si `ENABLE_MCP_LARGE_OUTPUT_FILES` esta desactivado: truncar a `maxTokens * 4` chars
2. Si contiene imagenes: truncar (no se pueden persistir como JSON)
3. Si no: persistir a archivo temporal y retornar instrucciones de lectura al modelo

---

## Resumen Ejecutivo

| Aspecto | Implementacion |
|---------|---------------|
| **Protocolo** | JSON-RPC via SDK oficial `@modelcontextprotocol/sdk`, no custom |
| **Transports** | stdio, SSE, HTTP Streamable, WebSocket, SDK in-process, claude.ai proxy |
| **Timeout tool calls** | ~27.8 horas default (configurable). No agresivo |
| **Timeout conexion** | 30s default (configurable via `MCP_TIMEOUT`) |
| **Timeout HTTP request** | 60s hard-coded. GET excluidos (SSE long-lived) |
| **Batching de calls** | No hay. Cada tool call es independiente |
| **Batching de conexiones** | Si: 3 local / 20 remote concurrentes via `pMap` |
| **Retry en tool calls** | 1 retry automatico por session expiry. 3 para URL elicitation |
| **Retry en conexion** | Exponential backoff hasta 5 intentos (solo remote) |
| **Error handling** | 7+ tipos de error con handling especializado |
| **Memoizacion** | Conexiones y fetches memoizados con invalidacion en close |
| **Output grande** | Truncacion o persistencia a archivo (>25K tokens) |
| **Cleanup de procesos** | Escalamiento SIGINT→SIGTERM→SIGKILL para stdio servers |
