# Reporte Adicional: Features Ocultos — Claude Code v2.1.88 (Análisis Profundo)
> Autor: ADA (Team SEAL) — Ingeniería inversa exhaustiva de source map + análisis full_src/
> Fecha: 2026-04-19
> Método: Lectura completa directorios plugins/, services/, tools/, migrations/, native-ts/, screens/, upstreamproxy/, schemas/
> Complementa: ada_hidden_features_claudecode_20260418.md (SPEC_01-16)

---

## RESUMEN EJECUTIVO (Hallazgos Nuevos)

Este análisis profundo revela arquitectura de **multiagent systems**, **plugin marketplace completo**, **system de servicios distribuidos**, y **herramientas experimentales no documentadas**. Se encontraron:

1. **VERIFICATION_AGENT** — built-in agent con sistema de verification y quality gates
2. **Plugin Marketplace** — system de instalación, versionado, y manifests completo
3. **Coordinator Mode** — orquestación paralela de workers con scratchpad
4. **Hook System** — 4 tipos de hooks (bash, prompt, http, agent) para automation
5. **Services Layer** — 21+ servicios internos (MCP, autoDream, compaction, team memory sync)
6. **Upstream Proxy** — sistema de proxy enterprise para CCR containers
7. **Native TypeScript** — módulos optimizados (color-diff, file-index, yoga-layout)
8. **LODESTONE** — Bun feature flag sin documentar (protocolo tree-shaking)

---

## 1. VERIFICATION_AGENT — Built-in Verification Specialist

**Ubicación:** `/tools/AgentTool/built-in/verificationAgent.ts`

### Estructura

```typescript
export const VERIFICATION_AGENT: BuiltInAgentDefinition = {
  agentType: 'verification',
  whenToUse: 'Use this agent to verify that implementation work is correct...',
  color: 'red',
  background: true,
  disallowedTools: [AGENT_TOOL_NAME, EXIT_PLAN_MODE, FILE_EDIT, FILE_WRITE, NOTEBOOK_EDIT],
  model: 'inherit'
}
```

### Sistema de Verification

**Características críticas:**

1. **Anti-verification avoidance detection** — Detecta cuando alguien lee código en lugar de ejecutar verificaciones
2. **80% bias detector** — Busca falsos positivos en happy-path (UIs bonitas que no funcionan)
3. **Adversarial probing obligatorio** — Exige pruebas de boundary, concurrency, idempotency, orphan ops
4. **Command-level evidence** — CADA check requiere output real de comandos ejecutados
5. **VERDICT system** — Emite PASS/FAIL/PARTIAL (parsed por caller)

**Restricciones impuestas:**
- NO puede modificar archivos en proyecto (no FileEdit, FileWrite)
- NO puede llamarse a sí mismo (no AgentTool)
- SI permite: Bash, Read, WebFetch, MCP tools, browser automation

**Uso triggerado:**
```typescript
// tools/TaskUpdateTool/TaskUpdateTool.ts:335
if (feature('VERIFICATION_AGENT') &&
    closedTasksCount >= 3 &&
    !anyTaskWasVerification) {
  resultContent += `\nNOTE: Spawn verification agent before final summary`
}
```

**GrowthBook flag:** `tengu_hive_evidence` (controla si se incluye en built-in agents)

---

## 2. PLUGIN MARKETPLACE — Architecture Completa

### Ubicación
- `/plugins/builtinPlugins.ts` — registry de plugins built-in
- `/plugins/bundled/index.ts` — inicialización (actualmente vacío, scaffolding)
- `/services/plugins/` — 4 archivos operacionales

### Componentes

#### 2.1 Plugin Types & Manifest

```typescript
// Desde builtinPlugins.ts
type BuiltinPluginDefinition = {
  name: string
  description: string
  version: string
  defaultEnabled?: boolean
  isAvailable?: () => boolean
  hooks?: HooksConfig
  mcpServers?: MCPServerConfig[]
  skills?: BundledSkillDefinition[]
}

type LoadedPlugin = {
  name: string
  manifest: { name, description, version }
  path: 'builtin' | string
  source: 'name@marketplace'
  repository: string
  enabled: boolean
  isBuiltin: boolean
  hooksConfig?: HooksConfig
  mcpServers?: MCPServerConfig[]
}
```

