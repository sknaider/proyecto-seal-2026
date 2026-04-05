# OpenClaude Remaining Areas — Complete Analysis

> Covers all previously uncovered src/ subsystems. Combined with prior analyses, this reaches 100% source coverage.

---

## 1. COMPACT SERVICE (src/services/compact/) — 13 files

The compaction system is Claude Code's context management brain. It has **four tiers** of context reduction, from lightest to heaviest:

### Tier 1: Microcompact (`microCompact.ts`)
**Lightest touch — clears old tool results without summarizing.**

- **Time-based microcompact**: When gap since last assistant message > threshold (default 60min, configurable via GrowthBook `tengu_slate_heron`), content-clears old tool results. Rationale: server cache expired anyway, so shrink before rewrite.
- **Cached microcompact** (internal/ant only): Uses cache-editing API (`cache_edits` blocks) to remove tool results from the server-side cache without invalidating the prefix. Tool results from `FileRead`, `Bash`, `Grep`, `Glob`, `WebSearch`, `WebFetch`, `FileEdit`, `FileWrite` are eligible.
- **COMPACTABLE_TOOLS** set defines which tools' results can be cleared.
- `estimateMessageTokens()` — walks all message blocks (text, tool_result, thinking, redacted_thinking, tool_use, images) and pads by 4/3x.

### Tier 2: Session Memory Compaction (`sessionMemoryCompact.ts`)
**Uses the session memory markdown file as summary instead of calling the model.**

- Replaces traditional compaction when `tengu_session_memory` AND `tengu_sm_compact` feature flags are both enabled.
- `calculateMessagesToKeepIndex()` — starts from `lastSummarizedMessageId`, expands backwards until meeting: minTokens (10K), minTextBlockMessages (5), capped at maxTokens (40K).
- `adjustIndexToPreserveAPIInvariants()` — critical function ensuring tool_use/tool_result pairs are never split and thinking blocks with shared message.id are kept together.
- Falls back to legacy compaction if session memory is empty or post-compact size exceeds threshold.

### Tier 3: Auto-Compact (`autoCompact.ts`)
**Automatic summarization when context hits threshold.**

- Threshold = `effectiveContextWindow - 13K buffer tokens`
- **Circuit breaker**: After 3 consecutive failures, stops retrying for the session (was wasting ~250K API calls/day globally).
- `isAutoCompactEnabled()` checks: not disabled by env vars, not disabled in user settings.
- `shouldAutoCompact()` guards: not for `session_memory`/`compact`/`marble_origami` query sources, respects context-collapse mode, reactive-compact mode.
- Flow: tries session memory compaction first, falls back to `compactConversation()`.

### Tier 4: Full Compaction (`compact.ts`)
**Model-based summarization of the entire conversation.**

- `compactConversation()` — the core function:
  1. Executes PreCompact hooks
  2. Strips images from messages (saves tokens)
  3. Strips re-injected attachments (skill_discovery, skill_listing)
  4. Streams summary via forked agent (shares prompt cache)
  5. Handles prompt-too-long retries (truncates oldest API-round groups)
  6. Post-compact: restores CLAUDE.md, recently-read files (up to 5 files, 5K tokens each), invoked skills (5K/skill, 25K budget)
  7. Creates compact boundary marker message
- `CompactionResult` interface: boundaryMarker, summaryMessages, attachments, hookResults, messagesToKeep, pre/post token counts.
- `stripImagesFromMessages()` replaces image/document blocks with `[image]`/`[document]` markers.

### Supporting Files

