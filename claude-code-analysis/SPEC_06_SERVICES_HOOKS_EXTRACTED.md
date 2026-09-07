# SPEC_06_SERVICES_HOOKS — Extracted Analysis

Now I have enough data to produce the analysis. Let me write the report.

---

# Analisis Tecnico: Claude Code — Services y Hooks Internos

## 1. SERVICES — Arquitectura de Servicios

### 1.1 Analytics Pipeline (9 archivos)

**Arquitectura de doble sink con queue buffer:**

El sistema de analytics usa un patron desacoplado donde los eventos se encolan en un buffer global (`eventQueue`) hasta que la app se inicializa y conecta un sink. Esto evita perder eventos tempranos durante el startup.

**Sinks activos (Segment fue removido):**
- **Datadog** — Logs HTTP via `https://http-intake.logs.us5.datadoghq.com/api/v2/logs`. Token hardcodeado: `pubbbf48e6d78dae54bceaa4acf463299bf`. Batching cada 15s o al acumular 100 eventos. Solo envia en `NODE_ENV=production` y provider `firstParty`. Allowlist explicita de ~40 eventos (prefijo `tengu_*`).
- **First Party (1P)** — OpenTelemetry `BatchLogRecordProcessor` con `FirstPartyEventLoggingExporter` custom. Recibe el payload completo incluyendo campos `_PROTO_*` con PII que se enrutan a columnas privilegiadas de BigQuery.

**Proteccion de PII:**
- Tipo `AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS` (tipo `never` de TypeScript) fuerza casting explicito para documentar que el desarrollador verifico que no contiene datos sensibles.
- Campos `_PROTO_*` se stripean antes de Datadog (general-access) pero pasan a 1P (privileged).
- MCP tool names se redactan a `'mcp_tool'` para no exponer config del usuario.
- User ID se hashea a 30 buckets para cardinalidad en alertas.

**Event sampling:** Configurable por evento via GrowthBook config `tengu_event_sampling_config`. Rate 0-1, eventos sin config van a 100%.

**Kill switch por sink:** Config `tengu_frond_boric` (nombre ofuscado) permite deshabilitar Datadog o 1P independientemente, fail-open.

### 1.2 GrowthBook Feature Flags (~360 lineas)

**No es Statsig, es GrowthBook** pero las funciones se llaman `checkStatsigFeatureGate` por legacy naming.

**Patron de acceso:**
- `getFeatureValue_CACHED_MAY_BE_STALE()` — Lee valor cacheado en disco (`~/.claude.json` campo `cachedGrowthBookFeatures`), no bloquea.
- `getDynamicConfig_BLOCKS_ON_INIT()` — Espera a que GrowthBook se inicialice con valores remotos. Usado cuando se necesita valor fresco.
- `getDynamicConfig_CACHED_MAY_BE_STALE()` — Para JSON configs complejos sin bloqueo.

**Remote Eval:** GrowthBook usa evaluacion remota (no local SDK eval). El SDK envia atributos del usuario y recibe feature values ya evaluados.

**Atributos de targeting:**
```
id, sessionId, deviceID, platform, organizationUUID, accountUUID,
userType, subscriptionType, rateLimitTier, firstTokenTime, email,
appVersion, github (CI metadata)
```

**Overrides (ant-only):**
- `CLAUDE_INTERNAL_FC_OVERRIDES` env var — JSON de features override para eval harnesses.
- `/config Gates` tab — Overrides en runtime persistidos en `globalConfig.growthBookOverrides`.

**Refresh signal:** `onGrowthBookRefresh()` notifica a subscriptores cuando los feature values cambian. Usado por `useMainLoopModel` para re-resolver el modelo con valores frescos.

**Workaround critico:** La API devuelve `{value: ...}` en lugar de `{defaultValue: ...}` — transformacion explicita en `processRemoteEvalPayload`.

### 1.3 OAuth Service (5 archivos)

