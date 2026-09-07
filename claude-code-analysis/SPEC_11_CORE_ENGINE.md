# SPEC 11: Core Engine Architecture

> Source: `src/main.tsx` (4,668 lines), `src/query.ts` (1,725 lines), `src/QueryEngine.ts` (1,309 lines)
> These three files form the heart of Claude Code. This spec documents their architecture, flow, state management, and non-obvious behaviors.

---

## 1. Architecture Overview

The three files form a layered pipeline:

```
main.tsx          CLI entrypoint, Commander.js program, startup orchestration
    |
    +---> Interactive path: launchRepl() -> REPL component
    +---> Headless path:    runHeadless() via print.ts
              |
              v
         QueryEngine.ts    Per-conversation session manager (SDK/headless path)
              |
              v
         query.ts           The agentic loop: API calls, tool execution, compaction, recovery
```

**main.tsx** handles everything before the conversation starts: CLI parsing, auth, trust, MCP servers, plugins, tools, permissions, migrations, session resume, and telemetry. It produces a fully configured environment, then hands off to either the interactive REPL or the headless engine.

**QueryEngine.ts** owns per-conversation state: message history, file cache, usage tracking, permission denials. It processes user input, builds the system prompt, calls `query()`, and yields SDK-compatible messages.

**query.ts** is the agentic loop: a `while(true)` generator that calls the model, streams responses, executes tools, manages context compaction, handles recovery from errors, and decides when to continue vs. terminate.

---

## 2. main.tsx — Startup Orchestration

### 2.1 Side Effects at Import Time (Lines 1-20)

Three operations run **before all other imports** via top-level side effects:

1. **`profileCheckpoint('main_tsx_entry')`** — Marks entry for startup profiling
2. **`startMdmRawRead()`** — Fires MDM subprocess reads (plutil/reg query) to run in parallel with ~135ms of remaining imports
3. **`startKeychainPrefetch()`** — Fires macOS keychain reads (OAuth + legacy API key) in parallel

This is a deliberate optimization: module evaluation takes ~135ms, and these I/O operations overlap with that time.

### 2.2 Anti-Debugging Guard (Lines 267-272)

```typescript
if ("external" !== 'ant' && isBeingDebugged()) {
  process.exit(1);
}
```

External builds silently exit if Node inspector or debug flags are detected. Internal (`ant`) builds are exempt. The string `"external"` is a build-time constant replaced during bundling.

### 2.3 Migration System (Lines 327-353)

A versioned migration system (`CURRENT_MIGRATION_VERSION = 11`) runs model string migrations, permission migrations, and config migrations. Migrations are idempotent and only run when the stored version differs from current. Notable migrations include model renames (sonnet -> opus transitions) and permission mode changes.

### 2.4 main() Function (Line 586)

The main entry point that:
1. Sets `NoDefaultCurrentDirectoryInExePath` (Windows security)
2. Installs SIGINT handler (skip for print mode since print.ts has its own)
3. Rewrites `cc://` URLs for Direct Connect
4. Handles deep link URIs (`--handle-uri`)
5. Rewrites `claude assistant [sessionId]` and `claude ssh <host>` from argv
6. Detects interactive vs non-interactive mode
7. Calls `run()`

### 2.5 run() Function — Commander.js Program (Line 885)

Creates the full Commander.js program with ~80 CLI options. The `.action()` handler (starting line 1000) is the core startup path. Key phases:

**Phase 1: Early Configuration (~1000-1300)**
- Bare mode (`--bare` sets `CLAUDE_CODE_SIMPLE=1`)
- KAIROS assistant mode detection
- Permission mode resolution
- MCP config parsing and policy enforcement
- Claude in Chrome setup
- Tool permission context initialization

**Phase 2: Parallel Initialization (~1800-2100)**
- `setup()` runs in parallel with commands/agents loading
- MCP config loading overlaps with setup
- Model resolution happens after setup (trust needed for AWS auth)
- Hooks fire in parallel with MCP connections

**Phase 3: Headless Branch (Lines 2574-2849)**
For `--print` mode:
- Creates a `headlessStore` with `createStore()` 
- Connects MCP servers incrementally into headlessStore
- claude.ai MCP has a 5-second timeout
- Calls `runHeadless()` from `print.ts`

