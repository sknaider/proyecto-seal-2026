# SPEC 15 — Utils (608 files), Components (399 files), Hooks (106 files)

I now have comprehensive data across all three areas. Here is the complete spec:

---

# OpenClaude Architecture Spec: Utils, Components, and Hooks

## FILE COUNTS
- **Utils**: 608 files, 188,293 lines
- **Components**: 399 files, 82,936 lines 
- **Hooks**: 106 files, 19,279 lines

---

## PART 1: UTILS (608 files)

### 1.1 Settings System (`src/utils/settings/`)

**Architecture**: Multi-source cascading configuration with Zod validation.

**Settings Sources** (in precedence order, lowest to highest):
1. **Managed settings** (`/etc/claude-code/managed-settings.json` + drop-in dir `managed-settings.d/*.json`) -- systemd/sudoers convention where drop-ins override base
2. **MDM/HKCU** (platform-specific enterprise policy -- macOS MDM, Windows Registry)
3. **User settings** (`~/.claude/settings.json`)
4. **Project settings** (`.claude/settings.json` in project root)
5. **Local settings** (`.claude/settings.local.json`)
6. **CLI flags** (`--settings` inline JSON)
7. **Remote managed settings** (synced from cloud)

**Key files**:
- `/src/utils/settings/types.ts` (1169 lines) -- `SettingsSchema` is a massive Zod schema defining ALL configuration options. Key fields: `apiKeyHelper`, `env`, `permissions` (allow/deny/ask rule arrays + defaultMode), `model`, `availableModels` (enterprise allowlist), `modelOverrides` (Bedrock ARN mapping), `hooks` (lifecycle event hooks), `worktree`, `allowedMcpServers`/`deniedMcpServers`, `allowedHttpHookUrls`, `attribution` (commit/PR text), `fileSuggestion`, `sandbox` config.
- `/src/utils/settings/settings.ts` (1015 lines) -- `getSettingsForSource()` loads/caches per-source; `getSettings_DEPRECATED()` returns merged result. Uses `mergeWith` with custom array-merge strategy. File-parsed results are cloned to prevent cache mutation. Supports symlinks, drop-in directories.
- `/src/utils/settings/settingsCache.ts` -- Per-file parsed-result cache + per-source merged-result cache + session cache. All caches invalidated via `resetSettingsCache()`.
- `/src/utils/settings/validation.ts` -- Zod parse + `filterInvalidPermissionRules()` to strip bad rules while preserving the rest. `formatZodError()` for human-readable output.
- `/src/utils/settings/permissionValidation.ts` -- `PermissionRuleSchema` validating tool permission rules.
- `/src/utils/settings/changeDetector.ts` -- Watches settings files for changes.

**SettingsSchema key sections** (from `types.ts`):
```
permissions: { allow: Rule[], deny: Rule[], ask: Rule[], defaultMode, additionalDirectories }
hooks: { PreToolUse, PostToolUse, Notification, Stop, SessionStart, etc. }
env: Record<string, string>
model: string (override)
availableModels: string[] (enterprise allowlist, supports family aliases like "opus")
worktree: { symlinkDirectories, sparsePaths }
sandbox: SandboxSettingsSchema
```

**Customization surfaces lockable by enterprise**: `skills`, `agents`, `hooks`, `mcp` (via `strictPluginOnlyCustomization`).

**SEAL relevance**: The settings cascade pattern (managed -> user -> project -> local) with Zod validation is directly applicable for SEAL IDE configuration hierarchy.

---

### 1.2 Authentication (`src/utils/auth.ts`, 2010 lines)

**Multi-provider auth with priority cascade**:

