# SPEC_08_UTILS_COMPONENTS_INK — Extracted Analysis

I now have a comprehensive understanding of the codebase. Let me compile the technical analysis.

---

# Analisis Tecnico: Claude Code -- Utils, Components e Ink Framework

## 1. UTILS: Sistema de Utilidades (329 archivos)

### 1.1 Settings System (utils/settings/)

**Arquitectura de capas de configuracion con merge jerarquico:**

- **6 fuentes de settings** con prioridad ascendente: `policySettings` (managed/enterprise) -> `userSettings` (~/.claude/) -> `projectSettings` (.claude/settings.json) -> `localSettings` -> `flagSettings` (CLI flags) -> `session` (runtime)
- Validacion via **Zod v4 schemas** (`SettingsSchema`). El schema soporta backward-compatible changes -- `.passthrough()` preserva campos desconocidos, `.optional()` para todo campo nuevo.
- **Managed settings** soportan drop-in directory (`managed-settings.d/*.json`) al estilo systemd -- archivos ordenados alfabeticamente se mergean con precedencia ascendente.
- **MDM/HKCU** para enterprise deployment en Windows (settings/mdm/).
- Settings se cachean por sesion (`settingsCache.ts`) con invalidacion por fuente. El parser clona antes de retornar para evitar mutacion del cache.
- **Plugin-only policy** (`pluginOnlyPolicy.ts`): puede lockear surfaces como skills, agents, hooks a solo-plugins.

**Tipo ProjectConfig** (~137 campos): allowed tools, MCP servers, trust dialog state, worktree sessions, remote-control spawn mode, etc.

**Tipo GlobalConfig** (~200+ campos): contiene absolutamente todo el estado persistente del usuario: OAuth accounts, theme, editor mode, feature flags caches (statsig gates, GrowthBook features), migration tracking, companion state, skill usage, IDE prefs, etc.

### 1.2 CLAUDE.md / Memory System (claudemd.ts)