**Phase 4: Interactive Branch (Lines 2850-3791)**
For interactive mode:
- Creates Ink root and render context
- Shows setup screens (trust dialog, onboarding)
- Builds `initialState: AppState` (100+ fields)
- Handles 6 session modes:
  - `--continue`: Resume most recent conversation
  - `--resume <id>`: Resume specific session
  - Direct Connect (`cc://` URL)
  - SSH Remote (`claude ssh`)
  - Assistant mode (`claude assistant`)
  - Teleport/Remote sessions
  - Fresh session (default)
- Calls `launchRepl()` for all interactive paths

### 2.6 Non-Obvious Behaviors

**Settings from --settings flag are content-hashed** (lines 450-458): To avoid busting Anthropic API prompt cache, the temp file path uses a content hash instead of a random UUID. Different processes with identical settings share the same path.

**`startDeferredPrefetches()`** (lines 389-432): Background work that runs after first render. Includes `initUser()`, `getUserContext()`, `getSystemContext()`, file counting, analytics, model capabilities refresh, and settings/skill change detectors. Skipped entirely in `--bare` mode.

**Feature flags via `feature()` from `bun:bundle`**: Dead code elimination gates. Code inside `feature('KAIROS')` blocks is tree-shaken from external builds. This is why many imports use `require()` — so the import itself is conditional.

---

## 3. query.ts — The Agentic Loop

### 3.1 Entry Point: `query()` (Line 217)

```typescript
export async function* query(params: QueryParams): AsyncGenerator<...>
```

A thin wrapper around `queryLoop()` that tracks consumed command UUIDs and notifies their lifecycle on completion. Uses `yield*` delegation.

### 3.2 queryLoop() — The Main Loop (Line 239)

An `AsyncGenerator` with a `while(true)` loop. Each iteration represents one model call + tool execution cycle.

**State Object** (lines 200-215):
```typescript
type State = {
  messages: Message[]
  toolUseContext: ToolUseContext
  autoCompactTracking: AutoCompactTrackingState | undefined
  maxOutputTokensRecoveryCount: number
  hasAttemptedReactiveCompact: boolean
  maxOutputTokensOverride: number | undefined
  pendingToolUseSummary: Promise<ToolUseSummaryMessage | null> | undefined
  stopHookActive: boolean | undefined
  turnCount: number
  transition: Continue | undefined
}
```

State is passed between iterations by assigning `state = next` at each `continue` site. There are **9 distinct `continue` transition reasons**:
1. `next_turn` — Normal: tools ran, need follow-up
2. `reactive_compact_retry` — Prompt-too-long, compacted, retry
3. `collapse_drain_retry` — Context collapse drained staged collapses
4. `max_output_tokens_recovery` — Output truncated, resume message injected
5. `max_output_tokens_escalate` — Retry at 64k tokens instead of 8k default
6. `stop_hook_blocking` — Stop hook detected errors, retry with error messages
7. `token_budget_continuation` — Token budget not exhausted, nudge to continue
8. Model fallback (via `attemptWithFallback` inner loop)
9. Streaming fallback (via `onStreamingFallback` callback)

### 3.3 Per-Iteration Flow

Each iteration of the while loop executes this pipeline:

```
1. Destructure state
2. Start skill discovery prefetch (background)
3. Yield stream_request_start
4. Initialize/increment query chain tracking
5. Get messages after compact boundary
6. Apply tool result budget (content replacement)
7. Apply snip compaction (HISTORY_SNIP feature)
8. Apply microcompact
9. Apply context collapse (CONTEXT_COLLAPSE feature)
10. Run autocompact
11. Prepend system context
12. Check blocking token limit
13. Call model API (streaming)
14. Execute tools (streaming or batch)
15. Handle attachments and memory prefetch
16. Check continuation conditions
17. Either continue or return terminal
```

### 3.4 Model Calling (Lines 651-863)

The model call happens inside a `while (attemptWithFallback)` loop:

```typescript
for await (const message of deps.callModel({...})) {
  // Stream processing
}
```