**Flujo OAuth 2.0 + PKCE completo:**
- Genera `codeVerifier` y `codeChallenge` (S256).
- Levanta `AuthCodeListener` en localhost para captura automatica.
- Dual flow: automatico (browser redirect a localhost) + manual (paste de codigo).
- Scopes: `ALL_OAUTH_SCOPES` o solo `CLAUDE_AI_INFERENCE_SCOPE` para tokens long-lived.
- Post-exchange: fetch de perfil para `subscriptionType` y `rateLimitTier`.
- Soporta `loginHint` (email), `loginMethod` (sso/magic_link/google), `orgUUID`.
- SDK mode: `skipBrowserOpen` para que el SDK client controle la UI.
- Dos URLs de auth: Console (`CONSOLE_AUTHORIZE_URL`) y Claude.ai (`CLAUDE_AI_AUTHORIZE_URL`).

### 1.4 AutoDream — Consolidacion de Memoria en Background (4 archivos)

**Concepto:** "Dream" como analogia del sueno — consolida memorias de sesiones recientes en archivos persistentes. Se ejecuta como subagente forkeado.

**Gate sequence (cheapest first):**
1. **Time gate:** Horas desde ultimo consolidation >= `minHours` (default 24h).
2. **Session gate:** Transcripts tocados desde ultimo consolidation >= `minSessions` (default 5).
3. **Lock gate:** No hay otro proceso consolidando.
4. **Exclusiones:** KAIROS mode, remote mode, auto-memory disabled.

**Feature flag:** `tengu_onyx_plover` (GrowthBook) — contiene `{enabled, minHours, minSessions}`. Setting local `autoDreamEnabled` override.

**Lock mechanism:** File-based lock cuyo `mtime` ES `lastConsolidatedAt`. El body contiene el PID del holder. Stale despues de 60min. Rollback revierte mtime en caso de fallo.

**El prompt de consolidacion tiene 4 fases:**
1. Orient — leer MEMORY.md y archivos existentes.
2. Gather — buscar senales en logs diarios y transcripts.
3. Consolidate — escribir/actualizar archivos de memoria.
4. Prune — mantener MEMORY.md como indice compacto (<25KB, <200 lineas).

**Restriccion del subagente:** Bash solo lectura (ls, find, grep, cat, etc). Solo puede escribir en el directorio de memoria.

### 1.5 ExtractMemories — Extraccion Post-Turn (2 archivos)

**Trigger:** Corre al final de cada query loop completo (cuando el modelo produce respuesta final sin tool calls) via `handleStopHooks`.

**Patron forked agent:** Fork perfecto de la conversacion principal que comparte prompt cache. Recibe los ultimos ~N mensajes y extrae memorias durables.

**Dos modos:**
- `buildExtractAutoOnlyPrompt` — Memoria individual (4 tipos taxonomicos).
- `buildExtractCombinedPrompt` — Memoria individual + team memory (con scope guidance por tipo).

**Tool budget del subagente:**
- FileRead, Grep, Glob, Bash (read-only), FileEdit, FileWrite (solo en directorio de memoria).
- Estrategia eficiente: Turn 1 = todos los reads en paralelo. Turn 2 = todos los writes en paralelo.

**Feature flag TEAMMEM:** Team memory se carga condicionalmente via `feature('TEAMMEM')` (dead code elimination en builds externos).

### 1.6 Compact System (11 archivos)

**Tres niveles de compaction:**