#### 2.2 Plugin Installation Scopes

```typescript
// services/plugins/pluginOperations.ts
export const VALID_INSTALLABLE_SCOPES = ['user', 'project', 'local'] as const

export type PluginScope = 'user' | 'project' | 'local' | 'builtin'
```

**Precedencia:** local > project > user > builtin

#### 2.3 Plugin Marketplace Registry

**Archivos:**
- `PluginInstallationManager.ts` — instalaciones de background + reconciliación
- `pluginOperations.ts` — operaciones CRUD + versionado
- Métodos públicos:
  - `getDeclaredMarketplaces()`
  - `loadKnownMarketplacesConfig()`
  - `diffMarketplaces(declared, materialized)`
  - `reconcileMarketplaces()`
  - `getPluginInstallationFromV2()`

**Marketplace Features:**
- Versionado semántico (SemVer)
- Instalación paralela con estado `pending`
- Diff + reconciliación automática
- Fallback a versión anterior en fallo

---

## 3. COORDINATOR MODE — Parallel Worker Orchestration

**Ubicación:** `/coordinator/coordinatorMode.ts`

### Activación

```typescript
feature('COORDINATOR_MODE') &&
isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)
```

### Arquitectura

```typescript
function isCoordinatorMode(): boolean {
  if (feature('COORDINATOR_MODE')) {
    return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)
  }
  return false
}

function getCoordinatorUserContext(
  mcpClients: ReadonlyArray<{ name: string }>,
  scratchpadDir?: string
): { [k: string]: string }
```

### Capacidades

1. **Worker Tool Filtering** — Solo ASYNC_AGENT_ALLOWED_TOOLS (Read, Write, Bash, Task*, Agent)
2. **Scratchpad para shared state** — GrowthBook gate: `tengu_scratch`
3. **MCP Client propagation** — Lista de servidores disponibles para workers
4. **Mode matching** — Detecta modo de sesión resumida y flipea env var automáticamente
5. **Analytics logging** — `tengu_coordinator_mode_switched` event

**Tools disponibles en worker:**
- AgentTool (para spawning sub-workers)
- TaskStopTool (stop coordinado)
- SendMessageTool (comunicación inter-agent)
- (No: FileEdit, ExitPlanMode en SIMPLE mode)

---

## 4. HOOKS SYSTEM — Event Automation Framework

**Ubicación:** `/schemas/hooks.ts` (defines schema for all hook types)

### 4 Tipos de Hooks

#### 4.1 Bash Command Hook

```typescript
type BashCommandHookSchema = {
  type: 'command'
  command: string          // Shell command
  if?: string             // Permission rule filter
  shell?: 'bash'|'powershell'
  timeout?: number        // seconds
  statusMessage?: string
  once?: boolean          // Run once then remove
  async?: boolean         // Non-blocking
  asyncRewake?: boolean   // Wake model on exit code 2
}
```

#### 4.2 Prompt Hook

```typescript
type PromptHookSchema = {
  type: 'prompt'
  prompt: string          // LLM prompt to evaluate
  model?: string          // e.g., "claude-sonnet-4-6"
  if?: string
  timeout?: number
  statusMessage?: string
  once?: boolean
}
```

#### 4.3 HTTP Hook

```typescript
type HttpHookSchema = {
  type: 'http'
  url: string            // POST target
  if?: string
  headers?: Record<string, string>  // env var interpolation
  allowedEnvVars?: string[]
  timeout?: number
  statusMessage?: string
  once?: boolean
}
```

#### 4.4 Agent Hook

```typescript
type AgentHookSchema = {
  type: 'agent'
  prompt: string         // Verification prompt
  if?: string
  timeout?: number       // default 60s
  model?: string
  statusMessage?: string
  once?: boolean
}
```

### Hook Events (Discriminated Union)

```typescript
export const HOOK_EVENTS = [
  'before-command-line',
  'after-command-line',
  'before-tool-use',
  'after-tool-use',
  'on-permission-denied',
  'on-error',
  // ... más
] as const
```

### Matcher Configuration

```typescript
type HookMatcher = {
  matcher?: string              // Pattern (e.g., tool names)
  hooks: HookCommand[]         // Array of hooks
}

type HooksSettings = Partial<Record<HookEvent, HookMatcher[]>>
```