During streaming:
- **Backfill observable inputs**: Tool inputs are cloned and enriched via `backfillObservableInput()` — the original is left untouched for prompt caching
- **Withholding**: Certain error messages are withheld from yield:
  - Prompt-too-long (for reactive compact recovery)
  - Max output tokens (for truncation recovery)
  - Media size errors (for strip-retry)
- **Streaming tool execution**: When enabled (`StreamingToolExecutor`), tools start executing as soon as their blocks arrive, not after the full response
- **Fallback**: `FallbackTriggeredError` triggers model switch + retry with tombstones for orphaned messages

### 3.5 Tool Execution (Lines 1356-1404)

Two paths:
1. **StreamingToolExecutor**: Tools started during streaming, `getRemainingResults()` collects stragglers
2. **Batch**: `runTools()` processes all tool_use blocks after streaming completes

Both yield `update.message` (tool results) and optionally `update.newContext` (updated ToolUseContext).

### 3.6 Attachments and Memory (Lines 1576-1624)

After tool execution, the loop collects:
- **Queued commands**: Drained from a process-global queue, scoped by agentId
- **Attachment messages**: File change notifications, edited files
- **Memory prefetch**: Relevant memories from a background prefetch started at loop entry
- **Skill discovery**: Prefetched skill suggestions

### 3.7 Tool Use Summaries (Lines 1407-1478)

After each tool batch, a summary is generated asynchronously (via Haiku model, ~1s). The promise is stored in `nextPendingToolUseSummary` and awaited at the start of the **next** iteration — overlapping summary generation with model streaming.

### 3.8 Error Recovery Cascade

When the model returns an error instead of a tool_use, the following cascade executes:

```
1. Is it prompt-too-long?
   a. Try context collapse drain (if not already tried)
   b. Try reactive compact
   c. Surface error

2. Is it media-size error?
   a. Try reactive compact (strips media)
   b. Surface error

3. Is it max_output_tokens?
   a. Try escalation to 64k (once)
   b. Try recovery message (up to 3 times):
      "Output token limit hit. Resume directly — no apology, no recap..."
   c. Surface error

4. Is it any other API error?
   → Skip stop hooks, return completed
```

### 3.9 Stop Hooks (Lines 1263-1302)

After model completes without tool_use, `handleStopHooks()` runs. If hooks return blocking errors, those errors are injected as messages and the loop continues (with `stopHookActive: true`). Hooks can also prevent continuation entirely.

### 3.10 Token Budget (Lines 1304-1351)

Feature-gated (`TOKEN_BUDGET`). A `budgetTracker` monitors cumulative output tokens against `getCurrentTurnTokenBudget()`. When the budget is consumed, a nudge message is injected. Has diminishing returns detection for early stopping.

### 3.11 Task Budget (Lines 190-195, 508-519, 1131-1142)

Separate from token budget. API-side `output_config.task_budget`. Tracked across compaction boundaries — when compact fires, `taskBudgetRemaining` is computed from the final context tokens of the pre-compact messages.

### 3.12 Terminal Conditions

The loop returns `{ reason: string }` for:
- `completed` — No tool_use, no hooks blocking
- `blocking_limit` — Token count at blocking limit (auto-compact off)
- `image_error` — Image size/resize error
- `prompt_too_long` — After recovery attempts exhausted
- `model_error` — callModel threw
- `aborted_streaming` — User interrupt during streaming
- `aborted_tools` — User interrupt during tool execution
- `hook_stopped` — Hook prevented continuation
- `stop_hook_prevented` — Stop hook returned preventContinuation
- `max_turns` — Exceeded maxTurns limit

---

## 4. QueryEngine.ts — Conversation Session Manager

### 4.1 Class Structure

```typescript
class QueryEngine {
  private config: QueryEngineConfig
  private mutableMessages: Message[]
  private abortController: AbortController
  private permissionDenials: SDKPermissionDenial[]
  private totalUsage: NonNullableUsage
  private hasHandledOrphanedPermission: boolean
  private readFileState: FileStateCache
  private discoveredSkillNames: Set<string>
  private loadedNestedMemoryPaths: Set<string>
}
```