- **`prompt.ts`**: Three prompt variants (BASE, PARTIAL, PARTIAL_UP_TO). All use `<analysis>` scratchpad + `<summary>` structure. `formatCompactSummary()` strips the analysis block. NO_TOOLS_PREAMBLE aggressively prevents tool calls.
- **`grouping.ts`**: `groupMessagesByApiRound()` — groups messages by assistant message.id boundaries. Used for partial compaction and PTL retry truncation.
- **`postCompactCleanup.ts`**: Clears caches after compaction — microcompact state, context-collapse, getUserContext cache, classifier approvals, system prompt sections, beta tracing state, session messages cache. Skips main-thread resets for subagent compactions.
- **`compactWarningState.ts` / `compactWarningHook.ts`**: React store + hook for UI warning suppression after successful compaction.
- **`timeBasedMCConfig.ts`**: GrowthBook config for time-based microcompact (enabled, gapThresholdMinutes, keepRecent).
- **`apiMicrocompact.ts`**: API-level context management using `clear_tool_uses_20250919` and `clear_thinking_20251015` strategies. Configurable trigger/target thresholds.
- **`snipCompact.ts`**: Stub (not in source snapshot).
- **`cachedMicrocompact.ts`**: Stub (feature-gated, not in snapshot).

---

## 2. SESSION MEMORY (src/services/SessionMemory/) — 3 files

### `sessionMemory.ts` — Core Extraction Engine
- **Runs as post-sampling hook** on main REPL thread only.
- Triggers when: (a) both token threshold AND tool call threshold met, OR (b) token threshold met AND no tool calls in last turn (natural pause).
- Default thresholds: init at 10K tokens, update every 5K token growth, 3 tool calls between updates.
- Uses `runForkedAgent()` for prompt cache sharing — the extraction subagent forks the main conversation.
- Only allows `FileEditTool` on the exact session memory file path.
- `initSessionMemory()` — registers hook if auto-compact is enabled. Synchronous to avoid startup race conditions.
- `manuallyExtractSessionMemory()` — bypasses threshold checks, used by `/summary` command.

### `prompts.ts` — Template & Update Prompt
- Default template has 9 sections: Session Title, Current State, Task Specification, Files and Functions, Workflow, Errors & Corrections, Codebase Documentation, Learnings, Key Results, Worklog.
- Per-section token limit: 2000. Total budget: 12000 tokens.
- Custom templates/prompts loadable from `~/.claude/session-memory/config/template.md` and `prompt.md`.
- `truncateSessionMemoryForCompact()` — truncates oversized sections when inserting into compact messages.
- `{{variable}}` substitution with single-pass replacement to avoid double-substitution bugs.

### `sessionMemoryUtils.ts` — Shared State
- Tracks: lastSummarizedMessageId, extraction in-progress state, tokens at last extraction, initialization flag.
- `waitForSessionMemoryExtraction()` — 15s timeout, stale threshold 1min.
- Clean separation to avoid circular dependencies with the forked agent system.

---

## 3. EXTRACT MEMORIES (src/services/extractMemories/) — 2 files

### `extractMemories.ts` — Auto-Memory Pipeline
- **Runs once at end of each complete query loop** (model produces final response with no tool calls) via stop hooks.
- Closure-scoped state pattern (same as confidenceRating.ts) — `initExtractMemories()` creates fresh state per session/test.
- **Overlap guard**: If extraction is in progress when another trigger arrives, stashes context for a trailing run.
- **Mutual exclusion with main agent**: If main agent already wrote to memory files (detected via `hasMemoryWritesSince()`), skips forked extraction.
- **Turn throttling**: `tengu_bramble_lintel` config controls extraction frequency (default every turn).
- `createAutoMemCanUseTool()` — allows Read/Grep/Glob unrestricted, Bash read-only only, Edit/Write only within auto-memory directory.
- `drainPendingExtraction()` — awaits in-flight extractions with soft timeout before shutdown.

### `prompts.ts` — Extraction Prompts
- Two variants: `buildExtractAutoOnlyPrompt()` (individual) and `buildExtractCombinedPrompt()` (with team memory).
- Four memory types from `memdir/memoryTypes.ts`, with frontmatter format.
- Two-turn efficient strategy: turn 1 = parallel reads, turn 2 = parallel writes.
- Index file (`MEMORY.md`) is an index of pointers, not content.

---

## 4. MAGIC DOCS (src/services/MagicDocs/) — 2 files