**Ventajas:**
- If conditions usan permission rule syntax (Bash("git *"), Read("*.ts"))
- Evita spawning hooks para non-matching inputs
- Support env var interpolation en headers HTTP
- Timeout per-hook + statusMessage para UX

---

## 5. SERVICES LAYER — 21+ Servicios Internos

**Ubicación:** `/services/`

### Categorización

#### 5.1 MCP Services (11 archivos)

```
services/mcp/
  ├── client.ts                   — Cliente MCP principal
  ├── config.ts                   — Config MCP
  ├── auth.ts                     — OAuth/auth para MCP servers
  ├── claudeai.ts                 — Claude.ai MCP server
  ├── xaa.ts, xaaIdpLogin.ts     — OIDC enterprise SSO
  ├── SdkControlTransport.ts      — Control transport SDK
  ├── InProcessTransport.ts       — In-process transport
  ├── headersHelper.ts            — Header helpers (beta, auth)
  ├── normalization.ts            — Normalización de tool names
  ├── officialRegistry.ts         — Registry de servidores oficiales
  └── envExpansion.ts             — Variable expansion $VAR syntax
```

**Features especiales:**
- `MCP_RICH_OUTPUT` feature gate — Rich formatting para output
- `MCP_SKILLS` feature gate — Skills basadas en MCP
- Soporte MITM proxy (relay.ts) para CCR containers

#### 5.2 Analytics Services (7 archivos)

```
services/analytics/
  ├── index.ts                    — Main logging API
  ├── growthbook.ts              — GrowthBook client (feature gates)
  ├── datadog.ts                 — Datadog integration
  ├── firstPartyEventLogger.ts    — 1P event batching
  ├── firstPartyEventLoggingExporter.ts — Exporter
  ├── sinkKillswitch.ts           — Killswitch analytics
  ├── metadata.ts                — Telemetry metadata extractor
  └── config.ts                  — Config + headers
```

**Telemetry events (all `tengu_*` prefixed):**
```
tengu_sonnet45_to_46_migration
tengu_coordinator_mode_switched
tengu_bash_tool_denied
tengu_task_completed
... (100+)
```

#### 5.3 Memory & Compaction (4 servicios)

```
services/autoDream/
  ├── autoDream.ts               — Auto-consolidación sesión
  ├── consolidationPrompt.ts      — Prompt para síntesis
  ├── consolidationLock.ts        — Lock para evitar race
  └── config.ts                  — Config

services/extractMemories/
  ├── extractMemories.ts          — Extrae insights de sesión
  └── prompts.ts

services/compact/
  ├── compact.ts                 — Compactación principal
  ├── autoCompact.ts             — Auto-trigger (token budget)
  ├── microCompact.ts            — Microcompaction pattern matching
  ├── cachedMCConfig.ts          — Cache microcompact config (CACHED_MICROCOMPACT)
  └── sessionMemoryCompact.ts     — Session-level compaction
```

#### 5.4 Tool Execution & Orchestration (3 servicios)

```
services/tools/
  ├── toolOrchestration.ts        — Partición en batches
  ├── toolExecution.ts            — Ejecución + telemetry
  ├── toolHooks.ts               — Pre/post-tool hooks
  └── StreamingToolExecutor.ts    — Streaming results
```

**Concurrency strategy:**
- Partition tool calls: read-only tools en paralelo, mutation serially
- Concurrent safety check: `tool.isConcurrencySafe(input)`
- Max concurrency (env var): `CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY` (default 10)

#### 5.5 Otros Servicios

```
services/
  ├── LSP/                        — Language Server Protocol
  ├── oauth/                      — OAuth provider integration
  ├── policyLimits/               — Rate limiting + policy
  ├── remoteManagedSettings/       — Sync settings remotamente
  ├── settingsSync/               — Upload/download settings
  ├── teamMemorySync/             — Sync memoria con equipo
  ├── PromptSuggestion/           — Speculación + sugerencias
  ├── SessionMemory/              — Gestión sesiones
  ├── notifier.ts                 — Sistema notificaciones
  ├── voiceStreamSTT.ts           — Voice STT streaming
  ├── diagnosticTracking.ts       — Tracking diagnóstico
  ├── AgentSummary/               — Resumen de agentes
  ├── internalLogging.ts          — Logging interno (debug)
  └── MagicDocs/                  — Docs dinámicos
```