One QueryEngine per conversation. Multiple `submitMessage()` calls share state across turns.

### 4.2 submitMessage() (Line 210)

The main method. An async generator that:

1. **Extracts config** — Destructures the extensive QueryEngineConfig
2. **Wraps canUseTool** — Adds permission denial tracking
3. **Resolves model and thinking** — From config, settings, or defaults
4. **Fetches system prompt** — Via `fetchSystemPromptParts()` (async, includes CLAUDE.md discovery)
5. **Builds system prompt** — Composes custom + memory + append prompts
6. **Processes user input** — Via `processUserInput()` (handles slash commands)
7. **Persists transcript** — Records user message before API call for resume safety
8. **Yields system init message** — Tools, model, permissions, agents, skills, plugins
9. **Handles local commands** — If slash command didn't need model query, yield results and return
10. **Calls query()** — The agentic loop
11. **Processes yielded messages** — Switch on message type, record transcripts, track usage
12. **Budget enforcement** — Checks maxBudgetUsd and structured output retry limits
13. **Yields result** — Success or error with full metadata

### 4.3 Message Processing (Lines 771-983)

The `for await (const message of query(...))` loop handles each message type:

- **`assistant`**: Push to mutableMessages, yield via `normalizeMessage()`, fire-and-forget transcript
- **`progress`**: Push and persist inline (prevents resume chain forking)
- **`user`**: Push and yield, increment turnCount
- **`stream_event`**: Track usage (`message_start`, `message_delta`, `message_stop`), optionally yield partials
- **`attachment`**: Handle structured output, max_turns_reached, queued_command replay
- **`system`**: Handle snip boundaries (truncate mutableMessages), compact boundaries (splice pre-compaction messages for GC), API retry notifications
- **`tombstone`**: Skip (control signal for message removal)
- **`tool_use_summary`**: Yield to SDK

### 4.4 Compact Boundary GC (Lines 936-947)

When a compact boundary arrives, QueryEngine **splices all pre-boundary messages** from both `mutableMessages` and the local `messages` array:

```typescript
const mutableBoundaryIdx = this.mutableMessages.length - 1
if (mutableBoundaryIdx > 0) {
  this.mutableMessages.splice(0, mutableBoundaryIdx)
}
```

This is critical for memory management in long sessions — without it, message arrays grow unbounded.

### 4.5 ask() Convenience Function (Line 1200)

A wrapper that creates a one-shot QueryEngine for non-interactive use:

```typescript
export async function* ask({...}): AsyncGenerator<SDKMessage, void, unknown> {
  const engine = new QueryEngine({...})
  try {
    yield* engine.submitMessage(prompt, { uuid, isMeta })
  } finally {
    setReadFileCache(engine.getReadFileState())
  }
}
```

Used by the headless path (`print.ts`) and anywhere a single prompt needs to be run programmatically.

---

## 5. State Management Patterns

### 5.1 Generator-Based Event Stream

All three files use `AsyncGenerator` as the core communication pattern. Messages flow upstream via `yield` and the caller consumes them via `for await`:

```
QueryEngine.submitMessage()   <-- SDK caller
    |  yield SDKMessage
    |
    query()                    <-- agentic loop
        |  yield StreamEvent | Message
        |
        deps.callModel()      <-- API streaming
```

### 5.2 AppState Store

A Zustand-like store pattern via `createStore()`:

```typescript
const store = createStore(initialState, onChangeAppState)
// getState(), setState()
```

Interactive mode: lives in the REPL React component tree.
Headless mode: created in main.tsx as `headlessStore`, passed to `runHeadless()`.

### 5.3 Mutable vs Immutable State

- **`mutableMessages`** (QueryEngine): The canonical message array, mutated across turns
- **`messages`** (query.ts loop-local): Copied from mutableMessages at start, accumulates during loop, assigned to `state.messages` at continue sites
- **`messagesForQuery`** (query.ts per-iteration): A working copy that gets compacted, snipped, collapsed before being sent to the API
- **`state: State`** (query.ts): Immutable-by-convention. Each continue site creates a new object.

### 5.4 ToolUseContext

The central context bag threaded through the entire system:

