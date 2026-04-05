# SPEC_10_CONSTANTS_STATE_CLI_REMAINING — Extracted Analysis

I now have enough data to produce the complete analysis. Here is the full technical analysis:

---

## Analisis Tecnico Completo: Modulos Restantes de Claude Code

### 1. CONSTANTS (21 archivos)

**apiLimits.ts** -- Limites de la API de Anthropic para imagenes (5MB base64, 2000px max), PDFs (20MB raw, 100 paginas, extraccion a imagenes sobre 3MB), y media (100 items por request). Derivados de limites server-side con margen de seguridad client-side.

**betas.ts** -- Headers beta para features experimentales. Revela el sistema de feature flags: interleaved thinking, contexto 1M, web search, tool search (con headers distintos para 1P vs 3P), effort control, task budgets, fast mode, redact thinking, token-efficient tools, AFK mode, advisor tool. Bedrock tiene un subset restringido via `extraBodyParams`. Usa `bun:bundle` feature flags para dead code elimination.

**common.ts** -- Utilidades de fecha con memoizacion para estabilidad de prompt cache. `getSessionStartDate` se calcula una vez por sesion para no invalidar el cache a medianoche. `getLocalMonthYear` cambia mensualmente, usado en tool prompts.

**cyberRiskInstruction.ts** -- Instruccion de seguridad cibernetica propiedad del equipo Safeguards. Define el boundary entre asistencia de seguridad defensiva aceptable y actividades potencialmente danosas. Requiere revision explicita del equipo para modificaciones.

**errorIds.ts** -- IDs numericos ofuscados para tracking de errores en produccion. Incrementales (siguiente: 346). Solo exporta constantes individuales para optimal dead code elimination.

**figures.ts** -- Glyphs Unicode para la UI terminal: indicadores de esfuerzo (low/medium/high/max), indicadores de bridge, estado de review (diamantes), forks, scroll, seleccion, blockquotes. Diferencia plataformas (darwin vs otros) para ciertos caracteres.

**files.ts** -- Deteccion de archivos binarios por extension (110+ extensiones) y por contenido (heuristica de bytes no-imprimibles >10% en primeros 8KB). Usado por herramientas de lectura/edicion para evitar operaciones de texto en binarios.

**github-app.ts** -- Templates completos de GitHub Actions workflows para integracion Claude Code: workflow de respuesta a @claude en PRs/issues, y workflow de code review automatico con plugins. Incluye PR title y body con documentacion de setup.

**keys.ts** -- GrowthBook client keys para feature flags (separados para builds internos ant vs externos, con override para dev). Lazy-evaluated para capturar variables de entorno post-carga.

**messages.ts** -- Una sola constante: `NO_CONTENT_MESSAGE = '(no content)'`.

**oauth.ts** -- Configuracion OAuth completa: 3 environments (prod/staging/local), scopes para Claude.ai (inference, profile, sessions, MCP, file upload) y Console (API key creation). Client ID Metadata Document URL para MCP OAuth (SEP-991). Custom OAuth URLs solo permitidas para FedStart/PubSec (allowlist de dominios). Override de CLIENT_ID via env var (para Xcode integration).

**outputStyles.ts** -- Sistema de estilos de output personalizables. 3 modos built-in: default, Explanatory (insights educativos con formato especial), Learning (pausa y pide al usuario escribir codigo, con formato "Learn by Doing"). Soporta estilos custom via markdown, plugins (con `forceForPlugin`), y directorios. Prioridad: built-in < plugin < user < project < managed.

**product.ts** -- URLs del producto y deteccion de entornos de sesion remota (staging, local, prod) por prefijos en session ID (`_staging_`, `_local_`). Shim de compatibilidad `cse_` -> `session_` para URLs de frontend mientras el servidor migra tags.