### `magicDocs.ts` — Self-Updating Documentation
- Files with `# MAGIC DOC: [title]` header are auto-tracked when read.
- Optional italicized instructions line immediately after header.
- **Post-sampling hook** updates all tracked docs when conversation is idle (no tool calls in last turn).
- Uses `runAgent()` with a dedicated magic-docs agent (model: sonnet, Edit-only).
- Internal-only feature (`USER_TYPE === 'ant'`).
- Re-detects header on each update — if header removed, stops tracking.

### `prompts.ts` — Update Prompt
- Terse, high-signal documentation philosophy: architecture > implementation details.
- In-place updates (not changelog). Removes outdated info.
- Custom prompts loadable from `~/.claude/magic-docs/prompt.md`.
- Same `{{variable}}` substitution pattern as session memory.

---

## 5. VOICE (src/services/voice.ts, voiceStreamSTT.ts, voiceKeyterms.ts)

### `voice.ts` — Audio Recording
- **Three recording backends** with fallback chain:
  1. **Native NAPI** (cpal): macOS, Linux (with ALSA cards), Windows. Lazy-loaded to avoid 1-8s dlopen blocking.
  2. **arecord** (ALSA utils): Linux fallback. Probed with 150ms timer to verify device opens.
  3. **SoX rec**: Linux/macOS fallback. Raw PCM 16kHz, 16-bit, mono.
- Platform detection: WSL audio support detection, homespace/remote environment check.
- `requestMicrophonePermission()` — probe-records briefly to trigger TCC dialog on macOS.
- Silence detection: SoX has built-in (2.0s silence at 3% threshold). arecord does not. Native module has its own.

### `voiceStreamSTT.ts` — WebSocket STT Client
- Connects to Anthropic's `voice_stream` WebSocket endpoint using OAuth tokens.
- Wire protocol: JSON control messages (KeepAlive, CloseStream) + binary audio frames.
- Server responds with TranscriptText (interim) and TranscriptEndpoint (final).
- **Finalize flow** with three resolution timers: post-CloseStream endpoint (~300ms), no-data timeout (1.5s), safety timeout (5s).
- Nova 3 (Deepgram) support via `tengu_cobalt_frost` feature flag — cumulative interims, no auto-finalize.
- Targets `api.anthropic.com` instead of `claude.ai` to avoid CF TLS fingerprinting.

### `voiceKeyterms.ts` — STT Accuracy Boosting
- Global terms: MCP, symlink, grep, regex, localhost, TypeScript, JSON, OAuth, webhook, gRPC, etc.
- Dynamic terms from: project root name, git branch words, recent file names.
- Max 50 keyterms. `splitIdentifier()` handles camelCase/kebab-case/snake_case/PascalCase.

---

## 6. LSP (src/services/lsp/) — 7 files

### Architecture
- **Plugin-only**: LSP servers are configured exclusively through plugins.
- **Singleton manager** (`manager.ts`): Global `LSPServerManager` initialized at startup, async background init.
- **Server lifecycle**: stopped -> starting -> running -> stopping -> stopped; any -> error -> starting (retry).

### Key Components
- **`LSPClient.ts`**: Wraps `vscode-jsonrpc` over stdio. Manages child process, pending handlers queue, crash detection.
- **`LSPServerManager.ts`**: Routes requests based on file extensions. Manages file open/change/save/close notifications. Lazy server start on first use.
- **`LSPServerInstance.ts`**: Per-server lifecycle with health monitoring. Retries transient errors (content-modified, -32801) up to 3 times with exponential backoff (500ms base).
- **`config.ts`**: `getAllLspServers()` loads from all enabled plugins in parallel.
- **`passiveFeedback.ts`**: Registers `textDocument/publishDiagnostics` handlers on all servers. Routes to diagnostic attachment system. Consecutive failure tracking with warnings after 3+.
- **`LSPDiagnosticRegistry.ts`**: LRU-cached dedup of delivered diagnostics (max 500 files). Volume limits: 10 diagnostics/file, 30 total.
- **`manager.ts`** (singleton): `initializeLspServerManager()`, `reinitializeLspServerManager()` (for plugin refresh), `shutdownLspServerManager()`. Generation counter prevents stale async state.