---

## 6. TOOLS — Herramientas Documentadas + Ocultas

**Ubicación:** `/tools/` (44 subdirectorios)

### Tools Principales (visible en tools.ts)

**Core I/O:**
- BashTool, FileReadTool, FileEditTool, FileWriteTool
- GlobTool, GrepTool (embedded si available)
- NotebookEditTool, WebFetchTool

**Planning & Tasks:**
- TaskCreateTool, TaskGetTool, TaskUpdateTool, TaskListTool
- EnterPlanModeTool, ExitPlanModeV2Tool
- VerifyPlanExecutionTool (env: `CLAUDE_CODE_VERIFY_PLAN=true`)

**Agents & Communication:**
- AgentTool, SkillTool, SendMessageTool
- AskUserQuestionTool

**Feature-gated Tools:**

```typescript
// tools.ts
const OverflowTestTool = feature('OVERFLOW_TEST_TOOL') ? ... : null
const CtxInspectTool = feature('CONTEXT_COLLAPSE') ? ... : null
const TerminalCaptureTool = feature('TERMINAL_PANEL') ? ... : null
const WebBrowserTool = feature('WEB_BROWSER_TOOL') ? ... : null
const SnipTool = feature('HISTORY_SNIP') ? ... : null
const ListPeersTool = feature('UDS_INBOX') ? ... : null
const WorkflowTool = feature('WORKFLOW_SCRIPTS') ? ... : null
const SleepTool = feature('PROACTIVE') || feature('KAIROS') ? ... : null
const PushNotificationTool = feature('KAIROS_PUSH_NOTIFICATION') ? ... : null
const REPLTool = process.env.USER_TYPE === 'ant' ? ... : null
```

### Tool Orchestration

```typescript
// services/tools/toolOrchestration.ts
export async function* runTools(
  toolUseMessages: ToolUseBlock[],
  assistantMessages: AssistantMessage[],
  canUseTool: CanUseToolFn,
  toolUseContext: ToolUseContext
): AsyncGenerator<MessageUpdate, void>
```

**Estrategia:**
1. Partition tool calls en batches (read-only vs mutation)
2. Read-only tools ejecutados en paralelo (concurrency-safe)
3. Mutation tools ejecutados serialmente
4. Context modifiers aplicados después batch

---

## 7. LODESTONE — Bun Feature Flag (Undocumented)

**Ubicación:** Múltiples archivos (6 mencionados)

### Uso

```typescript
// interactiveHelpers.tsx:176
if (feature('LODESTONE')) {
  // Conditional interactive code
}

// utils/backgroundHousekeeping.ts:10
const registerProtocolModule = feature('LODESTONE')

// main.tsx:647, 3781
if (feature('LODESTONE')) {
  // Critical initialization
}
```

### Propósito (análisis)

- **Nombre:** Lodestone = "piedra imán" (brújula)
- **Patrón:** Gatea protocol module, housekeeping background, interactive helpers
- **Teoría:** Tree-shaking de código protocol-heavy en builds externos
  - Cuando LODESTONE=false, todo el módulo protocol se DCE
  - Bun conoce que no existe `registerProtocolModule` en external builds
- **Mención en comentario:** "which would defeat LODESTONE tree-shaking"

**Contexto:** `/utils/deepLink/terminalPreference.ts:6`
```
* (which would defeat LODESTONE tree-shaking).
```

**Conclusión:** LODESTONE es **compile-time tree-shaking de protocolo** en Bun. Builds Anthropic internos (ant) incluyen código protocol que builds públicos descartan.

---

## 8. UPSTREAM PROXY — Enterprise Container Proxy

**Ubicación:** `/upstreamproxy/`

### Arquitectura

```typescript
// upstreamproxy.ts
export async function initUpstreamProxy(opts?: {
  tokenPath?: string
  systemCaPath?: string
  caBundlePath?: string
  ccrBaseUrl?: string
}): Promise<UpstreamProxyState>
```

### Flujo de Inicialización

1. **Check env:** `CLAUDE_CODE_REMOTE` + `CCR_UPSTREAM_PROXY_ENABLED`
2. **Read token:** `/run/ccr/session_token` (CCR container-only)
3. **Set prctl:** `PR_SET_DUMPABLE=0` — bloquea ptrace same-UID
4. **Download CA cert:** Descarga CA del proxy upstream
5. **Concatenate bundle:** Truststore del sistema + CA proxy
6. **Start relay:** CONNECT→WebSocket relay (relay.ts)
7. **Unlink token file:** Token queda solo en heap
8. **Set env vars:** `HTTPS_PROXY`, `SSL_CERT_FILE`