**prompts.ts** -- **EL ARCHIVO MAS IMPORTANTE**: System prompt completo de Claude Code (~600 lineas). Arquitectura:
- Seccion estatica (cacheable globalmente) antes de `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`
- Seccion dinamica (por sesion) despues del boundary
- Secciones: intro, system, doing tasks, actions (reversibilidad), using tools, tone/style, output efficiency
- Feature: "numeric length anchors" (ant-only) limita text entre tool calls a 25 palabras
- Feature: Verification Agent (adversarial verification obligatoria para cambios no-triviales)
- Feature: Scratchpad (directorio de escritura libre sin permisos)
- Feature: Skill discovery (surfacing automatico de skills relevantes por turno)
- Feature: Token budget ("+500k", "spend 2M tokens")
- Feature: REPL mode (herramientas se ejecutan via scripts, no directamente)
- Diferenciacion ant vs external en comentarios, thoroughness, false-claims mitigation
- `FRONTIER_MODEL_NAME = 'Claude Opus 4.6'`

**spinnerVerbs.ts** -- 200+ verbos humoristicos para el spinner de carga ("Clauding", "Flibbertigibbeting", "Prestidigitating"). Soporta reemplazo o extension via settings.

**systemPromptSections.ts** -- Sistema de secciones de system prompt memoizadas. Patron: computar una vez, cachear hasta /clear o /compact. `DANGEROUS_uncachedSystemPromptSection` para secciones volatiles (MCP instructions) que invalidan cache cada turno. Limpiar tambien resetea beta header latches.

**system.ts** -- Prefijos de system prompt para diferentes contextos (CLI default, Agent SDK con preset Claude Code, Agent SDK generico). Attribution header con fingerprint de version, entrypoint, workload routing hint, y placeholder `cch=00000` para native client attestation (Bun HTTP stack lo sobreescribe con hash computado).

**toolLimits.ts** -- Limites de tamano de resultados de herramientas: 50K chars por herramienta, 100K tokens max, 200K chars agregados por mensaje (parallel tools), 50 chars para summaries. Per-message budget previene N herramientas paralelas produciendo 400K en un turno.

**tools.ts** -- Listas de herramientas permitidas/prohibidas por tipo de agente: ALL_AGENT_DISALLOWED (plan mode, ask user, task stop), ASYNC_AGENT_ALLOWED (file ops, search, web, shell, notebooks, worktrees), IN_PROCESS_TEAMMATE_ALLOWED (task management, messaging, cron), COORDINATOR_MODE_ALLOWED (solo agent, stop, send message, synthetic output). Ant-only: nested agents habilitados.

**turnCompletionVerbs.ts** -- 8 verbos en pasado para mensajes de completitud de turno ("Baked for 5s", "Cogitated for 3s").

**xml.ts** -- Tags XML para metadata de skills, terminal I/O, notificaciones de tareas, ultraplan, teammates, canales, cross-session, fork boilerplate. Patterns de argumentos comunes para slash commands (help args, info args).

---

### 2. KEYBINDINGS (14 archivos)

Sistema completo de keybindings customizables con arquitectura de 5 capas:

**defaultBindings.ts** -- 18 contextos con 100+ bindings por defecto. Deteccion de platform-specific: Windows VT mode (necesita Node 24.2+ o Bun 1.2.23+), image paste (alt+v en Windows, ctrl+v en otros), mode cycle (shift+tab o meta+m). Feature-gated: voice mode (space = pushToTalk), quick search (ctrl+shift+f), terminal panel, message actions, kairos brief.

**parser.ts** -- Parser de keystrokes con aliases de modifiers (ctrl/control, alt/opt/option/meta, cmd/command/super/win). Soporta chords ("ctrl+k ctrl+s"). Display platform-aware (opt en macOS, alt en otros).

**match.ts** -- Matching de keystrokes con normalizacion de modifiers. QUIRK: Ink setea key.meta=true para escape (legacy terminal). Alt y meta son equivalentes (limitacion terminal). Super (cmd) es distinto y solo llega via kitty protocol.

**resolver.ts** -- Resolucion de keybindings con soporte de chords. Last-wins para overrides de usuario. Null-override shadows bindings (ctrl+x ctrl+k nullificado previene chord-wait en ctrl+x). Chord state machine: none -> chord_started -> match/cancelled.