1. **AutoCompact** — Trigger automatico cuando tokens >= `effectiveContextWindow - 13,000`. Threshold configurable via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`. Circuit breaker despues de 3 fallos consecutivos (dato real: 1,279 sesiones tenian 50+ fallos consecutivos, ~250K API calls/dia desperdiciados).

2. **MicroCompact** — Compacta tool results individuales (solo tools compactables: FileRead, Bash, Grep, Glob, WebSearch, WebFetch, FileEdit, FileWrite). Patron time-based configurable via GrowthBook.

3. **SessionMemoryCompact** — Compact del session memory separado.

**Prompt de compaction:** Instruccion detallada de 9 secciones (Primary Request, Technical Concepts, Files, Errors, Problem Solving, User Messages, Pending Tasks, Current Work, Next Step). Analisis en tag `<analysis>` que se stripea antes de inyectar en contexto.

**Preamble critico:** `NO_TOOLS_PREAMBLE` — bloque agresivo que prohibe cualquier tool call. En Sonnet 4.6+, modelos con adaptive thinking intentan tool calls pese a instrucciones en trailer. Tasa de fallo: 2.79% en 4.6 vs 0.01% en 4.5.

### 1.7 Session Memory (3 archivos)

Mantiene un archivo markdown con notas sobre la conversacion actual. Corre periodicamente en background como subagente forkeado. Feature-gated via GrowthBook.

### 1.8 Team Memory Sync (5 archivos)

**Sync bidireccional server-authenticated:**
- `GET /api/claude_code/team_memory?repo={owner/repo}` — Pull.
- `PUT` — Push (delta upload, solo keys con hash diferente).
- Scoped por repo (git remote hash).
- Pull: server wins per-key. Push: upsert semantics.
- Borrado NO propaga (pull restaura localmente).
- Secret scanning antes de push. Max file size 250KB.

### 1.9 Otros Servicios Notables

**MCP (22 archivos):**
- `channelAllowlist`, `channelPermissions` — granular control de canales MCP.
- `claudeai.ts` — MCP servers de Claude.ai (Canva, Gmail, Google Calendar, Notion).
- `xaa.ts`, `xaaIdpLogin.ts` — External Auth Adapter para OAuth con MCP servers.
- `SdkControlTransport.ts` — Transport MCP para SDK control protocol.
- `officialRegistry.ts` — Registry de MCP servers oficiales.

**Plugins (3 archivos):** `PluginInstallationManager`, `pluginOperations`, `pluginCliCommands`.

**LSP (7 archivos):** LSP client completo con `LSPServerManager`, `LSPDiagnosticRegistry`, `passiveFeedback`.

**Tools Orchestration (4 archivos):**
- `toolOrchestration.ts` — Partitiona tool calls en batches concurrentes (read-only, max 10) vs seriales (write). Patron `partitionToolCalls` que detecta safety de concurrencia.
- `toolHooks.ts` — Pre/post tool hooks con hook interrupt capability.
- `StreamingToolExecutor.ts` — Ejecucion de tools con streaming.

**Voice (3 archivos):** Push-to-talk, native audio capture (cpal), STT streaming via Whisper, voice keyterms.

**VCR (vcr.ts):** Sistema de fixtures para testing — graba y reproduce llamadas API. Activado por `NODE_ENV=test` o `FORCE_VCR` (ant-only).

**preventSleep.ts:** macOS `caffeinate` con refcount y auto-restart cada 4 minutos.

**MagicDocs:** Documentos markdown con header `# MAGIC DOC: [title]` que se auto-actualizan en background via subagente forkeado.

**PromptSuggestion:** Sugiere prompts al usuario. Feature flag `tengu_chomp_inflection`. Incluye speculation (pre-generate antes de input).

**AwaySummary:** Genera resumen "while you were away" usando el modelo small/fast con los ultimos 30 mensajes.

**AgentSummary:** Resumen periodico cada 30s de sub-agentes en coordinator mode. 3-5 palabras en present tense.

**Diagnostics Tracking:** Integra con IDE via MCP para tracking de diagnosticos LSP (errores, warnings).

**Rate Limits (claudeAiLimits.ts):** Rate limit types: `five_hour`, `seven_day`, `seven_day_opus`, `seven_day_sonnet`, `overage`. Early warning con thresholds configurables.

---

## 2. HOOKS — React Hooks Internos

### 2.1 useCanUseTool (40KB, hook mas critico de permisos)

**Pipeline de decision de permisos:**
1. Check abort signal.
2. `hasPermissionsToUseTool()` — Evaluacion basada en rules (config, allowlists).
3. Si `allow` — log y resolve. Si Bash classifier, registra `yoloClassifierApproval`.
4. Si `deny` — log, registra auto-mode denial, notifica UI.
5. Si `ask`:
   a. **Coordinator mode:** `handleCoordinatorPermission()` espera checks automatizados.
   b. **Bash classifier (feature BASH_CLASSIFIER):** `tryClassifier()` intenta auto-approve via modelo.
   c. **Hooks:** `runHooks()` ejecuta PermissionRequest hooks.
   d. **Interactive:** `handleInteractivePermission()` — muestra dialog al usuario.
   e. **Swarm worker:** `handleSwarmWorkerPermission()`.

**Feature flags en permisos:**
- `TRANSCRIPT_CLASSIFIER` — Auto-mode basado en clasificador de transcripts.
- `BASH_CLASSIFIER` — Clasificador especifico para Bash commands.

### 2.2 useReplBridge (115KB, hook mas grande)