**Loading order (prioridad ascendente):**
1. Managed memory (`/etc/claude-code/CLAUDE.md`)
2. User memory (`~/.claude/CLAUDE.md`)
3. Project memory (CLAUDE.md, .claude/CLAUDE.md, .claude/rules/*.md en project roots)
4. Local memory (CLAUDE.local.md)

**Descubrimiento:** Traversa de CWD hasta root, archivos mas cercanos al CWD tienen mayor prioridad.

**@include directive:** Permite `@path`, `@./relative`, `@~/home`, `@/absolute`. Solo text files (~100 extensiones permitidas en whitelist). Circular references prevenidas. Non-existent files silently ignored.

**Frontmatter paths:** Los archivos en `.claude/rules/` pueden tener frontmatter YAML con `paths:` -- glob patterns que restringen a que archivos aplica la regla. `picomatch` para matching.

**HTML comment stripping:** Usa `marked` lexer para strip block-level HTML comments (preserva comments en code blocks e inline).

**MAX_MEMORY_CHARACTER_COUNT:** 40,000 chars recomendado.

### 1.3 Token Counting y Context Budget (analyzeContext.ts)

**Estrategia dual de token counting:**
1. API primaria: `countMessagesTokensWithAPI` -- llama la API de token counting de Anthropic
2. Fallback: `countTokensViaHaikuFallback` -- usa Haiku como proxy de conteo
3. Ultimo recurso: `roughTokenCountEstimation` -- heuristica basada en chars

**Context budget categories:** System prompt, CLAUDE.md files, built-in tools, MCP tools, deferred tools, agents, slash commands, skills, messages, auto-compact buffer.

**Tool definition overhead:** 500 tokens fijos que la API agrega por llamada con tools. Se descuenta al calcular tokens por tool individual.

**Deferred tools:** Se diferencian always-loaded vs deferred (lazy). Solo los deferred que el modelo ha usado se cuentan como consumidos. Los demas se reportan separados como "deferred tokens".

### 1.4 Tool Search System (toolSearch.ts)

**Tres modos:** `tst` (always defer), `tst-auto` (defer si excede threshold), `standard` (todo inline).

**Auto threshold:** 10% del context window por defecto. Configurable via `ENABLE_TOOL_SEARCH=auto:N`.

**Cuando MCP tool definitions exceden el threshold, se envian con `defer_loading: true` y se descubren via ToolSearchTool** en runtime. Kill switch: `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS`.

### 1.5 Messages System (messages.ts -- 5512 lineas)

**El archivo mas grande de utils.** Central para:
- Creacion de mensajes (user, assistant, system, progress, attachment, interruption)
- Normalizacion de mensajes para la API
- Tag extraction (XML tags en mensajes)
- Permission denial messages con workaround guidance
- Memory correction hints (adjuntados post-rejection cuando auto-memory esta activo)
- Classifier denial detection
- Short message ID derivation (UUID -> 6-char base36)

**Pattern notable:** `SYNTHETIC_TOOL_RESULT_PLACEHOLDER` -- marcador para tool_use sin tool_result matching. Exportado para que el HFI pipeline pueda rechazar payloads que lo contengan (evita poisoning de training data).

### 1.6 Session Storage (sessionStorage.ts -- 5105 lineas)

**Formato:** JSONL por sesion. Cada linea es un `Entry` (transcript message, content replacement, file history snapshot, etc.).

**parentUuid chain:** Los mensajes forman una cadena linked-list. Progress messages NO participan en la cadena (son efimeros). Legacy progress entries se bridgean al cargar.

**Limites de seguridad:**
- `MAX_TRANSCRIPT_READ_BYTES`: 50MB (previene OOM)
- `MAX_TOMBSTONE_REWRITE_BYTES`: 50MB
- Subagent transcripts en subdirectorio: `{sessionId}/subagents/agent-{agentId}.jsonl`

**Metadata sidecar:** Cada agente tiene `.meta.json` con agentType, worktreePath, description.

### 1.7 Hooks System (hooks.ts -- 5022 lineas)

**Lifecycle hooks (shell commands definidos por usuario):**
- SessionStart, SessionEnd, PreToolUse, PostToolUse, Stop, Notification, PreCompact, PostCompact, SubagentStart/Stop, TaskCreated/Completed, ConfigChange, CwdChanged, FileChanged, InstructionsLoaded, UserPromptSubmit, PermissionRequest, Elicitation, Setup

**Timeouts:** 10min para tool hooks, 1.5s para SessionEnd (configurable via env).

**Seguridad:** Todos los hooks requieren workspace trust. Defense-in-depth -- incluso si el flow normal ejecutaria despues del trust dialog, se verifica explicitamente.

**Async hooks:** Pueden ejecutar en background via `registerPendingAsyncHook`. Los hooks con `asyncRewake: true` bypasan el registry y en completion (exit code 2) inyectan un task-notification para despertar al modelo.

**Multi-source:** Hooks vienen de settings files, plugins, skill matchers, session-level function hooks.

### 1.8 Config System (config.ts -- 1817 lineas)

**Re-entrancy guard:** `insideGetConfig` flag previene `getConfig -> logEvent -> getGlobalConfig -> getConfig` recursion infinita cuando el config file esta corrupto.

**Lockfile-based writes:** Usa `lockfile.js` para escrituras atomicas. `saveGlobalConfig` con lock + re-read.

**Feature flags caching:** GrowthBook features y statsig gates cacheados en GlobalConfig. Se refrescan en background.

**Migration system:** `migrationVersion` en config. Cuando equals `CURRENT_MIGRATION_VERSION`, skip all sync migrations al startup.

### 1.9 Permission System (utils/permissions/)

**Tres comportamientos:** `allow`, `deny`, `ask`.

**Rule format:** `ToolName` o `ToolName(content)` con escaped parentheses. Legacy tool names normalizados via alias map (Task -> AgentTool, etc.).

**Rule sources (prioridad):** Settings sources + cliArg + command + session.

**Classifier system:** Para auto-mode (YOLO), un classifier (LLM-based) evalua safety de acciones bash. Denial tracking con fallback-to-prompting despues de N denials. Cooldown de 30min para fail-closed.

**Sandbox integration:** `shouldUseSandbox` determina si bash corre en sandbox. Override con `sandboxOverride` reason en permission.

### 1.10 Fast Mode (fastMode.ts)

**Fast mode = Opus 4.6 acceleration.** Requiere:
- 1st party API (no Bedrock/Vertex)
- Paid subscription
- Extra usage billing enabled
- Not SDK (unless explicit --settings)

**Runtime state machine:** `active` -> `cooldown` (con `resetAt` timestamp y `reason: rate_limit | overloaded`) -> back to `active`.

**Cooldown signals:** Sistema de pub-sub para notificar UI de cooldown events.

### 1.11 Otros Utils Notables

- **crypto.ts**: Re-export indirecto de `randomUUID` con workaround para bun bytecode compilation bug
- **theme.ts**: 6 themes (dark/light + daltonized + ANSI variants). ~90 semantic color tokens por theme con shimmer variants para animacion
- **debug.ts**: Debug logging con filtro por pattern (`--debug=pattern`), niveles (verbose/debug/info/warn/error), buffered writer, archivo rotativo con symlink "latest"
- **collapseReadSearch.ts**: Collapsing de operaciones read/search/list/bash consecutivas en grupos UI. Deteccion de git operations, memory writes, MCP tools
- **completionCache.ts**: Shell completion generation y caching para zsh/bash/fish

---

## 2. COMPONENTS: Arquitectura UI (144 archivos)

### 2.1 App Root (App.tsx)

**Arbol de providers minimalista:**
```
FpsMetricsProvider
  StatsProvider
    AppStateProvider (con onChangeAppState callback)
      {children}
```

**React Compiler activado:** Los componentes usan `_c()` del `react/compiler-runtime` para memoizacion automatica. Esto es el output compilado de React Compiler -- cada prop se trackea individualmente para granular cache invalidation.

### 2.2 Message Rendering Pipeline

**Message.tsx (626 lineas):** Switch central por `message.type`:
- `attachment` -> AttachmentMessage
- `assistant` -> Itera sobre content blocks, cada uno dispatched a:
  - `text` -> AssistantTextMessage
  - `thinking` -> AssistantThinkingMessage  
  - `redacted_thinking` -> AssistantRedactedThinkingMessage
  - `tool_use` -> AssistantToolUseMessage
  - AdvisorBlock -> AdvisorMessage
  - ConnectorTextBlock -> handling especial
- `user` -> Itera content blocks: UserTextMessage, UserImageMessage, UserToolResultMessage
- `system` -> SystemTextMessage, CompactBoundaryMessage
- `grouped_tool_use` -> GroupedToolUseContent
- `collapsed_read_search` -> CollapsedReadSearchContent

**OffscreenFreeze:** Componente wrapper que previene re-renders de subtrees fuera de viewport.

### 2.3 Messages.tsx (833 lineas)

**Processing pipeline antes de render:**
1. `normalizeMessages` -- canonicaliza message format
2. `getMessagesAfterCompactBoundary` -- filtra pre-compaction
3. `filterForBriefTool` -- en brief-only mode, solo muestra Brief tool calls
4. `dropTextInBriefTurns` -- drop text redundante cuando Brief fue usado
5. `collapseReadSearchGroups` -- colapsa reads/searches consecutivos
6. `collapseHookSummaries` -- colapsa hook outputs
7. `collapseTeammateShutdowns` -- colapsa shutdown messages
8. `collapseBackgroundBashNotifications` -- colapsa bash notifications
9. `applyGrouping` -- agrupa tool_use consecutivos
10. `reorderMessagesInUI` -- reordena para display

**LogoHeader memoizado con React.memo:** Performance critica -- si se invalida, cascadea dirty a todos los MessageRow siguientes (150K+ writes/frame en sesiones largas).

### 2.4 VirtualMessageList.tsx (1081 lineas)

**Virtualizacion para fullscreen mode.** Basado en `useVirtualScroll` hook custom.

**Sticky prompt tracking:** Detecta el ultimo prompt visible del usuario y lo muestra como header sticky (con click-to-scroll). WeakMap cache para evitar recomputo.

**Search integration:**
- `JumpHandle` imperativo: `jumpToIndex`, `setSearchQuery`, `nextMatch`, `prevMatch`
- `warmSearchIndex` pre-computa search text para todo mensaje
- Position-based highlighting via `scanElement` -> `MatchPosition[]`

### 2.5 Permission Request System (components/permissions/)

**Tool-specific permission dialogs** mapeados via `permissionComponentForTool()`:
- BashPermissionRequest
- FileEditPermissionRequest
- FileWritePermissionRequest
- FilesystemPermissionRequest (Glob/Grep/Read)
- NotebookEditPermissionRequest
- PowerShellPermissionRequest
- SkillPermissionRequest
- WebFetchPermissionRequest
- AskUserQuestionPermissionRequest
- Enter/ExitPlanModePermissionRequest
- FallbackPermissionRequest (default)

**ToolUseConfirm type:** Contiene tool instance, input, permission result, classifier state, y callbacks para allow/reject con permission updates y feedback.

**Sticky footer para fullscreen:** `setStickyFooter` callback para plans largos donde las opciones de respuesta deben permanecer visibles durante scroll.

### 2.6 PromptInput (PromptInput.tsx)

**Componente masivo (~150 imports).** Integra:
- Vim mode (VimTextInput) y normal mode (TextInput)
- History search (arrow keys, Ctrl+R)
- Image paste detection y processing
- Slash command suggestions, @mentions, Slack channels
- Fast mode toggle, thinking toggle
- Model picker, theme picker
- Teams dialog, background tasks dialog
- Bridge dialog (remote control)
- Auto-mode opt-in
- Queued commands display
- Prompt suggestion/speculation
- IDE at-mentions

### 2.7 Design System (components/design-system/)

Color system (`color.ts`), Divider, ThemedText con hover color context.

### 2.8 Otros Componentes Notables

- **Stats.tsx (1227 lineas):** Dashboard detallado de context usage, token breakdown
- **LogSelector.tsx (1574 lineas):** Session browser/selector
- **ScrollKeybindingHandler.tsx (1011 lineas):** Keyboard-driven scrolling para fullscreen
- **FullscreenLayout.tsx (636 lineas):** Alt-screen layout con scroll chrome
- **Spinner.tsx (561 lineas):** Animated spinner con shimmer effects
- **ContextVisualization.tsx (488 lineas):** Grid visual del context window

---

## 3. INK: Terminal UI Framework (48 archivos)

### 3.1 Core Architecture (ink.tsx -- 1722 lineas)

**Ink es un fork pesadamente customizado** del proyecto ink original. La clase `Ink` es el runtime:

- **Reconciler:** React custom reconciler (`react-reconciler`) con ConcurrentRoot mode
- **Render loop:** `scheduleRender` via throttle (FRAME_INTERVAL_MS) + microtask defer (para que layout effects commiteen antes del render)
- **Double-buffered rendering:** `frontFrame` y `backFrame` se swappean cada frame
- **Object pools:** `StylePool`, `CharPool`, `HyperlinkPool` -- interning para memory efficiency

**Alt-screen support:** Cursor clamping, mouse tracking (mode 1003), SIGCONT resume, DECSTBM hardware scroll hints.

**Selection system:** Text selection con mouse en alt-screen. `SelectionState` maneja start/extend/clear con shift-selection, word-select, line-select, URL detection.

**Search highlighting:** Post-render overlay que aplica inverse/bold/yellow a matching cells.

**Native cursor declaration:** `useDeclaredCursor` hook permite a componentes declarar donde debe estar el cursor fisico (para IME CJK input y screen readers).

### 3.2 Screen Buffer (screen.ts -- 1486 lineas)

**Cell-based terminal screen buffer.** Cada celda es un packed integer:

```
Cell = (charId << 20) | (styleId << 1) | visibleOnSpaceFlag
```

Hyperlinks almacenadas en array paralelo separado.

**Pool system:**
- `CharPool`: Intern strings a integer IDs. ASCII fast-path con Int32Array lookup directo
- `StylePool`: Intern AnsiCode[] a IDs con bit-0 encoding (visible-on-space flag). Transition cache para diff-based ANSI output
- `HyperlinkPool`: Intern hyperlink URLs

**StylePool.withInverse/withCurrentMatch/withSelectionBg:** Overlay operations que componen estilos preservando el base style. Current match = yellow-bg via fg-swap + inverse + bold + underline.

### 3.3 Render Pipeline (render-node-to-output.ts -- 1462 lineas)

**Layout shift detection:** Per-frame flag. Cuando un nodo cambia posicion/size, el renderer cae al full-damage path. Steady-state frames (spinner tick, text append) usan narrow damage bounds.

**Scroll optimization:**
- **DECSTBM hints:** Cuando un ScrollBox's scrollTop cambia sin otros movimientos, `log-update.ts` puede emitir hardware scroll en vez de rewrite
- **Adaptive drain:** xterm.js (VS Code) usa steps fijos; native terminals usan drain proporcional (3/4 de pending por frame)
- **Follow-scroll:** Cuando content crece y scrollTop auto-pins, la selection se translada para mantenerse anclada al texto

**OSC 8 hyperlinks:** Wrapping con secuencias OSC 8 para links clickeables.

**Styled text wrapping:** `applyStylesToWrappedText` mapea cada character de vuelta a su segmento original preservando per-segment styles a traves de line wraps.

### 3.4 Reconciler (reconciler.ts -- 512 lineas)

**Custom React reconciler** con:
- `createInstance` / `createTextInstance` crean DOM nodes
- `applyProp` dispatcha a style, textStyles, event handlers, o attributes
- Yoga node cleanup con `clearYogaNodeReferences` ANTES de `freeRecursive` (previene acceso a WASM memory liberada)
- Debug tooling: `getOwnerChain` para attributing repaints a componentes
- Commit instrumentation con logging opcional

### 3.5 DOM (dom.ts -- 484 lineas)

**Virtual DOM elements:**
- Types: `ink-root`, `ink-box`, `ink-text`, `ink-virtual-text`, `ink-link`, `ink-progress`, `ink-raw-ansi`
- Cada element tiene Yoga layout node (excepto virtual-text, link, progress)
- Text measurement via `measureTextNode` (custom Yoga measure function)
- Scroll state: `scrollTop`, `pendingScrollDelta`, `scrollClampMin/Max`, `stickyScroll`, `scrollAnchor`

### 3.6 Styles (styles.ts -- 771 lineas)

**CSS Flexbox subset completo:**
- Position (absolute/relative), flexDirection, flexGrow/Shrink/Basis, flexWrap
- Alignment (alignItems, alignSelf, justifyContent)
- Margin, padding (con X/Y shorthands)
- Width, height (numeros o porcentajes)
- Gap (row/column)
- Overflow: visible, hidden, scroll
- Borders con 8 estilos + custom text borders
- Display: flex, none

**Color types:** RGB, Hex, Ansi256, 16 named ANSI colors.

### 3.7 Components Ink (ink/components/)

- **Box.tsx:** Contenedor flexbox principal
- **Text.tsx:** Texto con estilos
- **ScrollBox.tsx:** Scroll container con virtualizacion
- **AlternateScreen.tsx:** Alt-screen mode management
- **Button.tsx, Link.tsx, Spacer.tsx, Newline.tsx**
- **RawAnsi.tsx:** Render raw ANSI content
- **NoSelect.tsx:** Marker para excluir de text selection

### 3.8 Hooks Ink (ink/hooks/)

- `use-input`: Keyboard input handling con key parsing
- `use-declared-cursor`: Cursor position declaration para IME
- `use-animation-frame`: RAF-like para terminal animations
- `use-selection`: Text selection state subscription
- `use-search-highlight`: Query-based highlighting
- `use-terminal-viewport`: Viewport size tracking
- `use-tab-status`: OSC 21337 tab status indicator
- `use-terminal-title`: OSC window title
- `use-terminal-focus`: Focus/blur detection

---

## 4. PATTERNS DE DISENO NOTABLES

### 4.1 Feature Flags via Dead Code Elimination

```typescript
import { feature } from 'bun:bundle'
const module = feature('FLAG_NAME')
  ? require('./module.js')
  : null
```

**Bun's bundler elimina el branch falso en build time.** Permite builds externos sin codigo interno de Anthropic (features: KAIROS, PROACTIVE, TRANSCRIPT_CLASSIFIER, REVIEW_ARTIFACT, WORKFLOW_SCRIPTS, etc.).

### 4.2 Lazy Schema Pattern

`lazySchema(() => z.object({...}))` -- Zod schemas se definen como factories lazily evaluadas para evitar circular dependencies y reducir startup cost.

### 4.3 Pool/Intern Pattern (Ink Screen)

Strings y styles se internan a integer IDs para comparacion O(1) y memory sharing. El packed cell integer encode 3 valores en un numero, eliminando object allocation per-cell.

### 4.4 Cascading Dirty + Blit Optimization

El renderer trackea dirty flags por nodo. Si un nodo no es dirty Y su cached output es valido, se "blitea" (copia del frame anterior) en O(1) en vez de re-render O(content). Un bug en esto causaba 150K+ writes/frame -- resuelto con React.memo en LogoHeader.

### 4.5 Microcompaction

`microcompactMessages` -- compactacion incremental durante la sesion (no solo al final). Reduce context usage sin perder contexto critico.

### 4.6 Defense-in-Depth en Permissions

Multiples capas independientes: permission rules -> classifier -> hooks -> sandbox -> trust dialog. Cada capa puede denegar independientemente. `SYNTHETIC_TOOL_RESULT_PLACEHOLDER` exportado para prevenir training data poisoning.

### 4.7 React Compiler Integration

Todo el output de componentes usa `_c()` (React Compiler cache). Props se trackean individualmente via slots indexados (`$[0]`, `$[1]`...) para memoizacion automatica sin `useMemo`/`useCallback` explicitos.

---

## 5. FEATURES OCULTAS / NO OBVIAS

1. **Companion system** ("Pickle" capybara): `buddy/` -- companion soul con bones regeneradas del userId. Muted state en GlobalConfig.

2. **Teleport** (utils/teleport.tsx, 1225 lineas): Cross-repository directory switching basado en GitHub repo mapping.

3. **Worktree isolation** (utils/worktree.ts, 1519 lineas): Git worktree-based session isolation con hook-based mode.

4. **Teammate mailbox** (utils/teammateMailbox.ts, 1183 lineas): Inter-agent message passing para multi-agent coordination (swarms).

5. **File history checkpointing** (utils/fileHistory.ts, 1115 lineas): Snapshots de archivos tocados por la sesion para revert capability.

6. **Tool result storage** (utils/toolResultStorage.ts, 1040 lineas): Content replacement records -- tool outputs largos se reemplazan con placeholders y se almacenan on-disk.

7. **Context collapse** (utils/collapseReadSearch.ts): Git operation tracking integrado -- detecta branch actions, commit kinds, PR actions en bash commands para mostrar resumen semantico en vez de raw output.

8. **Plans / Ultraplan** (utils/plans.ts, utils/ultraplan/): Plan mode con interview phase, file snapshots, y swarm launch integration.

9. **Fast mode model**: Hardcoded a `opus` con optional `[1m]` suffix para 1M context. Opus 4.6 specific.

10. **Grove config**: Per-account enterprise feature flagging cache.

---