---

## 7. OAUTH (src/services/oauth/) — 6 files

### `index.ts` — OAuthService Class
- OAuth 2.0 Authorization Code flow with PKCE.
- Two auth paths: automatic (browser redirect to localhost) and manual (user pastes code).
- `AuthCodeListener` starts local HTTP server for callback.
- After code exchange: fetches profile info (subscription type, rate limit tier).
- Supports: `loginWithClaudeAi`, `inferenceOnly`, `orgUUID`, `loginHint`, `loginMethod`, `skipBrowserOpen` (for SDK control protocol).

### `client.ts` — OAuth Client
- `buildAuthUrl()` constructs authorization URLs with PKCE parameters.
- `shouldUseClaudeAIAuth()` checks for `CLAUDE_AI_INFERENCE_SCOPE`.
- `parseScopes()` splits space-delimited scope strings.
- Token exchange, profile fetch, scope management.

### `crypto.ts` — PKCE Crypto
- Code verifier generation, code challenge (SHA-256), state parameter generation.

---

## 8. ANALYTICS (src/services/analytics/) — 9 files

### Architecture: Zero-dependency event queue with lazy sink attachment

### `index.ts` — Public API
- Events queued until `attachAnalyticsSink()` called during startup. Drained via `queueMicrotask`.
- **Two marker types** for metadata verification:
  - `AnalyticsMetadata_I_VERIFIED_THIS_IS_NOT_CODE_OR_FILEPATHS` — for general strings
  - `AnalyticsMetadata_I_VERIFIED_THIS_IS_PII_TAGGED` — for PII-tagged proto columns
- `stripProtoFields()` removes `_PROTO_*` keys before general-access storage.
- String values intentionally excluded from metadata type to prevent accidental code/filepath logging.

### `sink.ts` — Event Routing
- Routes to: Datadog (gated by `tengu_log_datadog_events`) and 1P event logging.
- Event sampling via `shouldSampleEvent()`.
- `initializeAnalyticsSink()` + `initializeAnalyticsGates()` called during startup.

### `growthbook.ts` — Feature Flags & A/B Testing
- GrowthBook SDK for feature flags and experiments.
- User attributes: id, sessionId, deviceID, platform, org/account UUIDs, subscription type, etc.
- Cached values for non-blocking reads. Experiment data stored per-feature for exposure logging.

### `datadog.ts` — Log Shipping
- Batched log shipping to Datadog US5. 15s flush interval, 100 max batch size.
- ~65 allowed event types (allowlist). MCP tool names normalized to "mcp" for cardinality.
- User bucketing: SHA-256 hash mod 30 buckets for unique user estimation without PII.
- Model name normalization for cardinality reduction (external users only).

### `config.ts` — Privacy Controls
- `isAnalyticsDisabled()` returns **true** in Open Claude — no product telemetry.
- `isFeedbackSurveyDisabled()` — more permissive, allows survey on 3P providers.

### `sinkKillswitch.ts` — Per-sink remote kill
- GrowthBook config `tengu_frond_boric` can disable individual sinks (datadog, firstParty).

### `metadata.ts` — Event Enrichment (large file, not fully read)
- Adds platform, version, model, user type, env context to events.

---

## 9. TASKS (src/tasks/) — 9 files

### Task Type System (`types.ts`)
Union of 7 task types:
- `LocalShellTaskState` — Background bash commands
- `LocalAgentTaskState` — Local sub-agents
- `RemoteAgentTaskState` — Cloud sessions (ultraplan support)
- `InProcessTeammateTaskState` — In-process teammates (teams)
- `LocalWorkflowTaskState` — Background workflows
- `MonitorMcpTaskState` — MCP monitoring
- `DreamTaskState` — Memory consolidation