1. OAuth (Claude.ai accounts -- Pro/Max/Team subscribers)
2. API key from environment (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`)
3. API key from file descriptor (`CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR`)
4. `apiKeyHelper` script (runs external command to get key)
5. macOS Keychain secure storage
6. AWS STS (for Bedrock)

**Key concepts**:
- `isAnthropicAuthEnabled()` -- determines if 1P OAuth is available. Disabled for 3P providers (Bedrock/Vertex/Foundry/OpenAI/Gemini), external API keys, or `--bare` mode.
- `getAuthTokenSource()` returns one of: `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR`, `CCR_OAUTH_TOKEN_FILE`, `apiKeyHelper`, `none`.
- `isManagedOAuthContext()` -- true for remote CCR or Claude Desktop sessions (should NOT fall back to user's ~/.claude API key).
- Subscription types tracked: `isMaxSubscriber()`, `isProSubscriber()`, `isTeamPremiumSubscriber()`.
- TTL-cached API key helper (5 min default).

---

### 1.3 Model System (`src/utils/model/`, 20 files)

**Provider abstraction**: `APIProvider` = `'firstParty' | 'bedrock' | 'vertex' | 'foundry' | 'openai' | 'gemini' | 'github' | 'codex'`

**Model configs** (`configs.ts`): Each model has a per-provider config mapping:
```typescript
const CLAUDE_OPUS_4_6_CONFIG = {
  firstParty: 'claude-opus-4-6',
  bedrock: 'us.anthropic.claude-opus-4-6-v1',
  vertex: 'claude-opus-4-6',
  foundry: 'claude-opus-4-6',
  openai: 'gpt-4o',
  gemini: 'gemini-2.5-pro-preview-03-25',
}
```

**ALL_MODEL_CONFIGS** registered: haiku35, haiku45, sonnet35, sonnet37, sonnet40, sonnet45, sonnet46, opus40, opus41, opus45, opus46.

**Model selection priority** (`model.ts`):
1. Session override (`/model` command)
2. Startup flag (`--model`)
3. `ANTHROPIC_MODEL` env var
4. `settings.model`
5. Built-in default (Sonnet 4.6 for firstParty)

Enterprise `availableModels` allowlist filters the selection. Supports family aliases: `"opus"` allows any opus version.

**Model aliases** (`aliases.ts`): `sonnet`, `opus`, `haiku`, `best`, `sonnet[1m]`, `opus[1m]`, `opusplan`.

**Other model files**: `modelCapabilities.ts` (feature support per model), `modelAllowlist.ts` (enterprise gating), `deprecation.ts` (sunset warnings), `bedrock.ts` (ARN resolution), `ollamaModels.ts`, `openaiContextWindows.ts`, `openaiModelDiscovery.ts`.

---

### 1.4 Plugin System (`src/utils/plugins/`, 15+ files)

**Architecture**: Marketplace-based plugin distribution with versioned caching.

**Plugin structure**:
```
my-plugin/
  plugin.json       # Manifest with metadata
  commands/          # Custom slash commands (.md files)
  agents/            # Custom AI agents (.md files)
  hooks/hooks.json   # Hook definitions
```

**Key files**:
- `pluginLoader.ts` (3302 lines) -- Discovery, validation, loading. Sources: marketplace plugins (`name@marketplace`), session-only plugins (`--plugin-dir`), built-in plugins. Handles git-based fetching, version pinning, zip caching.
- `marketplaceManager.ts` (2643 lines) -- Fetches/caches marketplace manifests. Supports multiple known marketplaces + extra marketplaces from repo settings.
- `installedPluginsManager.ts` (1268 lines) -- Tracks installed plugins, enable/disable state.
- `schemas.ts` (1681 lines) -- `PluginManifestSchema`, `PluginIdSchema`, `PluginHooksSchema`, `PluginMarketplaceEntry`.
- `loadPluginCommands.ts` -- Loads `.md` command files from plugin dirs.

**Security**: Path traversal prevention (`validatePathWithinBase`), source allowlist/blocklist policy, dependency resolution with demotion.

---

### 1.5 Swarm Coordination (`src/utils/swarm/`, 10+ files)

**In-process teammate runner** (`inProcessRunner.ts`, 1552 lines):
- Wraps `runAgent()` with AsyncLocalStorage-based context isolation
- Progress tracking, AppState updates, idle notification
- Permission flow: tries bash classifier auto-approval first, then uses leader's ToolUseConfirm dialog with worker badge, falls back to mailbox system
- Auto-compact for long-running teammates
- Plan mode approval flow support

**Teammate mailbox** (`teammateMailbox.ts`, 1183 lines):
- File-based messaging: `.claude/teams/{team_name}/inboxes/{agent_name}.json`
- Lock-based concurrent access with retry/backoff
- Message types: text, permission requests/responses, shutdown requests, plan approvals, mode changes
- Each message has `from`, `text`, `timestamp`, `read`, `color`, `summary`

**Other swarm files**: `constants.ts` (TEAM_LEAD_NAME), `leaderPermissionBridge.ts`, `permissionSync.ts`, `teammatePromptAddendum.ts`, backend registry for different pane backends.

---

### 1.6 Message Factory (`src/utils/messages.ts`, 5512 lines)

The largest util file. Core message creation and manipulation.

**Message creation factories**:
- `createAssistantMessage({ content, usage, isVirtual })` -- synthetic assistant messages
- `createAssistantAPIErrorMessage({ content, apiError, error })` -- API error messages  
- `createUserMessage({ content, isMeta, isVisibleInTranscriptOnly, isVirtual, isCompactSummary, toolUseResult, mcpMeta, imagePasteIds, permissionMode, origin })` -- rich user message creation

**Synthetic messages**: `INTERRUPT_MESSAGE`, `CANCEL_MESSAGE`, `REJECT_MESSAGE`, `NO_RESPONSE_REQUESTED`, `SUBAGENT_REJECT_MESSAGE`. All recognized by `isSyntheticMessage()`.

**Permission denial messages**:
- `buildYoloRejectionMessage(reason)` -- for auto-mode classifier denials
- `AUTO_REJECT_MESSAGE(toolName)` -- denied tools
- `DENIAL_WORKAROUND_GUIDANCE` -- shared text explaining acceptable workarounds

**Key helpers**: `deriveShortMessageId(uuid)` (6-char base36 for tool referencing), `getLastAssistantMessage()`, `normalizeMessagesForAPI()`, `extractTextContent()`, `wrapInSystemReminder()`, `buildMessageLookups()` (creates assistant/user/tool lookup maps for rendering), `isCompactBoundaryMessage()`.

**Memory correction**: `withMemoryCorrectionHint(message)` -- appends hint for the model to watch for user corrections after rejections.

---

### 1.7 Token Counting (`src/utils/tokens.ts`)

**Three measurement strategies**:
1. `getTokenCountFromUsage(usage)` -- from API response usage data (input + cache + output)
2. `tokenCountFromLastAPIResponse(messages)` -- walks backward to find last real API response
3. `tokenCountWithEstimation(messages)` -- rough estimation when no API response available (for threshold checks)

**Specialized functions**:
- `finalContextTokensFromLastResponse()` -- uses `usage.iterations[-1]` for post-compaction budget; falls back to input+output without cache.
- `messageTokenCountFromLastAPIResponse()` -- output tokens only (for measuring generation size, NOT context fullness).
- `getCurrentUsage()` -- returns `{ input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens }`.

---

### 1.8 System Prompt Assembly (`src/utils/queryContext.ts`)

**`fetchSystemPromptParts()`**: Fetches three parallel pieces:
1. `defaultSystemPrompt` -- from `getSystemPrompt(tools, model, additionalDirs, mcpClients)`
2. `userContext` -- from `getUserContext()` (CLAUDE.md files, memory)
3. `systemContext` -- from `getSystemContext()` (platform info, env)

When `customSystemPrompt` is set, defaults 1 and 3 are skipped.

**`buildSideQuestionFallbackParams()`**: Rebuilds CacheSafeParams when the main loop hasn't produced a snapshot yet (for SDK side_question handler on resume).

---

### 1.9 CLAUDE.md System (`src/utils/claudemd.ts`, 1488 lines)

**Load order** (reverse priority -- later = higher):
1. Managed memory (`/etc/claude-code/CLAUDE.md`)
2. User memory (`~/.claude/CLAUDE.md`)
3. Project memory (`CLAUDE.md`, `.claude/CLAUDE.md`, `.claude/rules/*.md` in project roots)
4. Local memory (`CLAUDE.local.md` in project roots)

**Features**:
- `@include` directive for file inclusion (`@path`, `@./relative`, `@~/home`, `@/absolute`)
- Only text file extensions allowed (prevents binary inclusion)
- Circular reference prevention
- Directory traversal from CWD to root for discovery
- `MAX_MEMORY_CHARACTER_COUNT = 40000`
- Integration with `InstructionsLoaded` hooks

---

### 1.10 File State Cache (`src/utils/fileStateCache.ts`)

**LRU-based file content cache** for the Edit/Write tools' "must read before edit" enforcement.

```typescript
class FileStateCache {
  // LRU cache with path normalization and size-based eviction
  // Max 100 entries, 25MB total
  // Tracks: content, timestamp, offset, limit, isPartialView
}
```

`isPartialView` flag marks entries populated by auto-injection (CLAUDE.md) where content was stripped/truncated -- forces explicit Read before Edit/Write.

Helpers: `cloneFileStateCache()`, `mergeFileStateCaches()` (newer timestamps win), `cacheToObject()`.

---

### 1.11 Session Storage (`src/utils/sessionStorage.ts`, 5361 lines)

**JSONL-based session persistence**. Each session = one `.jsonl` file under `~/.claude/projects/{sanitized_cwd}/`.

**Entry types**: user, assistant, attachment, system (but NOT progress -- ephemeral).

**Key concepts**:
- `isTranscriptMessage()` -- the single source of truth for what's persisted
- `isChainParticipant()` -- entries that get `parentUuid` linking
- `isEphemeralToolProgress()` -- bash_progress, powershell_progress, mcp_progress, sleep_progress
- `MAX_TOMBSTONE_REWRITE_BYTES = 50MB` -- prevents OOM on large sessions
- Progress bridge: handles legacy transcripts that included progress entries

---

### 1.12 Config (`src/utils/config.ts`, 1854 lines)

**Two config layers**:
1. **GlobalConfig** -- `~/.claude/config.json`. Contains: `projects` map (per-project settings), `numStartups`, `theme`, `autoCompactEnabled`, `showTurnDuration`, `oauthAccount`, `editorMode`, `companion` (buddy system), `memoryUsageCount`, subscription caches, voice settings, many feature-tracking flags.
2. **ProjectConfig** -- keyed by sanitized CWD path within GlobalConfig. Contains: `allowedTools`, `mcpServers`, session metrics (cost, tokens, duration, lines), trust dialog state, MCP server approvals, worktree session.

**ProviderProfile** type for multi-provider support: `{ id, name, provider: 'openai'|'anthropic', baseUrl, model, apiKey }`.

---

### 1.13 Hooks Engine (`src/utils/hooks.ts`, 5022 lines)

**Full lifecycle hook system**. Events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionDenied`, `Notification`, `Stop`, `StopFailure`, `SessionStart`, `SessionEnd`, `SubagentStart`, `SubagentStop`, `TaskCreated`, `TaskCompleted`, `ConfigChange`, `CwdChanged`, `FileChanged`, `InstructionsLoaded`, `UserPromptSubmit`, `PermissionRequest`, `Elicitation`, `ElicitationResult`, `Setup`, `PreCompact`, `PostCompact`, `TeammateIdle`, `StatusLine`, `FileSuggestion`.

**Hook types**: Bash commands, Prompt hooks (agent-based), HTTP hooks, Agent hooks.

**Execution**: 10-minute timeout for tool hooks, 1.5s for SessionEnd. Background async execution supported. Shell commands use configurable shell (bash/powershell). Plugin hooks with variable substitution. Telemetry spans per hook.

---

### 1.14 Context Analysis (`src/utils/analyzeContext.ts`, 1382 lines)

Token budget visualization for `/context` command. Counts tokens for: system prompt, tools, CLAUDE.md files, messages, skills, compact buffer. Uses API token counting with Haiku fallback. Defines `TOOL_TOKEN_COUNT_OVERHEAD = 500` (fixed API overhead per tool).

---

### 1.15 Other Notable Utils

| File | Lines | Purpose |
|------|-------|---------|
| `attachments.ts` | 3998 | Attachment system -- file, image, URL, hook, memory header attachments |
| `bash/bashParser.ts` | 4436 | Full bash AST parser for command analysis |
| `bash/ast.ts` | 2679 | Bash AST node types |
| `shell/readOnlyCommandValidation.ts` | 1893 | Determines if bash commands are read-only (for auto-approval) |
| `permissions/filesystem.ts` | 1777 | Path-based permission checks, `pathInWorkingPath()` |
| `permissions/yoloClassifier.ts` | 1603 | Auto-mode safety classifier |
| `permissions/permissionSetup.ts` | 1530 | Permission mode setup/transitions |
| `worktree.ts` | 1519 | Git worktree management |
| `ide.ts` | 1496 | IDE integration (VSCode, JetBrains) |
| `fileHistory.ts` | 1115 | File change tracking for undo/attribution |
| `collapseReadSearch.ts` | 1109 | Collapses sequential Read/Grep calls in UI |
| `toolResultStorage.ts` | 1068 | Content replacement for large tool results |
| `stats.ts` | 1061 | Session statistics tracking |
| `commitAttribution.ts` | 961 | Git commit attribution (Co-Authored-By) |
| `sandbox/sandbox-adapter.ts` | 985 | Sandbox execution adapter |
| `teleport.tsx` | 1225 | Session transfer between environments |
| `Cursor.ts` | 1530 | Full cursor movement engine (emacs-style kill ring, yank/pop) |
| `agentContext.ts` | -- | AsyncLocalStorage-based agent context isolation |
| `caCerts.ts` | 115 | Custom CA certificate loading |
| `crypto.ts` | 13 | Thin re-export of `randomUUID` (browser-swappable) |
| `log.ts` | 362 | Structured logging |

---

## PART 2: COMPONENTS (399 files)

### 2.1 Main App Flow

The app renders through a **REPL screen** model:

1. **`FullscreenLayout.tsx`** (636 lines) -- Top-level layout with ScrollBox, header, footer
2. **`Messages.tsx`** (833 lines) -- Message list container. Applies filtering (brief mode, compact boundaries), grouping (tool use groups, read/search collapse), reordering, normalization. Uses `buildMessageLookups()` for efficient tool_use/tool_result pairing.
3. **`VirtualMessageList.tsx`** (1081 lines) -- Virtualized scrolling. Renders only visible items + overscan (80 rows). Height measurement via Yoga layout. Sticky prompt tracker for scroll context. Full search implementation (incremental, highlight, n/N navigation).
4. **`Message.tsx`** (626 lines) -- Message router: switches on `message.type` to render the appropriate sub-component.

### 2.2 Message Rendering

**Per message type**:
- `attachment` -> `AttachmentMessage` (536 lines)
- `assistant` -> Iterates `content` blocks, each gets `AssistantMessageBlock`:
  - `text` -> `AssistantTextMessage` (markdown streaming)
  - `tool_use` -> `AssistantToolUseMessage` (tool call display)
  - `thinking` -> `AssistantThinkingMessage`
  - `redacted_thinking` -> `AssistantRedactedThinkingMessage`
  - Advisor blocks -> `AdvisorMessage`
  - Connector text -> special handling
- `user` -> Routes by content type:
  - `text` -> `UserTextMessage` (with `>` prefix)
  - `tool_result` -> `UserToolResultMessage` (collapsible tool output)
  - `image` -> `UserImageMessage`
  - Compact summary -> `CompactSummary`
- `system` -> `SystemTextMessage` (826 lines, handles many subtypes: api_error, compact_boundary, agents_killed, bridge_status, etc.)

**Grouped rendering**:
- `GroupedToolUseContent` -- Multiple tool calls collapsed into one visual group
- `CollapsedReadSearchContent` (483 lines) -- Sequential Read/Grep calls collapsed

### 2.3 PromptInput (`src/components/PromptInput/`, 10+ files)

**`PromptInput.tsx`** (2338 lines) -- The largest component. Main input box with:
- Text input with cursor movement (emacs/vim modes)
- Typeahead suggestions (files, commands, agents, shell history, Slack channels)
- Image paste handling (clipboard detection)
- Mode indicators (permission mode, model, effort level)
- Inline ghost text (speculative completion)
- Stash support (Ctrl+S to save/restore prompt)
- Queued command display
- Voice integration
- Bridge dialog (remote control)
- Model picker, fast mode picker, thinking toggle
- Footer with notifications, suggestions, status

### 2.4 Permission Dialogs

**`permissions/`** directory (15+ files):
- `BashPermissionRequest` (481 lines) -- Shows command, allows yes/no/always/deny
- `ExitPlanModePermissionRequest` (767 lines) -- Plan -> implementation transition approval
- `AskUserQuestionPermissionRequest` (644 lines) -- Tool asking user a question
- `FileEditToolDiff` -- Shows file edit diff for approval
- `PermissionRuleList` (1178 lines) -- Settings UI for managing permission rules
- `PermissionDecisionDebugInfo` (459 lines) -- Debug view of permission decisions
- `SandboxViolationExpandedView` -- Sandbox violation details

### 2.5 Spinner System (`src/components/Spinner/` + `Spinner.tsx`)

**`Spinner.tsx`** (561 lines) -- Main spinner with:
- Mode-aware display: `thinking`, `tool`, `idle`, `processing`
- Elapsed time counter
- Thinking status tracker (shows "thinking" for min 2s, then duration)
- Token budget progress bar
- Teammate spinner tree (shows parallel teammate status)
- Brief mode variant
- Stalled detection (red shimmer after timeout)
- Shimmer animation on text

**Sub-components**: `SpinnerAnimationRow` (animation frame driver), `SpinnerGlyph`, `ShimmerChar`, `FlashingChar`, `TeammateSpinnerTree`, `TeammateSpinnerLine`, `GlimmerMessage`.

### 2.6 Other Key Components

| Component | Lines | Purpose |
|-----------|-------|---------|
| `Settings/Config.tsx` | 1840 | Full settings configuration UI |
| `LogSelector.tsx` | 1574 | Session log browser/selector |
| `Stats.tsx` | 1227 | Session statistics display |
| `StatusLine.tsx` | ~500 | Dynamic status line (model, context, cost, rate limits, vim mode) |
| `MessageRow.tsx` | ~500 | Individual message row with margin, dot indicator, hover |
| `MessageSelector.tsx` | 830 | Session/message selection UI |
| `mcp/MCPSettings.tsx` | ~500 | MCP server configuration panel |
| `mcp/ElicitationDialog.tsx` | 1168 | MCP server elicitation (form) dialogs |
| `mcp/MCPListPanel.tsx` | 503 | MCP server list with status indicators |
| `ContextVisualization.tsx` | 488 | Visual token budget breakdown |
| `TaskListV2.tsx` | ~400 | Task list display (todos) |
| `EffortPicker.tsx` | ~300 | Reasoning effort level selector |
| `QuickOpenDialog.tsx` | ~300 | Quick file open (Ctrl+P style) |
| `ScrollKeybindingHandler.tsx` | 1011 | Keyboard-driven scroll (j/k/PgUp/PgDn/G/gg) |
| `HighlightedCode/` | ~300 | Syntax-highlighted code display |
| `StructuredDiff/` | ~500 | Structured diff rendering |
| `FeedbackSurvey/` | 8 files | Post-turn feedback collection |

---

## PART 3: HOOKS (106 files)

### 3.1 Complete Hook Inventory

**Core Input/Text Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `useTextInput` | 529 | Full text input engine -- cursor, kill ring, emacs bindings, multiline, image paste, ghost text |
| `useVimInput` | 316 | Vim mode input (normal/insert/visual) |
| `useTypeahead` | 1392 | Typeahead suggestions -- files (@mentions), commands (/), agents, shell completion, Slack channels, argument hints. Debounced async. |
| `useArrowKeyHistory` | 228 | Up/down arrow prompt history navigation |
| `useHistorySearch` | 303 | Ctrl+R reverse history search |
| `useSearchInput` | 364 | Search input mode (for transcript search) |
| `useInputBuffer` | ~100 | Early input buffering before component mount |
| `usePasteHandler` | 310 | Multi-line paste detection/handling |
| `useDoublePress` | ~50 | Double-keypress detection |

**Permission Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `toolPermission/PermissionContext` | 388 | Core permission context factory: creates resolve-once guards, queue ops, logging, abort handling |
| `toolPermission/handlers/interactiveHandler` | 536 | Interactive (main agent) permission flow: pushes to confirm queue, races hooks/classifier against user |
| `toolPermission/handlers/coordinatorHandler` | ~100 | Coordinator mode permission handling |
| `toolPermission/handlers/swarmWorkerHandler` | 159 | Swarm worker permission (sends to leader) |
| `toolPermission/permissionLogging` | 238 | Analytics logging for permission decisions |
| `useCanUseTool` | 203 | Main permission decision hook -- calls `hasPermissionsToUseTool()`, dispatches to handler |
| `useSwarmPermissionPoller` | 330 | Polls mailbox for permission requests from teammates |

**State Management Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `useSettings` | ~100 | Subscribe to settings changes |
| `useSettingsChange` | ~80 | Detect and react to settings file changes |
| `useMainLoopModel` | ~80 | Current model with reactive updates |
| `useAppState` (in state/) | -- | Zustand-like store subscription |
| `useDynamicConfig` | ~100 | Runtime config changes |
| `useCommandQueue` | ~100 | Queued command processing |
| `useQueueProcessor` | ~100 | Process queued user inputs |

**UI/Layout Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `useVirtualScroll` | 721 | Virtual scrolling engine: LRU height cache, overscan (80 rows), scroll quantum (40 rows for perf), pessimistic height estimation, max 300 mounted items, slide-step mounting (25 items/commit) |
| `useTerminalSize` | ~50 | Terminal dimensions subscription |
| `useElapsedTime` | ~40 | Timer for elapsed display |
| `useMinDisplayTime` | ~40 | Minimum display duration enforcement |
| `useBlink` | ~30 | Cursor blink animation |
| `useCopyOnSelect` | ~50 | Copy text on selection |
| `useAfterFirstRender` | ~20 | Post-first-render callback |

**Session/Navigation Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `useReplBridge` | 722 | WebSocket bridge to claude.ai -- bidirectional message sync, inbound message injection, reconnection with failure fuse (max 3 consecutive failures) |
| `useInboxPoller` | 969 | Polls teammate mailbox -- handles permission requests/responses, shutdown, plan approval, mode changes. Skips in-process teammates (they use their own mechanism) |
| `useRemoteSession` | 605 | SSH remote session management |
| `useSSHSession` | 241 | SSH session setup/teardown |
| `useSessionBackgrounding` | 158 | Background session handling (Ctrl+B) |
| `useBackgroundTaskNavigation` | 251 | Navigate between background tasks |
| `useTeleportResume` | ~100 | Resume from session transfer |

**IDE Integration Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `useIDEIntegration` | ~200 | IDE connection lifecycle |
| `useIdeConnectionStatus` | ~80 | IDE connection state |
| `useIdeSelection` | 150 | IDE text selection passthrough |
| `useIdeAtMentioned` | ~50 | IDE @mention integration |
| `useIdeLogging` | ~50 | IDE debug logging |
| `useDiffInIDE` | 379 | Open diffs in IDE |

**Voice Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `useVoice` | 1144 | Hold-to-talk voice input. Uses Anthropic voice_stream STT (WebSocket). Auto-repeat key detection for release. Multi-language support (17 languages). Native macOS audio or SoX recording. |
| `useVoiceIntegration` | 676 | Voice UI integration (status display, keybinding) |
| `useVoiceEnabled` | ~50 | Voice feature availability check |

**Notification Hooks** (`hooks/notifs/`, 17 files):
| Hook | Purpose |
|------|---------|
| `useAutoModeUnavailableNotification` | Auto mode not available warning |
| `useFastModeNotification` | Fast mode status |
| `useIDEStatusIndicator` | IDE connection indicator |
| `useInstallMessages` | Installation guidance |
| `useLspInitializationNotification` | LSP startup status |
| `useMcpConnectivityStatus` | MCP server connectivity |
| `useModelMigrationNotifications` | Model deprecation warnings |
| `usePluginAutoupdateNotification` | Plugin auto-update status |
| `usePluginInstallationStatus` | Plugin install progress |
| `useRateLimitWarningNotification` | Rate limit warnings |
| `useSettingsErrors` | Settings validation errors |
| `useStartupNotification` | Startup messages |
| `useTeammateShutdownNotification` | Teammate shutdown alerts |
| `useDeprecationWarningNotification` | API deprecation |
| `useCanSwitchToExistingSubscription` | Subscription upgrade prompts |
| `useNpmDeprecationNotification` | NPM package deprecation |

**Other Hooks**:
| Hook | Lines | Purpose |
|------|-------|---------|
| `useAssistantHistory` | 250 | Browse assistant message history |
| `useCancelRequest` | 276 | Cancel in-flight API requests |
| `useTasksV2` | 250 | Task list state management |
| `useTaskListWatcher` | 221 | File-watch for task changes |
| `useTurnDiffs` | 213 | File diffs per turn |
| `useLogMessages` | ~100 | Message persistence to JSONL |
| `useFileHistorySnapshotInit` | ~50 | Initialize file history tracking |
| `useMergedTools` | ~100 | Merge built-in + MCP + plugin tools |
| `useMergedCommands` | ~100 | Merge built-in + plugin commands |
| `useMergedClients` | ~100 | Merge MCP clients |
| `useManagePlugins` | 304 | Plugin install/remove/enable/disable |
| `useSkillsChange` | ~50 | Detect skill file changes |
| `useSwarmInitialization` | ~100 | Initialize swarm mode |
| `useScheduledTasks` | ~100 | Cron-scheduled remote agents |
| `useMemoryUsage` | ~50 | Memory save tracking |
| `useDeferredHookMessages` | ~50 | Queue hook messages for display |
| `usePromptSuggestion` | 177 | AI-generated prompt suggestions |
| `useNotifyAfterTimeout` | ~50 | Desktop notification after idle |
| `useUpdateNotification` | ~100 | Version update prompts |
| `useGlobalKeybindings` | 248 | Global shortcuts (Ctrl+T tasks, Ctrl+O transcript, etc.) |
| `useCommandKeybindings` | ~100 | Command-specific shortcuts |
| `useExitOnCtrlCD` | ~50 | Ctrl+C/D exit handling |
| `fileSuggestions` | 811 | File suggestion engine for @mentions (background indexing, fuzzy match) |
| `unifiedSuggestions` | 202 | Combines all suggestion sources |
| `useDirectConnect` | 229 | Direct API connection (bypass proxy) |
| `renderPlaceholder` | ~30 | Placeholder text generation |
| `useAwaySummary` | ~80 | Summary of missed events while away |
| `usePrStatus` | ~100 | GitHub PR status tracking |
| `useIssueFlagBanner` | ~50 | Feature flag issue banner |
| `useMailboxBridge` | ~100 | Bridge mailbox messages to UI |
| `useTeammateViewAutoExit` | ~50 | Auto-exit teammate view |

### 3.2 State Management Pattern

The app uses a **Zustand-like AppState store** (`src/state/AppState.ts`):

```typescript
useAppState(selector)      // Subscribe to specific state slice
useSetAppState()           // Get setter function
useAppStateStore()         // Get raw store reference
```

State flows: Hooks subscribe to AppState via selectors. Tool execution writes results through `setAppState`. Messages are managed as React state in the REPL component and synced to persistence via `useLogMessages`. Permission decisions flow through the `ToolUseConfirm` queue (React state) with resolve-once guards.

### 3.3 Key Architectural Patterns

1. **Feature gating**: `feature('FLAG')` from `bun:bundle` enables compile-time dead code elimination. Hooks conditionally called only after feature check (with biome-ignore comments).

2. **React Compiler**: Components use `_c()` from `react-compiler-runtime` for automatic memoization (the `$[n]` cache slots pattern visible in compiled output).

3. **Resolve-once guards**: Permission handlers use `createResolveOnce()` to ensure exactly one resolution when multiple async paths race (user click vs classifier vs hook).

4. **WeakMap caching**: Message-derived computations (search text, sticky prompt text) cached in WeakMaps keyed on message objects -- self-GC on compaction.

5. **Scroll quantum**: Virtual scroll uses 40-row quantization for `useSyncExternalStore` to prevent React re-renders on every wheel tick while maintaining visual smoothness.

---

## SEAL IDE Relevance Summary

**Directly transferable patterns**:
- Settings cascade (managed -> user -> project -> local) with Zod validation
- Plugin system architecture (marketplace + local, versioned cache, manifest schema)
- Hook lifecycle engine (pre/post tool use, session start/end, file changed)
- Virtual scroll implementation for large message lists
- Permission system (allow/deny/ask rules, classifier-based auto-approval)
- File state cache with LRU eviction and partial-view tracking
- Multi-provider model abstraction (provider configs per model)
- Teammate mailbox for inter-agent communication
- CLAUDE.md @include directive for modular instructions