**Bridge bidireccional con Claude.ai:**
- WebSocket siempre-on cuando esta habilitado.
- Envia mensajes del REPL a claude.ai y recibe mensajes inbound.
- Max 3 intentos de init consecutivos antes de "fuse blown" (dato real: un cliente generaba 2,879 x 401/dia).
- Feature gated: `feature('BRIDGE_MODE')`.
- Soporta outbound-only mode.
- Mensajes inbound se inyectan via `queuedCommands`.

### 2.3 useTypeahead (212KB, archivo mas grande)

Autocompletado en el input. Por tamano, probablemente contiene logica compleja de file/path/command completion.

### 2.4 useVoiceIntegration (99KB)

Integracion completa de voice con push-to-talk, STT, y voice keyterms.

### 2.5 useInboxPoller (34KB)

**Polling de inbox para multi-agent:**
- Soporta permission requests/responses, shutdown requests, plan approval.
- Mode set requests para cambiar permission mode de teammates.
- Sandbox permission bridge.
- Integra con team file y leader permission bridge.

### 2.6 Hooks de Estado y Navegacion

- **useMainLoopModel** — Resuelve modelo activo. Subscribre a GrowthBook refresh para re-resolver cuando feature flags cambian.
- **useDynamicConfig** — Wrapper React para `getDynamicConfig_BLOCKS_ON_INIT`. Returns default initially, actualiza cuando config llega.
- **useMergedTools** — Ensambla tool pool: built-in + MCP tools, aplica deny rules y dedup.
- **useSessionBackgrounding** — Ctrl+B para background/foreground sesiones. Sincroniza mensajes de background tasks.
- **useSwarmInitialization** — Inicializa swarm features, maneja fresh spawn vs resumed sessions.
- **useDirectConnect** — SDK direct connect via WebSocket con DirectConnectSessionManager.
- **useScheduledTasks** — Wrapper de REPL para cron scheduler. Tareas disparadas como 'later' priority en command queue.

### 2.7 toolPermission/ (subdirectorio, 3+ archivos)

**PermissionContext** — Objeto frozen que encapsula todo el estado de una decision de permiso:
- `ResolveOnce<T>` — Patron atomico claim-and-resolve para evitar double-resolution en callbacks async.
- `PermissionQueueOps` — Interfaz generica para queue, desacoplada de React state.
- `buildAllow`/`buildDeny` — Builders tipados para decisions.
- `persistPermissions` — Persiste permission updates y actualiza AppState.
- `cancelAndAbort` — Reject con optional abort del controller.
- `tryClassifier` (feature BASH_CLASSIFIER) — Auto-approve via classifier.
- `runHooks` — Itera sobre PermissionRequest hooks, soporta allow/deny/interrupt.

### 2.8 Otros Hooks Notables

- **useArrowKeyHistory** (34KB) — Navegacion de historial con arrow keys.
- **useGlobalKeybindings** (31KB) — Keybindings globales del REPL.
- **useVirtualScroll** (35KB) — Virtual scrolling para output largo.
- **useSearchInput** (10KB) — Busqueda Ctrl+R style.
- **useTextInput** (17KB) — Input handler principal.
- **useVimInput** (9.7KB) — Vim keybindings.
- **fileSuggestions** (27KB) — Sugerencias de archivos.
- **useSwarmPermissionPoller** (9.6KB) — Polling de permisos en swarm mode.
- **useTasksV2** (8.8KB) — Sistema de tareas v2.
- **usePasteHandler** (10KB) — Manejo de paste con soporte de imagenes clipboard.

---

## 3. ORQUESTACION DEL FLUJO DE DATOS

### 3.1 Startup Sequence

```
main.tsx
  -> initializeAnalyticsSink() [attach sink, drain queue]
  -> initializeAnalyticsGates() [read Datadog gate from GrowthBook cache]
  -> GrowthBook init [remote eval, populate feature flags]
  -> OAuth check/refresh
  -> MCP connections init
  -> REPL mount [React Ink app]
```

### 3.2 Flujo Principal (Query Loop)