### `DreamTask.ts` — Memory Consolidation UI
- Background task wrapping the auto-dream (memory consolidation) subagent.
- Phase: `starting` -> `updating` (when first Edit/Write detected).
- Tracks: filesTouched, turns (max 30), sessionsReviewing.
- Kill rolls back consolidation lock mtime.

### `LocalMainSessionTask.ts` — Backgrounded Main Sessions
- When user presses Ctrl+B twice, current query backgrounds.
- Uses `startBackgroundSession()` to spawn independent `query()` call.
- Isolated per-task transcript (not main session file).
- Foreground/background toggle with task state management.
- Agent context wrapping for skill scoping across /clear.

### `InProcessTeammateTask/types.ts` — Team Agents
- `TeammateIdentity`: agentId, agentName, teamName, color, planModeRequired.
- Per-teammate permission mode cycling.
- `TEAMMATE_MESSAGES_UI_CAP = 50` — BQ analysis showed ~20MB RSS per agent at 500+ turns, whale session reached 36.8GB with 292 agents.
- `appendCappedMessage()` maintains UI-only message window.

### `stopTask.ts` — Task Termination
- Shared by TaskStopTool (LLM-invoked) and SDK stop_task.
- Error codes: not_found, not_running, unsupported_type.
- Shell tasks suppress exit code 137 notification (noise).

### `pillLabel.ts` — Footer UI Labels
- Renders compact pill labels for background tasks.
- Ultraplan phases: `plan_ready` (filled diamond), `needs_input` (open diamond).
- Team tasks show team count, shell tasks show shell/monitor split.

### `LocalShellTask/guards.ts` — Shell Task Type Guard
- `LocalShellTaskState`: command, result, shellCommand, isBackgrounded, agentId, kind (bash|monitor).

---

## 10. TYPES (src/types/) — 11 files

### `permissions.ts` — Complete Permission Type System
- **Permission modes**: acceptEdits, bypassPermissions, default, dontAsk, plan + internal: auto, bubble.
- **Permission behaviors**: allow, deny, ask.
- **Permission rules**: source (userSettings/projectSettings/localSettings/flagSettings/policySettings/cliArg/command/session) + behavior + value (toolName + optional ruleContent).
- **Decision types**: PermissionAllowDecision (with optional updatedInput, contentBlocks), PermissionAskDecision (with suggestions, classifier check), PermissionDenyDecision.
- **Classifier types**: ClassifierResult, YoloClassifierResult with two-stage XML (fast + thinking), stage-level usage tracking, request_id for BQ joins.
- **PermissionExplanation**: riskLevel (LOW/MEDIUM/HIGH) + explanation + reasoning + risk.

### `hooks.ts` — Hook Type System
- Hook events from `agentSdkTypes.js`. Zod schemas for validation.
- Sync hooks: continue, suppressOutput, stopReason, decision (approve/block), hookSpecificOutput per event type.
- Async hooks: `{async: true, asyncTimeout?}`.
- 14+ hook event types with specific outputs: PreToolUse, UserPromptSubmit, SessionStart, Setup, SubagentStart, PostToolUse, PostToolUseFailure, PermissionDenied, Notification, PermissionRequest, Elicitation, ElicitationResult, CwdChanged, FileChanged, WorktreeCreate.

### `plugin.ts` — Plugin System Types
- `BuiltinPluginDefinition`: name, description, skills, hooks, mcpServers, isAvailable, defaultEnabled.
- `LoadedPlugin`: manifest, paths for commands/agents/skills/outputStyles, hooks config, MCP/LSP servers, settings.
- 23 discriminated PluginError types covering: path-not-found, git failures, network errors, manifest errors, marketplace errors, MCP/LSP config errors, dependency issues, cache misses.
- `getPluginErrorMessage()` provides human-readable messages for all error types.