```typescript
type ToolUseContext = {
  options: { tools, commands, mainLoopModel, thinkingConfig, ... }
  abortController: AbortController
  getAppState: () => AppState
  setAppState: (f: (prev: AppState) => AppState) => void
  messages: Message[]          // Updated per-iteration
  queryTracking: { chainId, depth }
  agentId?: string             // Set for subagents
  readFileState: FileStateCache
  contentReplacementState?: ...
  // ... 20+ more fields
}
```

Reassigned within iterations when tool execution returns `update.newContext`.

---

## 6. Complete Flow: User Input to LLM Response to Tool Execution

### 6.1 Interactive Mode

```
User types in REPL
    -> REPL processes input
    -> Calls query() with messages + toolUseContext
    -> query.ts while(true) loop:
        1. Get messagesAfterCompactBoundary
        2. Apply budget, snip, microcompact, collapse, autocompact
        3. callModel() streams response
        4. Collect assistant message + tool_use blocks
        5. If tool_use blocks exist:
            a. Execute tools (streaming or batch)
            b. Collect tool results
            c. Gather attachments (file changes, memory, skills, queued commands)
            d. Set state.messages = [...messages, ...assistant, ...toolResults]
            e. Continue loop
        6. If no tool_use:
            a. Run stop hooks
            b. Check token budget
            c. Return terminal { reason: 'completed' }
    -> REPL receives yielded messages
    -> Updates UI, persists transcript
```

### 6.2 Headless/SDK Mode

```
SDK caller calls ask() or engine.submitMessage()
    -> QueryEngine processes user input
    -> Builds system prompt + user context
    -> Calls query() 
    -> Same loop as above
    -> QueryEngine receives yielded messages:
        - assistant: yield as SDKMessage, persist transcript
        - user: yield as SDKMessage, track turnCount
        - stream_event: accumulate usage, optionally yield partials
        - attachment: handle structured output, max_turns, budget
        - system: handle compact boundaries (GC), API retries
    -> Yield final result message with:
        - duration_ms, duration_api_ms
        - num_turns
        - result text
        - stop_reason
        - total_cost_usd
        - usage, modelUsage
        - permission_denials
        - structured_output (if json_schema)
```

---

## 7. Hidden Features and Non-Obvious Behavior

### 7.1 Thinking Block Rules (query.ts Lines 149-161)

Comments document "The Rules of Thinking" (written as wizard lore):
1. Messages with thinking/redacted_thinking blocks require `max_thinking_length > 0`
2. Thinking blocks cannot be the last block in a message
3. Thinking blocks must be preserved for the entire assistant trajectory

Violating these causes "an entire day of debugging."

### 7.2 Streaming Tool Execution

When `config.gates.streamingToolExecution` is true, tools begin executing as their `tool_use` blocks arrive from the stream — not after the full response. This is a significant latency optimization: tool results are ready sooner, reducing turn time.

If the stream fails mid-way (fallback triggered), the executor is discarded and recreated.

### 7.3 Memory Prefetch (query.ts Lines 298-302)

At the start of each turn, `startRelevantMemoryPrefetch()` fires a side-query to find relevant memories. This runs during the model's streaming time (5-30s). The result is consumed after tool execution via a zero-wait poll (`settledAt !== null`). If not settled, it retries on the next iteration.

Uses the `using` keyword (TC39 Explicit Resource Management) for cleanup.

### 7.4 Skill Discovery Prefetch

Similarly, `startSkillDiscoveryPrefetch()` runs during streaming to find relevant skills. The result is injected as attachment messages for the next iteration.

### 7.5 Command Queue Mid-Turn Drain (query.ts Lines 1543-1574)

A process-global command queue allows external inputs during a turn. Commands are drained between tool execution and the next API call, scoped by agent ID:
- Main thread drains `agentId === undefined`
- Subagents drain `agentId === currentAgentId`
- Slash commands are excluded
- Sleep tool running drains `'later'` priority commands

### 7.6 Fallback Tombstoning (query.ts Lines 712-739)

When a streaming fallback occurs, all partial assistant messages become orphans. These are yielded as `tombstone` messages so they're removed from the UI and transcript. Without this, thinking blocks from the failed attempt would cause "thinking blocks cannot be modified" API errors.