### NO_PROXY List (Hardcoded)

```
localhost, 127.0.0.1, ::1
169.254.0.0/16 (IMDS)
10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16 (RFC1918)
anthropic.com, .anthropic.com, *.anthropic.com  (No MITM Anthropic API)
github.com, *.github.com, api.github.com
registry.npmjs.org, pypi.org, files.pythonhosted.org
index.crates.io, proxy.golang.org
```

### Relay Transport

```typescript
// relay.ts
const relay = startUpstreamProxyRelay()
```

Implementa CONNECT→WebSocket tunnel para HTTPS interception sin breaking Anthropic API.

---

## 9. MIGRATIONS — Schema & Model Versioning

**Ubicación:** `/migrations/` (11 archivos)

### Migration Pattern

Cada migration es idempotent y solo se ejecuta si condiciones se cumplen:

```typescript
// migrateSonnet45ToSonnet46.ts
export function migrateSonnet45ToSonnet46(): void {
  if (getAPIProvider() !== 'firstParty') return
  if (!isProSubscriber() && !isMaxSubscriber()) return
  
  const model = getSettingsForSource('userSettings')?.model
  if (model !== 'claude-sonnet-4-5-20250929') return
  
  updateSettingsForSource('userSettings', {
    model: 'sonnet' // alias → 4.6
  })
  
  logEvent('tengu_sonnet45_to_46_migration', {
    from_model: model,
    has_1m: has1m
  })
}
```

### Migraciones Activas (v2.1.88)

| Migration | Propósito |
|-----------|-----------|
| `migrateSonnet45ToSonnet46` | 4.5 → 4.6 alias (Pro/Max/Team Premium) |
| `migrateSonnet1mToSonnet45` | 1M → 4.5 modelo |
| `migrateOpusToOpus1m` | Opus → Opus 1M |
| `migrateLegacyOpusToCurrent` | Legacy Opus → current |
| `migrateFennecToOpus` | Fennec → Opus (internal rename) |
| `migrateAutoUpdatesToSettings` | Legacy → settings.json |
| `migrateBypassPermissionsAcceptedToSettings` | Legacy → settings |
| `migrateEnableAllProjectMcpServersToSettings` | MCP servers config |
| `migrateReplBridgeEnabledToRemoteControlAtStartup` | Bridge mode rename |
| `resetAutoModeOptInForDefaultOffer` | Reinicia auto-mode opt-in con TRANSCRIPT_CLASSIFIER |
| `resetProToOpusDefault` | Pro default → Opus |

---

## 10. NATIVE TYPESCRIPT MODULES — Optimized Components

**Ubicación:** `/native-ts/` (3 subdirectorios)

### 10.1 Color Diff

```typescript
// native-ts/color-diff/index.ts (pure TS port)
type NativeModule = {
  ColorDiff: typeof ColorDiff
  ColorFile: typeof ColorFile
  getSyntaxTheme: (themeName: string) => SyntaxTheme
}
```

**Propósito:** Pure TypeScript port de Rust vendor/color-diff-src
- Syntax highlighting vía highlight.js (no syntect)
- Word diff vía npm diff (no Rust similar crate)
- Lazy loading: ~50MB highlight.js se carga on-demand
- Elimina costo de bundle inicial

### 10.2 File Index

```typescript
// native-ts/file-index/index.ts
// Indexing + searching de archivos en workspace
```

### 10.3 Yoga Layout

```typescript
// native-ts/yoga-layout/
  ├── index.ts
  └── enums.ts
```

Flexbox layout engine (JS/WASM port de Yoga).

**Ventaja:** Evita NAPI dlopen, puro JS/WASM = compatible con todas plataformas.

---

## 11. SCREENS — UI Screens Principales

**Ubicación:** `/screens/` (3 archivos, solo compiled)

### 11.1 Doctor.tsx (~900 líneas)

Diagnostic screen que chequea:
- Version updates (GCS, NPM dist tags)
- Keybinding conflicts
- MCP parsing warnings
- Sandbox doctor section
- Settings validation
- LSP diagnostics
- Context warnings
- Agent configuration