**loadUserBindings.ts** -- Carga de keybindings de usuario desde `~/.claude/keybindings.json` con hot-reload via chokidar. Feature-gated via GrowthBook (`tengu_keybinding_customization_release`). File stability threshold de 500ms. Telemetria: log custom keybindings loaded max 1 vez/dia.

**schema.ts** -- Zod schema con 17 contextos y 70+ acciones. Soporta action strings, `command:xxx` bindings (ejecuta slash commands), y null (unbind). Valida que command bindings esten en contexto Chat.

**validate.ts** -- Validacion multi-capa: duplicados dentro del mismo contexto (JSON silently drops), shortcuts reservados (ctrl+c, ctrl+d, ctrl+m non-rebindable; ctrl+z, ctrl+\ terminal-intercepted; macOS cmd+q/w/tab/space OS-intercepted), voice pushToTalk con letras bare (warn: prints into input).

**template.ts** -- Generador de template para keybindings.json con todos los defaults (filtrado de reserved shortcuts).

**useKeybinding.ts / useKeybindings.ts** -- React hooks que registran handlers con el contexto de keybindings. Soporta chords automaticamente. `stopImmediatePropagation()` previene otros handlers. Handler returning `false` = event propagates (para fall-through patterns).

**useShortcutDisplay.ts / shortcutFormat.ts** -- Display de shortcuts configurados en React y no-React contexts. Fallback con telemetria de migracion.

**KeybindingContext.tsx / KeybindingProviderSetup.tsx** -- Provider React con compilador React optimizado (source maps embedded en base64).

---

### 3. MIGRATIONS (11 archivos)

Sistema de migraciones de configuracion. Patron comun: leer settings source especifico (no merged), escribir, marcar completado. Todas idempotentes:

- **migrateAutoUpdatesToSettings**: autoUpdates false -> `DISABLE_AUTOUPDATER=1` en env settings
- **migrateBypassPermissionsAcceptedToSettings**: bypass permissions -> skipDangerousModePermissionPrompt
- **migrateEnableAllProjectMcpServersToSettings**: MCP approval fields de project config a local settings (merge sin duplicados)
- **migrateFennecToOpus**: fennec-latest -> opus, fennec-fast-latest -> opus[1m] + fast mode (ant-only)
- **migrateLegacyOpusToCurrent**: Opus 4.0/4.1 strings explicitos -> 'opus' alias (1P only)
- **migrateOpusToOpus1m**: 'opus' -> 'opus[1m]' para Max/Team Premium users (merge Opus 1M)
- **migrateSonnet1mToSonnet45**: 'sonnet[1m]' -> 'sonnet-4-5-20250929[1m]' (pin antes de Sonnet 4.6)
- **migrateSonnet45ToSonnet46**: Sonnet 4.5 explicito -> 'sonnet' alias (ahora resuelve a 4.6)
- **migrateReplBridgeEnabledToRemoteControlAtStartup**: rename de config key
- **resetAutoModeOptInForDefaultOffer**: re-surface dialog para opcion "make it my default mode"
- **resetProToOpusDefault**: Pro subscribers -> Opus 4.5 default con timestamp de notificacion

---

### 4. CONTEXT (9 archivos)

React Context providers con compilador React (memo cache sentinels):

- **fpsMetrics.tsx**: Provider de metricas FPS (getter lazy, no re-render en writes)
- **mailbox.tsx**: Mailbox singleton (useMemo[], singleton pattern)
- **modalContext.tsx**: Contexto modal con dimensiones y scroll ref. useIsInsideModal, useModalOrTerminalSize (fallback a terminal size)
- **notifications.tsx**: Sistema de notificaciones con cola (demasiado grande para leer completo)
- **overlayContext.tsx**: Overlay management (demasiado grande para leer completo)
- **promptOverlayContext.tsx**: Portal para contenido floating sobre el prompt. Split data/setter para evitar re-renders en writers. Dos canales: suggestions (PromptInputFooter) y dialog (AutoModeOptInDialog)
- **QueuedMessageContext.tsx**: Contexto de mensajes en cola con padding configurable (brief layout skip padding)
- **stats.tsx**: Estadisticas de sesion (demasiado grande para leer completo)
- **voice.tsx**: Estado de voz con external store pattern (useSyncExternalStore). Estados: idle/recording/processing. Audio levels, warmup, interim transcript. setState sincrono para VoiceKeybindingHandler.