### Other Type Files
- `command.ts` — Command type definitions
- `connectorText.ts` — Connector text types
- `ids.ts` — AgentId and other identifier types
- `logs.ts` — Log type definitions
- `textInputTypes.ts` — Text input type definitions
- `generated/` — Protobuf-generated types for analytics events (Claude Code internal events, auth, GrowthBook experiments, timestamps)

---

## 11. KEYBINDINGS (src/keybindings/) — 12 files

### `defaultBindings.ts` — Full Keybinding Map
14 contexts with complete bindings:
- **Global**: ctrl+c/d (reserved), ctrl+l (redraw), ctrl+t (todos), ctrl+o (transcript), ctrl+r (history), ctrl+shift+f/p (file search/quick open)
- **Chat**: escape (cancel), ctrl+x ctrl+k (kill agents), shift+tab (cycle mode, meta+m on Windows without VT), meta+p (model picker), enter (submit), ctrl+s (stash), ctrl+g (external editor), space (voice push-to-talk)
- **Autocomplete**: tab/escape/up/down
- **Settings**: vim-style j/k navigation, space toggle, enter save, / search
- **Confirmation**: y/n/enter/escape, shift+tab (cycle mode), ctrl+e (explanation)
- **Transcript**: ctrl+e (toggle), escape/q/ctrl+c (exit)
- **Task**: ctrl+b (background)
- **Scroll**: pageup/down, wheelup/down, ctrl+home/end, ctrl+shift+c (copy)
- **MessageSelector**: vim j/k + arrow navigation
- **MessageActions**: vim navigation + enter/c/p actions
- **DiffDialog**, **ModelPicker**, **Footer**, **Attachments**, **HistorySearch**, etc.