### 11.2 REPL.tsx (~900 KB, huge!)

**Screen más compleja del proyecto.** Features:

- VM en JS para ejecutar código en sandbox
- Stdin/stdout capture
- File system mocking
- Terminal emulation
- Syntax highlighting
- Error recovery

### 11.3 ResumeConversation.tsx

Resume UI para sesiones anterior (KAIROS mode).

---

## 12. SCHEMAS — Hook Validation Schemas

**Ubicación:** `/schemas/hooks.ts` (única file, 223 líneas)

### Zod Schema Tree

```
HookCommandSchema (union)
├── BashCommandHookSchema
├── PromptHookSchema
├── AgentHookSchema
└── HttpHookSchema

HookMatcherSchema
├── matcher?: string
└── hooks: HookCommand[]

HooksSchema
└── Partial<Record<HookEvent, HookMatcher[]>>
```

**Circular dependency solution:** Extracted para break cyclo entre:
- `settings/types.ts` (imports HooksSchema)
- `plugins/schemas.ts` (imports HooksSchema)

---

## ANEXO A: FEATURE FLAGS COMPLETO (91 total)

### Tier 1: Critical (afecta arquitectura)

```
COORDINATOR_MODE          — Worker orchestration
LODESTONE                 — Protocol tree-shaking
KAIROS                    — Assistant mode (background sessions)
BRIDGE_MODE               — Remote control
VOICE_MODE                — Voice input/output
VERIFICATION_AGENT        — Built-in verification
EXTRACT_MEMORIES          — Sesión → memories
EXTRACT_MEMORIES          — Auto-consolidation
```

### Tier 2: Feature Sets (nuevas capabilities)

```
CHICAGO_MCP               — Computer Use
WEB_BROWSER_TOOL          — Browser automation
WORKFLOW_SCRIPTS          — Workflow/scripts
MONITOR_TOOL              — Background task monitoring
MCP_SKILLS                — Skills from MCP servers
EXPERIMENTAL_SKILL_SEARCH — Skill discovery
TRANSCRIPT_CLASSIFIER     — AFK mode
BASH_CLASSIFIER           — Auto-approve safe bash
```

### Tier 3: Optimization & Testing

```
CACHED_MICROCOMPACT       — Cache MC config
CONTEXT_COLLAPSE          — Memory compaction
REACTIVE_COMPACT          — Reactive compaction
TOKEN_BUDGET              — Token budget tracking
HISTORY_SNIP              — History snipping
CACHED_MICROCOMPACT       — Cache MC config
```

### Tier 4: Experimental (high risk)

```
ULTRAPLAN                 — Navigation CCR
ULTRATHINK                — Extended thinking
ABLATION_BASELINE         — A/B baseline
ANTI_DISTILLATION_CC      — Privacy measure
```

### Tier 5: Enterprise/Internal

```
DAEMON                    — Worker daemon
DIRECT_CONNECT            — Direct network
SSH_REMOTE                — SSH remote exec
BYOC_ENVIRONMENT_RUNNER   — Bring-your-own-compute
CCR_AUTO_CONNECT          — Auto-connect CCR
CCR_MIRROR                — Mirror CCR
```

### Tier 6: Debugging/Analytics

```
DUMP_SYSTEM_PROMPT        — evals security
HARD_FAIL                 — Fail on error
PERFETTO_TRACING          — Performance tracing
SLOW_OPERATION_LOGGING    — Logging operaciones lentas
SHOT_STATS                — Screenshot stats
ENHANCED_TELEMETRY_BETA   — Telemetry beta
NATIVE_CLIENT_ATTESTATION — Client attestation
```

---

## ANEXO B: GrowthBook Feature Gates (GrowthBook runtime)

### Gates Críticos (Antropic-controlados)

```
tengu_ant_model_override              → Override modelos (ants only)
tengu_amber_flint                     → Killswitch agent swarms externos
tengu_amber_stoat                     → Explore/plan agents enabled
tengu_hive_evidence                   → Verification agent enabled
tengu_scratch                         → Coordinator scratchpad
tengu_hawthorn_window                 → Budget tool results (remotely modifiable)
tengu_1p_event_batch_config          → Batching telemetría
tengu_kairos_cron_config             → Jitter cron tuning
tengu_max_version_config             → Killswitch upgrade
tengu_bridge_mode                     → Bridge/remote control
tengu_ultraplan_model                 → CCR navigation model
```