---

### 5. STATE (6 archivos)

**store.ts** -- Implementacion minima de store reactivo (35 lineas). Pattern: `getState/setState/subscribe` con `Object.is` equality check y onChange callback. Compatible con `useSyncExternalStore`.

**AppStateStore.ts** -- **ESTADO COMPLETO DE LA APLICACION** (~570 campos). Highlights:
- Speculation state machine (idle/active con pipelining)
- REPL bridge state (11 campos: enabled, connected, session active, reconnecting, URLs, IDs)
- Computer use MCP state (app allowlist, clipboard grants, screenshot dims, hidden apps)
- REPL VM context (vm.Context persistente entre llamadas)
- Team context (swarm members con tmux sessions)
- Ultraplan state (launching, session URL, pending choice, launch dialog)
- Plugin system (enabled/disabled, installation status, errors, needs refresh)
- Tungsten tmux panel state (visibility, auto-hide, last command)
- WebBrowser (codename "bagel") state
- Chicago MCP session state (computer use)

**AppState.tsx** -- Provider React para AppState (demasiado grande para leer completo).

**onChangeAppState.ts** -- Side effects hook: sincroniza permission mode con CCR via notifySessionMetadataChanged, persiste model changes a settings, persiste expandedView/verbose/tungsten panel a global config, limpia auth caches cuando settings cambian, re-aplica environment variables.

**selectors.ts** -- Selectors puros: getViewedTeammateTask (valida taskId, type guard), getActiveAgentForInput (routing discriminado: leader/viewed/named_agent).

**teammateViewHelpers.ts** -- Helpers para vista de teammates: enter (retain + clear evictAfter, switch releases previous), exit (release back to stub, grace period eviction), stopOrDismiss (running -> abort, terminal -> immediate evict).

---

### 6. VIM (5 archivos)

Implementacion completa de vim mode para el input de texto:

**types.ts** -- State machine con diagram ASCII. VimState = INSERT (tracks insertedText) | NORMAL (CommandState). 11 estados de comando: idle, count, operator, operatorCount, operatorFind, operatorTextObj, find, g, operatorG, replace, indent. PersistentState para dot-repeat y registros. MAX_VIM_COUNT = 10000.

**motions.ts** -- Resoluciones puras: h/l/j/k, w/b/e/W/B/E (word motions), 0/^/$ (line positions), G (last line), gj/gk (visual lines). Clasificacion: inclusive (e/E/$), linewise (j/k/G/gg).