### `resolver.ts` — Key Resolution Engine
- `resolveKey()` — single-keystroke matching, last binding wins (user overrides).
- `resolveKeyWithChordState()` — multi-keystroke chord support (e.g., ctrl+x ctrl+k). Handles chord_started, chord_cancelled states.
- Null-unbinding: binding action=null means "unbound" — swallows the event.
- `keystrokesEqual()` collapses alt/meta into one logical modifier (legacy terminals can't distinguish).

### Other Files
- `parser.ts` — Parses binding strings like "ctrl+shift+f" into ParsedKeystroke.
- `match.ts` — Low-level key matching against Ink's Key object.
- `schema.ts` — Zod validation for keybindings.json format.
- `loadUserBindings.ts` — Loads from `~/.claude/keybindings.json`.
- `validate.ts` — Validates user bindings against reserved shortcuts.
- `reservedShortcuts.ts` — ctrl+c and ctrl+d cannot be rebound.
- `template.ts` — Generates keybindings.json template.
- `useKeybinding.ts` — React hook for keybinding consumption.
- `useShortcutDisplay.ts` — React hook for displaying shortcut text.
- `shortcutFormat.ts` — Formats shortcuts for display (e.g., "Ctrl+K").

---

## 12. MIGRATIONS (src/migrations/) — 11 files

Settings migration pipeline. Each runs at startup to handle version transitions:

- `migrateFennecToOpus.ts` — fennec-latest -> opus, fennec-fast-latest -> opus[1m] + fast (ant-only)
- `migrateLegacyOpusToCurrent.ts` — Old opus aliases to current
- `migrateOpusToOpus1m.ts` — opus -> opus[1m]
- `migrateSonnet1mToSonnet45.ts` — sonnet[1m] -> sonnet 4.5
- `migrateSonnet45ToSonnet46.ts` — sonnet 4.5 -> sonnet 4.6
- `resetProToOpusDefault.ts` — Reset pro users to opus default
- `migrateAutoUpdatesToSettings.ts` — Auto-update config migration
- `migrateBypassPermissionsAcceptedToSettings.ts` — Permission bypass migration
- `migrateEnableAllProjectMcpServersToSettings.ts` — MCP server enablement
- `migrateReplBridgeEnabledToRemoteControlAtStartup.ts` — REPL bridge -> remote control
- `resetAutoModeOptInForDefaultOffer.ts` — Auto mode opt-in reset

---

## 13. SERVER (src/server/) — 3 files

### `directConnectManager.ts` — WebSocket Direct Connection
- `DirectConnectSessionManager` class manages WebSocket connection to a remote server.
- Handles SDK messages and permission requests via callbacks.
- Parses NDJSON from WebSocket messages.

### `createDirectConnectSession.ts` — Session Creation
- POSTs to `${serverUrl}/sessions` with cwd and permission options.
- Returns `DirectConnectConfig` (serverUrl, sessionId, wsUrl, authToken).
- `DirectConnectError` class for connection failures.

### `types.ts` — Server type definitions and schemas.

---

## 14. OTHER SUBSYSTEMS

### `native-ts/file-index/` — Pure-TS File Fuzzy Search
- Port of Rust NAPI nucleo module for fuzzy file searching.
- Scoring: nucleo-style with boundary/camel/consecutive/first-char bonuses, gap penalties.
- Test files get 1.05x penalty. Lower score = better.
- Async chunking: yields to event loop after 4ms of sync work.
- `native-ts/color-diff/` — Color difference calculations.
- `native-ts/yoga-layout/` — Layout engine bindings.

### `outputStyles/loadOutputStylesDir.ts` — Custom Output Styles
- Loads markdown files from `.claude/output-styles/` and `~/.claude/output-styles/`.
- Frontmatter: name, description, keep-coding-instructions (bool).
- `force-for-plugin` only works on plugin output styles.

### `schemas/hooks.ts` — Hook Zod Schemas
- Extracted to break import cycles between settings/types.ts and plugins/schemas.ts.
- `IfConditionSchema` — permission rule syntax for hook filtering (e.g., "Bash(git *)").
- Command hooks: type, command, if, shell (bash/powershell), timeout, statusMessage, once, async, asyncRewake.

### `upstreamproxy/upstreamproxy.ts` — CCR Proxy Setup
- Container-side wiring for CCR session containers.
- Flow: read session token -> prctl(PR_SET_DUMPABLE,0) to block ptrace -> download CA cert -> start CONNECT->WebSocket relay -> unlink token -> set env vars.
- Security: `setNonDumpable()` uses Bun FFI to call prctl, blocking same-UID heap scraping.
- PEM validation prevents compromised upstream from injecting arbitrary data.
- NO_PROXY list covers: loopback, RFC1918, IMDS, Anthropic API (breaks non-Bun runtimes), GitHub, npm, PyPI, crates.io.
- `getUpstreamProxyEnv()` — env vars for all subprocesses (HTTPS_PROXY, SSL_CERT_FILE, NODE_EXTRA_CA_CERTS, etc.).

---

## Key Architecture Insights

### Context Management Hierarchy (lightest to heaviest)
1. **API Context Management** (apiMicrocompact.ts) — server-side clear_tool_uses/clear_thinking
2. **Cached Microcompact** — cache-editing API, no content mutation
3. **Time-based Microcompact** — content-clears tool results, mutates messages
4. **Session Memory Compact** — uses pre-built session notes as summary
5. **Full Compaction** — model-based summarization
6. **Context Collapse** (separate system) — commits context segments to disk

### Memory Extraction Dual-Path
- **Session Memory**: Background extraction during conversation for compaction use. Markdown format, section-based.
- **Auto-Memory (extractMemories)**: End-of-turn extraction for persistent cross-session memory. Frontmatter format, file-per-topic.
- **Mutual exclusion**: If main agent writes to memory, extraction subagent skips.

### Plugin Architecture Depth
- Plugins provide: commands, agents, skills, hooks, output styles, MCP servers, LSP servers.
- 23 typed error conditions with structured error handling.
- Marketplace support with dependency management and policy enforcement.

### Security Measures
- Analytics: no code/filepaths in metadata (enforced by type system), PII routing to privileged columns only.
- Upstream proxy: prctl anti-ptrace, PEM validation, token file unlinking after relay startup.
- Permission system: 11+ decision reason types, two-stage classifier with separate stage tracking.