### Sonic Gates (model-tuning)

```
tengu_sonic_v1
tengu_sonic_v2
... (versioning del modelo sonic para CCR)
```

---

## ANEXO C: Built-in Agents (builtInAgents.ts)

```typescript
agents = [
  GENERAL_PURPOSE_AGENT,        // Siempre presente
  STATUSLINE_SETUP_AGENT,       // Siempre presente
  EXPLORE_AGENT,                // Si tengu_amber_stoat=true
  PLAN_AGENT,                   // Si tengu_amber_stoat=true
  CLAUDE_CODE_GUIDE_AGENT,      // Si no es SDK entrypoint
  VERIFICATION_AGENT,           // Si feature(VERIFICATION_AGENT) && tengu_hive_evidence=true
]
```

**Disabling:**
```
CLAUDE_AGENT_SDK_DISABLE_BUILTIN_AGENTS=1  → Blank slate (SDK mode only)
```

---

## ANEXO D: Environment Variables (61 total)

### Control de Características

```
CLAUDE_CODE_ENABLE_XAA                    — Enterprise OIDC
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS      — Agent swarms
CLAUDE_CODE_COORDINATOR_MODE              — Coordinator
CLAUDE_CODE_SIMPLE                        — Simple mode
CLAUDE_CODE_MESSAGING_SOCKET             — UDS socket
CLAUDE_CODE_REMOTE                        — CCR container
CLAUDE_CODE_ABLATION_BASELINE             — A/B baseline
CLAUDE_CODE_GB_BASE_URL                   — Custom GrowthBook
USER_TYPE=ant                             — Anthropic internal
```

### Limits & Tuning

```
CLAUDE_CODE_MAX_TOOL_USE_CONCURRENCY      — default 10
BASH_MAX_OUTPUT_DEFAULT                   — default 100KB
TASK_MAX_OUTPUT_DEFAULT                   — default task output
```

### Ablation/Disablers

```
CLAUDE_CODE_DISABLE_THINKING
DISABLE_INTERLEAVED_THINKING
DISABLE_COMPACT
DISABLE_AUTO_COMPACT
CLAUDE_CODE_DISABLE_AUTO_MEMORY
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS
```

---

## ANEXO E: Hallazgos de Seguridad/Privacidad

### 1. **Upstream Proxy Desensibilización**

CCR containers ejecutan MITM proxy para interception pero:
- NO intercepta Anthropic API (hardcoded exclude)
- NO intercepta GitHub, registros de paquetes
- Token `/run/ccr/session_token` deletedo después startup
- prctl(PR_SET_DUMPABLE,0) bloquea ptrace

### 2. **Team Memory Sync Guarding**

```
services/teamMemorySync/teamMemSecretGuard.ts
services/teamMemorySync/secretScanner.ts
```

Secret scanning antes de sync a equipo — evita exposure.

### 3. **Verification Agent Isolation**

- Disallowed tools: FILE_EDIT, FILE_WRITE (no mutation)
- Ejecuta en sandbox
- VERDICT parsing es event-driven (no side effects)

---

## CONCLUSIÓN

Claude Code v2.1.88 es un **sistema LLM-primeiro distribuido** con:

1. **Multiagent orchestration** (coordinator mode, verification, sub-agents)
2. **Full plugin ecosystem** (marketplace, versioning, hook system)
3. **Distributed services** (MCP, team sync, remote execution, compaction)
4. **Enterprise capabilities** (upstream proxy, team management, CCR)
5. **Compile-time + runtime gates** (Bun DCE + GrowthBook)

**Diferencias ant vs public:**
- Ant builds: LODESTONE, DAEMON, SSH_REMOTE, CCR native, direct upstream proxy
- Public builds: DCE la mayoría; version pública tiene subsets

**Implicaciones:**
- Usuarios `USER_TYPE=ant` pueden override cualquier feature gate
- GrowthBook gates son remotely mutable (tengu_*) durante runtime
- Verificación agent es gating de quality (PARTIAL→FAIL si no verifica)
- Plugin marketplace es future-ready (no plugins live yet pero scaffold complete)