**operators.ts** -- Operadores completos: delete/change/yank con motion, find, text object, line op (dd/cc/yy). Comandos: x (delete char), r (replace), ~ (toggle case), J (join lines), p/P (paste linewise/characterwise), >/< (indent), o/O (open line). Image ref snap-out para chips [Image #N].

**textObjects.ts** -- Text objects: w/W (word), brackets (()[]{}), quotes (""''``), angle brackets (<>). Matching balanceado para brackets, pair-quote para quotes.

**transitions.ts** -- Tabla de transiciones completa. Dispatch por estado con shared input handling (handleNormalInput para idle/count, handleOperatorInput para operator states). Soporte para dot-repeat (.), find-repeat (;/,), undo (u).

---

### 7. SCREENS (3 archivos)

**Doctor.tsx** -- Pantalla de diagnostico del sistema. Imports revelan checks: keybinding warnings, MCP parsing warnings, sandbox doctor, settings errors, output size limits, PID locks, context warnings, auto-updater dist tags.

**REPL.tsx** -- La pantalla principal (~50 imports). Core loop: query guard, message queue, permission system, cost tracking, token budgets, speculation, session hooks, swarm worker permissions, REPL bridge, teammate injection, worktree management.

**ResumeConversation.tsx** -- Resume de conversaciones previas: busqueda agentica, cross-project resume, file history restore, agent context restore, worktree restore, content replacement records.

---

### 8. SERVER (3 archivos)

**types.ts** -- Tipos para servidor direct-connect: session states (starting/running/detached/stopping/stopped), session index persistido a disco para resume cross-restart.

**createDirectConnectSession.ts** -- POST a `${serverUrl}/sessions` con validacion Zod. Soporta `dangerously_skip_permissions`. Retorna config con wsUrl.

**directConnectManager.ts** -- WebSocket session manager completo. Protocolo: SDK messages (assistant, result, system), control requests (can_use_tool permissions), control responses, interrupts. Filtra: keep_alive, streamlined_text, post_turn_summary.

---

### 9. NATIVE-TS (4 archivos)

Reimplementaciones en TypeScript puro de modulos nativos:

**color-diff/index.ts** -- Port de color diffing nativo (demasiado grande para leer completo).

**file-index/index.ts** -- Port de nucleo (Rust NAPI) para fuzzy file search. Implementa fzf-v2 scoring con bonuses (boundary, camelCase, consecutive, first char) y penalties (gap start, gap extension). Bitmap rejection O(1) por path (a-z character bits). Async build con yield cada ~4ms. Top-K results con binary insertion sort. "test" paths penalizados 1.05x.

**yoga-layout/index.ts** -- Port completo de yoga-layout (Meta's flexbox engine) en TypeScript puro. Soporta: flex-direction, flex-grow/shrink/basis, align/justify, margin/padding/border/gap, position relative/absolute, display flex/none/contents, measure functions, flex-wrap, baseline alignment. NO implementa: aspect-ratio, RTL.

---

### 10. TYPES (11 archivos)

**command.ts** -- Tipos de comandos: PromptCommand (con getPromptForCommand, model override, hooks, fork context, paths glob), LocalCommand (lazy-loaded), LocalJSXCommand (lazy-loaded con React). CommandAvailability: 'claude-ai' | 'console'. CommandBase con 20+ propiedades.

**ids.ts** -- Branded types para SessionId y AgentId. Pattern `a` + optional label + 16 hex chars para AgentId.

**permissions.ts** -- Sistema de permisos completo (~440 lineas): 5 external modes + 2 internal (auto, bubble). Permission rules con source tracking (8 sources). Permission decisions: allow (con updatedInput), ask (con pendingClassifierCheck), deny. Classifier results con 2-stage (fast/thinking). Risk levels (LOW/MEDIUM/HIGH). Tool permission context con rules by source.

**plugin.ts** -- Plugin system: builtin plugins, marketplace repos con SHA pinning, loaded plugins con 15+ campos. Components: commands, agents, skills, hooks, output-styles.

**textInputTypes.ts** -- Tipos del input de texto: BaseTextInputProps (30+ propiedades), VimTextInputProps, InlineGhostText, PromptInputMode (bash/prompt/orphaned-permission/task-notification), QueuedCommand (35+ propiedades con origin tracking, workload tags, agent routing), QueuePriority (now/next/later).

**hooks.ts / logs.ts** -- Tipos complementarios para hooks y logs.

**generated/** -- Protobuf types para analytics events (Claude Code internal, auth, GrowthBook experiments).

---

### 11. UPSTREAMPROXY (2 archivos)

**relay.ts** -- CONNECT-over-WebSocket relay para CCR upstream proxy. Hand-encoded protobuf (UpstreamProxyChunk, campo 1 bytes). Dual runtime: Bun (sock.write partial writes + drain queue) y Node (net.createServer + ws package). MAX_CHUNK_BYTES = 512KB. Keepalive cada 30s. CONNECT state machine: accumulate headers -> parse CONNECT -> open WS tunnel -> forward bytes.

**upstreamproxy.ts** -- Container-side wiring: lee session token de /run/ccr/session_token, setea prctl(PR_SET_DUMPABLE, 0) via Bun FFI para bloquear ptrace, descarga CA cert del proxy, inicia relay local, unlink token file, exporta HTTPS_PROXY/SSL_CERT_FILE para subprocesos. NO_PROXY incluye Anthropic API (MITM breaks non-Bun runtimes), registries, localhost. Fail-open design.

---

### 12. SCHEMAS (1 archivo)

**hooks.ts** -- Schemas Zod para los 4 tipos de hooks: command (bash con shell/timeout/async/asyncRewake), prompt (LLM evaluation con $ARGUMENTS), http (POST con headers interpolados y allowedEnvVars), agent (agentic verifier). HookMatcher con patterns string. Extraido de settings/types.ts para romper ciclos de importacion.

---

### 13. OUTPUT STYLES (1 archivo)

**loadOutputStylesDir.ts** -- Carga markdown de `.claude/output-styles/` con frontmatter (name, description, keep-coding-instructions). Sources por prioridad: project > user > managed. Memoizado.

---

### 14. MORERIGHT (1 archivo)

**useMoreRight.tsx** -- Stub para builds externos. El hook real es interno (ant-only). Interfaz: onBeforeQuery, onTurnComplete, render. No-op en external.

---

### 15. ASSISTANT (1 archivo)

**sessionHistory.ts** -- Paginacion de historial de sesiones remotas via API de Anthropic. fetchLatestEvents (anchor_to_latest), fetchOlderEvents (before_id cursor). Page size: 100. Timeout: 15s. Auth: OAuth + org-UUID + beta header `ccr-byoc-2025-07-29`.

---

### 16. CLI (19 archivos)

**exit.ts** -- Helpers cliError/cliOk con `return undefined as never` (tests spy on process.exit y lo dejan retornar).

**ndjsonSafeStringify.ts** -- Escapa U+2028/U+2029 en JSON para transports line-based. Previene truncamiento silencioso de mensajes.

**structuredIO.ts** -- IO bidireccional estructurado para SDK mode. Parsea stdin NDJSON, maneja control requests (permission, elicitation), hook callbacks, permission updates.

**remoteIO.ts** -- Extiende StructuredIO con WebSocket transport, session tracking, CCR client integration, keepalive timer.

**print.ts** -- Sistema de IO principal para el CLI interactivo. Importa: settings sync, remote managed settings, tool pool assembly, agent definitions, message queue, session state, streamlined transform.

**update.ts** -- Auto-updater: check version contra dist tags, deteccion de instalaciones multiples, diagnostico, installation via native/npm/local installer.

**handlers/** -- Subcommand handlers: agents (configuracion de agentes), auth (autenticacion), autoMode (modo autonomo), mcp.tsx (servidores MCP), plugins (sistema de plugins), util.tsx (utilidades compartidas).

**transports/** -- Layer de transporte: CCRClient (comunicacion con CCR), HybridTransport (WebSocket + SSE fallback), SerialBatchEventUploader (batch events), SSETransport (Server-Sent Events), WebSocketTransport, WorkerStateUploader, transportUtils.

---

### Patterns de Diseno Principales

1. **Feature flags via `bun:bundle`**: Dead code elimination en compile time. Features como KAIROS, PROACTIVE, VOICE_MODE, QUICK_SEARCH, MESSAGE_ACTIONS, WORKFLOW_SCRIPTS, VERIFICATION_AGENT.

2. **Lazy schemas (`lazySchema`)**: Zod schemas envueltos en factories para evitar circular dependencies y reducir startup time.

3. **Compilador React**: Todos los .tsx pasan por React compiler (patron `_c(n)` + memo cache sentinels).

4. **External store pattern**: `createStore` + `useSyncExternalStore` para estado que no necesita Context.

5. **Ant vs External splits**: `process.env.USER_TYPE === 'ant'` condiciona features, prompts, verbosity, y migraciones. External builds usan stubs.

6. **Prompt cache optimization**: Boundary marker `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` separa contenido estatico (cacheable globalmente) de dinamico. Secciones memoizadas, dates memoizados, monthly granularity para tool prompts.

7. **Branded types**: SessionId y AgentId previenen mezcla accidental en compile time.

8. **Fail-open design**: Upstreamproxy, keybinding loading, migration failures -- todos degrade gracefully sin romper funcionalidad core.

---