### 7.7 Content Replacement for Cache Efficiency (query.ts Lines 370-398)

`applyToolResultBudget()` replaces oversized tool results with summaries. Crucially, this runs BEFORE microcompact, and the replacements are persisted only for resumable query sources (`agent:*`, `repl_main_thread*`).

### 7.8 Build-Time String Elimination

The pattern `"external" === 'ant'` is a build-time constant. In external builds, this evaluates to `false` and the entire block is tree-shaken. Many internal-only features (ccshare, session data uploader, heap dump monitor) use this gate.

### 7.9 Prompt Cache Preservation

Tool input backfilling (lines 746-786) explicitly avoids mutating the original message object. Instead, it creates clones only when new fields are added — not when existing fields are overwritten. This preserves byte-level message identity for API prompt caching.

### 7.10 Proactive Mode

When `--proactive` is set, a system prompt addendum tells Claude to "take initiative" and use Sleep between actions. The loop handles periodic `<tick>` prompts. Coordinator mode explicitly disables this (conflicting instructions).

### 7.11 Multi-Layer Compaction

The system has 5 independent context reduction mechanisms, applied in order:
1. **Tool Result Budget** — Replaces oversized tool outputs
2. **Snip Compaction** — Removes old conversation segments (HISTORY_SNIP)
3. **Microcompact** — Fine-grained tool result compression
4. **Context Collapse** — Staged collapse of conversation segments
5. **Autocompact** — Full conversation summary when approaching limit

These compose: snip can free enough tokens that autocompact becomes unnecessary, preserving granular context.

---

## 8. Dependencies Between Files

```
main.tsx
  ├── imports: QueryEngine (for headless via print.ts)
  ├── imports: query (indirectly, via REPL's query path)
  ├── produces: AppState, tools, commands, mcpClients, permissions
  └── delegates to: launchRepl() or runHeadless()

QueryEngine.ts
  ├── imports: query() from query.ts
  ├── imports: processUserInput, fetchSystemPromptParts
  ├── owns: mutableMessages, usage, permissionDenials
  └── yields: SDKMessage to external callers

query.ts
  ├── imports: deps (callModel, autocompact, microcompact, uuid)
  ├── imports: StreamingToolExecutor, runTools
  ├── imports: handleStopHooks, buildQueryConfig
  ├── owns: per-turn State, budgetTracker
  └── yields: StreamEvent | Message to QueryEngine
```

---

## 9. Key Type Definitions

### QueryParams (query.ts)
```typescript
{
  messages, systemPrompt, userContext, systemContext,
  canUseTool, toolUseContext, fallbackModel, querySource,
  maxOutputTokensOverride, maxTurns, skipCacheWrite,
  taskBudget: { total: number },
  deps: QueryDeps
}
```

### QueryEngineConfig (QueryEngine.ts)
```typescript
{
  cwd, tools, commands, mcpClients, agents, canUseTool,
  getAppState, setAppState, initialMessages, readFileCache,
  customSystemPrompt, appendSystemPrompt, userSpecifiedModel,
  fallbackModel, thinkingConfig, maxTurns, maxBudgetUsd,
  taskBudget, jsonSchema, verbose, replayUserMessages,
  handleElicitation, includePartialMessages, setSDKStatus,
  abortController, orphanedPermission, snipReplay
}
```

### Terminal (query.ts return type)
```typescript
{ reason: string; error?: Error; turnCount?: number }
```

---

## 10. Performance Characteristics

- **Startup**: ~135ms module evaluation, parallelized with MDM/keychain prefetch
- **First render**: Deferred prefetches run after, not before
- **Print mode skip**: 52 subcommand registrations skipped (~65ms saved)
- **MCP timeout**: claude.ai servers get 5s max, background connect continues
- **Tool summary**: Generated async via Haiku (~1s), overlapped with next model call
- **Streaming tools**: Start executing during model response, not after
- **Memory prefetch**: Side-query during model streaming (5-30s window)
- **Transcript writes**: Fire-and-forget for assistant messages, await for user/compact
- **GC**: Compact boundaries trigger splice of pre-boundary messages