```
User Input
  -> useTextInput / useVoice
  -> API call (services/api/claude.ts) con streaming
  -> Model response con tool_use blocks
  -> toolOrchestration.ts: partitionToolCalls (concurrent vs serial)
    -> Per tool: useCanUseTool pipeline (rules -> classifier -> hooks -> UI)
    -> toolExecution.ts: execute tool
    -> toolHooks.ts: pre/post tool hooks
  -> If no more tool calls (stop):
    -> stopHooks:
      -> extractMemories (forked agent, background)
      -> autoDream (if gates pass)
    -> microCompact (if threshold reached)
  -> autoCompact (if token count > threshold)
  -> Render response
```

### 3.3 Patron Forked Agent (Shared Cache)

Patron central que usan AutoDream, ExtractMemories, SessionMemory, AgentSummary, MagicDocs, Compact:

```
runForkedAgent({
  promptMessages: [...],
  cacheSafeParams: createCacheSafeParams(context), // preserva cache key
  canUseTool: restrictedCanUseTool,
  querySource: 'auto_dream' | 'extract_memories' | ...,
  forkLabel: '...',
  skipTranscript: true,
  onMessage: progressWatcher,
})
```

El fork comparte el prompt cache del parent (same system prompt + tool definitions = same cache key) pero tiene su propio tool permission set restringido.

### 3.4 Feature Flag Control Flow

GrowthBook es el cerebro de control remoto:
- **Modelo:** `tengu_ant_model_override` (ant-only model override).
- **Analytics:** `tengu_log_datadog_events`, `tengu_1p_event_batch_config`, `tengu_event_sampling_config`.
- **Kill switches:** `tengu_frond_boric` (per-sink kill), `tengu_max_version_config` (force upgrade).
- **Features:** `tengu_onyx_plover` (autoDream), `tengu_chomp_inflection` (prompt suggestion).
- **Permisos:** Feature flags para BASH_CLASSIFIER, TRANSCRIPT_CLASSIFIER.
- **Build-time:** `feature('BRIDGE_MODE')`, `feature('TEAMMEM')`, `feature('KAIROS')`, `feature('PROACTIVE')` — dead code elimination via bun:bundle.

### 3.5 State Flow

```
GrowthBook (remote config)
  -> Analytics sinks (what to log, where)
  -> Feature gates (what features enabled)
  -> Model resolution (what model to use)
  
AppState (React zustand-like store)
  -> tools, permissions, tasks, bridge state
  -> foregroundedTaskId, messages, loading

postSamplingHooks (registered callbacks)
  -> extractMemories, autoDream, SessionMemory, MagicDocs
  
CleanupRegistry (process exit handlers)
  -> Datadog flush, caffeinate kill, bridge teardown
```

---

## 4. FEATURES OCULTOS / NOTABLES

1. **Codenames internos:** El proyecto se llama "Tengu" internamente (todos los eventos analytics tienen prefijo `tengu_`).

2. **ant-only paths:** `process.env.USER_TYPE === 'ant'` habilita diagnostics, overrides, internal logging, VCR. Es el modo para empleados de Anthropic.

3. **KAIROS mode:** Feature flag que habilita funcionalidad avanzada (cron scheduling, proactive mode, transcript-based dream). Gated con `feature('KAIROS')`.

4. **Bridge Mode (Remote Control):** WebSocket bridge bidireccional con claude.ai. Permite controlar Claude Code desde la web. Feature `BRIDGE_MODE`.

5. **Speculation:** Pre-generacion de prompt suggestions mientras el usuario escribe. Usa forked agent con abort.

6. **Session Memory vs AutoMemory vs Dream:** Tres capas de memoria: Session Memory (per-session markdown), AutoMemory (per-project persistent files), Dream (consolidation periodica de memories).

7. **Team Memory Sync:** Sync de memorias compartidas por equipo via API server, scoped por repo.

8. **MagicDocs:** Documentos que se auto-actualizan. Header `# MAGIC DOC:` trigger el background updater.

9. **Forked Agent pattern:** Subagentes que comparten prompt cache pero con tool restrictions. Usado por 6+ features.

10. **macOS caffeinate integration:** Prevent sleep con refcount y auto-restart.

---

Archivos clave analizados:
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/services/analytics/` (9 archivos)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/services/autoDream/` (4 archivos)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/services/extractMemories/` (2 archivos)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/services/compact/` (11 archivos)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/services/oauth/` (5 archivos)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/hooks/useCanUseTool.tsx` (40KB)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/hooks/useReplBridge.tsx` (115KB)
- `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/hooks/toolPermission/PermissionContext.ts`

---

