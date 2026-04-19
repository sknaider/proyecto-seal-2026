# SPEC: Claude Code Compaction System — Complete Reverse Engineering

> Source: `open-claude-code-gitlab/src/services/compact/`
> Date: 2026-04-10
> Purpose: Full architecture extraction for SEAL session adaptation (8-12h sessions)

---

## 1. Architecture Overview — 5-Tier System

The compaction system is NOT 4 tiers — it is **5 tiers** (plus a 6th experimental API-native tier). Each tier operates at a different level of urgency and invasiveness:

```
Tier 0: API Microcompact (apiMicrocompact.ts)
   ↓  Server-side context_management edits — clear_tool_uses + clear_thinking
   ↓  Runs transparently at API level, no local message mutation
   
Tier 1: Cached Microcompact (cachedMicrocompact.ts + microCompact.ts)
   ↓  Cache-editing: delete old tool results via cache_edits API
   ↓  Does NOT mutate local messages — edits happen server-side
   ↓  Trigger: count-based (GrowthBook config)
   
Tier 2: Time-Based Microcompact (microCompact.ts)
   ↓  Content-clear old tool results when idle gap > threshold
   ↓  MUTATES local message content (replaces with placeholder)
   ↓  Trigger: >60min since last assistant message (cache expired)
   
Tier 3: Session Memory Compact (sessionMemoryCompact.ts)
   ↓  Replace old messages with session memory markdown file
   ↓  Keeps recent messages verbatim (10K-40K tokens)
   ↓  Trigger: autocompact threshold reached + session memory available
   
Tier 4: Full LLM Compact (compact.ts)
   ↓  Fork agent to summarize entire conversation
   ↓  9-section structured summary via LLM call
   ↓  Post-compact: restore files, skills, plans, hooks
   ↓  Trigger: autocompact threshold reached + no session memory
   
Tier 5: Reactive Compact (reactiveCompact.ts — feature-gated)
   ↓  Triggered by API prompt_too_long error
   ↓  Progressive head-truncation + retry loop
   ↓  Last resort when proactive compact fails
```

### Decision Tree: When Does Each Tier Fire?

```
On every API request (pre-request):
  ├─ evaluateTimeBasedTrigger() fires?
  │   └─ YES → Tier 2 (time-based MC) → short-circuit, skip other tiers
  │
  ├─ CACHED_MICROCOMPACT feature enabled + supported model + main thread?
  │   └─ YES → Tier 1 (cached MC) → queue cache_edits for API layer
  │
  └─ Neither → no pre-request compaction
  
On every API request (via context_management param):
  └─ Tier 0 (API MC) → always sent if thinking is active

After API response (query loop):
  ├─ shouldAutoCompact() → tokens > threshold?
  │   ├─ NO → continue normally
  │   └─ YES → autoCompactIfNeeded():
  │       ├─ trySessionMemoryCompaction() succeeds?
  │       │   └─ YES → Tier 3 (SM compact) → done
  │       └─ NO → compactConversation() → Tier 4 (full LLM compact)
  │
  └─ On prompt_too_long API error:
      └─ Tier 5 (reactive compact) → progressive truncation + retry

Manual /compact command:
  ├─ trySessionMemoryCompaction() first (if no custom instructions)
  │   └─ YES → Tier 3
  ├─ reactiveCompact.isReactiveOnlyMode()?
  │   └─ YES → route through reactive path
  └─ Default → microcompact → Tier 4 (full LLM)
```

---

## 2. Tier 0: API Microcompact (`apiMicrocompact.ts`)

Server-side context management strategies sent as part of the API request.

### Strategy Types

```typescript
type ContextEditStrategy =
  | {
      type: 'clear_tool_uses_20250919'
      trigger?: { type: 'input_tokens'; value: number }
      keep?: { type: 'tool_uses'; value: number }
      clear_tool_inputs?: boolean | string[]
      exclude_tools?: string[]
      clear_at_least?: { type: 'input_tokens'; value: number }
    }
  | {
      type: 'clear_thinking_20251015'
      keep: { type: 'thinking_turns'; value: number } | 'all'
    }
```

### Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `DEFAULT_MAX_INPUT_TOKENS` | 180,000 | Trigger threshold for tool clearing |
| `DEFAULT_TARGET_INPUT_TOKENS` | 40,000 | Keep last 40K tokens |

### Tools with Clearable Results

```
Bash (all shell tools), Glob, Grep, FileRead, WebFetch, WebSearch
```

### Tools with Clearable Uses (input cleared, not output)

```
FileEdit, FileWrite, NotebookEdit
```

### Thinking Strategy

- When thinking is active and NOT redacted: `keep: 'all'` (preserve all thinking)
- When `clearAllThinking` (>1h idle): `keep: { type: 'thinking_turns', value: 1 }` (only last turn)
- When redacted thinking: strategy skipped entirely

### Gating

- Tool clearing strategies: `ant` users only (env: `USE_API_CLEAR_TOOL_RESULTS`, `USE_API_CLEAR_TOOL_USES`)
- Thinking clearing: all users

---

## 3. Tier 1: Cached Microcompact (`microCompact.ts` + `cachedMicrocompact.ts`)

Uses the API's cache-editing mechanism to delete old tool results without invalidating the cached prefix.

### How It Works

1. On each request, `microcompactMessages()` is called
2. Walks all messages collecting tool_use IDs from assistant messages where tool name is in COMPACTABLE_TOOLS
3. Registers tool results grouped by user message in `CachedMCState`
4. Calls `getToolResultsToDelete(state)` using GrowthBook-configured thresholds
5. Creates `cache_edits` block queued for API layer (via `consumePendingCacheEdits()`)
6. Local messages are NOT modified — edits happen server-side

### Compactable Tools

```typescript
const COMPACTABLE_TOOLS = new Set([
  'Read',           // FILE_READ_TOOL_NAME
  'Bash',           // SHELL_TOOL_NAMES (all shell variants)
  'Grep',
  'Glob',
  'WebSearch',
  'WebFetch',
  'Edit',           // FILE_EDIT_TOOL_NAME
  'Write',          // FILE_WRITE_TOOL_NAME
])
```

### Key Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `IMAGE_MAX_TOKEN_SIZE` | 2,000 | Token estimate for any image/document block |
| Token estimation padding | x 4/3 (1.33x) | Conservative padding on rough estimates |

### Gating

- Feature flag: `CACHED_MICROCOMPACT` (build-time)
- Model must support cache editing (`isModelSupportedForCacheEditing`)
- Main thread only (prevents sub-agents from corrupting main thread state)
- GrowthBook: `triggerThreshold` and `keepRecent` from config

---

## 4. Tier 2: Time-Based Microcompact (`microCompact.ts` + `timeBasedMCConfig.ts`)

Content-clears old tool results when the user has been idle long enough that the server cache has expired.

### Trigger Logic

```typescript
function evaluateTimeBasedTrigger(messages, querySource):
  1. Read config from GrowthBook ('tengu_slate_heron')
  2. Require explicit main-thread querySource (not undefined)
  3. Find last assistant message
  4. Calculate gap = (now - lastAssistant.timestamp) / 60_000
  5. If gap >= config.gapThresholdMinutes → FIRE
```

### Action

```typescript
function maybeTimeBasedMicrocompact(messages, querySource):
  1. Collect all compactable tool IDs
  2. keepRecent = max(1, config.keepRecent)  // Always keep at least 1
  3. keepSet = last N compactable IDs
  4. clearSet = everything else
  5. For each tool_result in clearSet:
     → Replace content with '[Old tool result content cleared]'
  6. Reset cached MC state (stale after content mutation)
```

### Configuration (GrowthBook: `tengu_slate_heron`)

```typescript
const TIME_BASED_MC_CONFIG_DEFAULTS: TimeBasedMCConfig = {
  enabled: false,                // Master switch (default: OFF)
  gapThresholdMinutes: 60,       // 1 hour (matches server cache TTL)
  keepRecent: 5,                 // Keep last 5 tool results
}
```

### Key Detail

The placeholder message is:
```
'[Old tool result content cleared]'
```
This constant is `TIME_BASED_MC_CLEARED_MESSAGE` — exported and used by both time-based MC and the tool result storage system.

---

## 5. Tier 3: Session Memory Compact (`sessionMemoryCompact.ts`)

Replaces old messages with the session memory markdown file (maintained by a background sub-agent).

### Prerequisites

- Feature flags: `tengu_session_memory` AND `tengu_sm_compact` (both must be true)
- Session memory file exists and is NOT empty (not just the template)
- Env overrides: `ENABLE_CLAUDE_CODE_SM_COMPACT` / `DISABLE_CLAUDE_CODE_SM_COMPACT`

### Configuration (GrowthBook: `tengu_sm_compact_config`)

```typescript
const DEFAULT_SM_COMPACT_CONFIG: SessionMemoryCompactConfig = {
  minTokens: 10_000,           // Min tokens to preserve after compaction
  minTextBlockMessages: 5,     // Min messages with text to keep
  maxTokens: 40_000,           // Hard cap on preserved tokens
}
```

### Message Preservation Algorithm

```typescript
function calculateMessagesToKeepIndex(messages, lastSummarizedIndex):
  1. Start from message after lastSummarizedIndex
  2. Calculate tokens and text-block count from startIndex to end
  3. If already >= maxTokens → stop (hard cap)
  4. If already >= minTokens AND >= minTextBlockMessages → stop
  5. Otherwise, expand backwards:
     - Floor at last compact boundary (never cross it)
     - Add messages until both minimums met OR maxTokens hit
  6. adjustIndexToPreserveAPIInvariants():
     - Ensure no orphan tool_result (include matching tool_use)
     - Ensure thinking blocks with same message.id are included
```

### Session Memory File Structure

The session memory is a structured markdown file updated by a background sub-agent:

```markdown
# Session Title
_5-10 word descriptive title_

# Current State
_What is actively being worked on right now?_

# Task specification
_What did the user ask to build?_

# Files and Functions
_Important files and their relevance_

# Workflow
_Bash commands usually run and in what order_

# Errors & Corrections
_Errors encountered and fixes applied_

# Codebase and System Documentation
_Important system components_

# Learnings
_What worked, what didn't, what to avoid_

# Key results
_Exact outputs the user requested_

# Worklog
_Step by step terse summary_
```

### Session Memory Limits

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_SECTION_LENGTH` | 2,000 tokens | Per-section cap |
| `MAX_TOTAL_SESSION_MEMORY_TOKENS` | 12,000 tokens | Total file cap |

### Truncation for Compact

When inserting session memory into the compact summary, sections exceeding `MAX_SECTION_LENGTH * 4` chars are truncated at line boundaries with `[... section truncated for length ...]` marker.

### Session Memory Update Triggers

```typescript
const DEFAULT_SESSION_MEMORY_CONFIG: SessionMemoryConfig = {
  minimumMessageTokensToInit: 10_000,    // Min tokens before first extraction
  minimumTokensBetweenUpdate: 5_000,     // Min growth between updates
  toolCallsBetweenUpdates: 3,            // Min tool calls between updates
}
```

---

## 6. Tier 4: Full LLM Compact (`compact.ts` + `prompt.ts`)

The most invasive tier — forks a sub-agent to summarize the entire conversation.

### The Compaction Prompt

There are THREE prompt variants:

#### A. Full Compact (BASE_COMPACT_PROMPT)

Used for full conversation compaction. 9 sections:

```
1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections (with code snippets)
4. Errors and fixes (with user feedback)
5. Problem Solving
6. All user messages (non-tool-result)
7. Pending Tasks
8. Current Work (most recent, with file names and code)
9. Optional Next Step (with direct quotes from conversation)
```

#### B. Partial Compact — Direction "from" (PARTIAL_COMPACT_PROMPT)

Summarizes only RECENT messages (after a preserved prefix). Same 9 sections but scoped to "recent messages only."

#### C. Partial Compact — Direction "up_to" (PARTIAL_COMPACT_UP_TO_PROMPT)

Summarizes messages BEFORE a pivot point. Section 8 becomes "Work Completed" and section 9 becomes "Context for Continuing Work."

#### Prompt Wrapping

Every prompt is wrapped with:

```
PREAMBLE (NO_TOOLS_PREAMBLE):
"CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.
- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- Tool calls will be REJECTED and will waste your only turn..."

ANALYSIS INSTRUCTION:
"Before providing your final summary, wrap your analysis in <analysis> tags..."
(Chronological analysis of each message section)

TRAILER (NO_TOOLS_TRAILER):
"REMINDER: Do NOT call any tools. Respond with plain text only —
an <analysis> block followed by a <summary> block."
```

#### Summary Post-Processing (`formatCompactSummary`)

1. Strip `<analysis>...</analysis>` block (drafting scratchpad, not preserved)
2. Extract `<summary>...</summary>` content
3. Replace tags with `Summary:\n` header
4. Clean up extra whitespace

#### User Summary Message Wrapper (`getCompactUserSummaryMessage`)

```
"This session is being continued from a previous conversation that ran out of context.
The summary below covers the earlier portion of the conversation.

[formatted summary]

If you need specific details from before compaction... read the full transcript at: [path]

[If SM compact]: Recent messages are preserved verbatim.

[If autocompact]: Continue the conversation from where it left off without asking
the user any further questions. Resume directly — do not acknowledge the summary..."
```

### Streaming the Summary

Two paths:

1. **Forked Agent (prompt cache sharing)** — reuses main conversation's cached prefix
   - `runForkedAgent()` with `maxTurns: 1`, `skipCacheWrite: true`
   - System prompt: "You are a helpful AI assistant tasked with summarizing conversations."
   - Thinking: disabled
   - Max output: `min(COMPACT_MAX_OUTPUT_TOKENS, model_max_output)`
   - On failure → fall through to streaming path

2. **Direct Streaming** — standalone API call
   - Up to `MAX_COMPACT_STREAMING_RETRIES = 2` retries
   - Tools included: FileReadTool + ToolSearchTool (if enabled) + MCP tools
   - All tool calls denied via `createCompactCanUseTool()`

### Prompt-Too-Long Retry (`truncateHeadForPTLRetry`)

When the compact request itself is too large:

```typescript
const MAX_PTL_RETRIES = 3

function truncateHeadForPTLRetry(messages, ptlResponse):
  1. Group messages by API round (groupMessagesByApiRound)
  2. Parse token gap from error response
  3. If gap parseable: drop groups until gap covered
  4. If unparseable: drop 20% of groups
  5. Keep at least 1 group
  6. If first remaining message is assistant: prepend synthetic user message
```

### Post-Compact Restoration

After the LLM generates a summary, these are restored/re-injected:

#### A. File Attachments

```typescript
POST_COMPACT_MAX_FILES_TO_RESTORE = 5
POST_COMPACT_TOKEN_BUDGET = 50_000
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000
```

- Sort recently-read files by timestamp (most recent first)
- Exclude files already in preserved messages (dedup)
- Re-read via FileReadTool (get fresh content)
- Stop when token budget exhausted

#### B. Skill Attachments

```typescript
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000
```

- Sort invoked skills by `invokedAt` (most recent first)
- Truncate each skill to per-skill cap
- Stop when total budget exhausted
- `sentSkillNames` NOT reset (avoids re-injecting full skill_listing ~4K tokens)

#### C. Plan Attachments

- If a plan file exists → create plan_file_reference attachment
- If in plan mode → create plan_mode attachment with full instructions

#### D. Delta Attachments (deferred tools, agents, MCP instructions)

- Re-announce all deferred tools, agent listings, MCP instructions
- For full compact: diff against empty (announce everything)
- For partial: diff against messagesToKeep (announce only what was summarized away)

#### E. Session Start Hooks

`processSessionStartHooks('compact')` — re-runs hooks that inject CLAUDE.md and other context.

#### F. Post-Compact Hooks

`executePostCompactHooks()` — user-defined hooks that run after compaction.

---

## 7. AutoCompact Trigger System (`autoCompact.ts`)

### Constants

```typescript
const MAX_OUTPUT_TOKENS_FOR_SUMMARY = 20_000
const AUTOCOMPACT_BUFFER_TOKENS = 13_000
const WARNING_THRESHOLD_BUFFER_TOKENS = 20_000
const ERROR_THRESHOLD_BUFFER_TOKENS = 20_000
const MANUAL_COMPACT_BUFFER_TOKENS = 3_000
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
```

### Effective Context Window

```typescript
function getEffectiveContextWindowSize(model):
  contextWindow = getContextWindowForModel(model)  // Usually 200K or 1M
  
  // Allow override via CLAUDE_CODE_AUTO_COMPACT_WINDOW
  if env.CLAUDE_CODE_AUTO_COMPACT_WINDOW:
    contextWindow = min(contextWindow, parsed)
  
  return contextWindow - min(getMaxOutputTokensForModel(model), 20_000)
```

### Threshold Calculation

```typescript
function getAutoCompactThreshold(model):
  effective = getEffectiveContextWindowSize(model)
  threshold = effective - 13_000  // AUTOCOMPACT_BUFFER_TOKENS
  
  // Override via CLAUDE_AUTOCOMPACT_PCT_OVERRIDE (e.g., "80" = 80%)
  if env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE:
    percentThreshold = floor(effective * (pct / 100))
    return min(percentThreshold, threshold)
  
  return threshold
```

### Example: 200K Context Window

```
Context window:           200,000
Max output reservation:   -20,000 (min of model max and 20K)
Effective window:         180,000
AutoCompact buffer:       -13,000
═══════════════════════════════════
AutoCompact threshold:    167,000 tokens

Warning threshold:  167,000 - 20,000 = 147,000
Error threshold:    167,000 - 20,000 = 147,000
Blocking limit:     180,000 - 3,000  = 177,000
```

### Disabling AutoCompact

- `DISABLE_COMPACT=1` — disables ALL compaction
- `DISABLE_AUTO_COMPACT=1` — disables only auto (keeps manual /compact)
- Settings: `autoCompactEnabled: false`
- Feature flags: `tengu_cobalt_raccoon` (reactive-only mode)
- Context collapse enabled → autocompact suppressed (collapse owns headroom)

### Circuit Breaker

After 3 consecutive autocompact failures, stops retrying for the session. Prevents ~250K wasted API calls/day globally (BQ 2026-03-10 data).

### Token Warning States

```typescript
function calculateTokenWarningState(tokenUsage, model):
  threshold = isAutoCompactEnabled() ? autoCompactThreshold : effectiveWindow
  
  percentLeft = max(0, round(((threshold - tokenUsage) / threshold) * 100))
  isAboveWarningThreshold = tokenUsage >= threshold - 20_000
  isAboveErrorThreshold = tokenUsage >= threshold - 20_000
  isAboveAutoCompactThreshold = autoCompact && tokenUsage >= threshold
  isAtBlockingLimit = tokenUsage >= effectiveWindow - 3_000
```

---

## 8. Post-Compact Cleanup (`postCompactCleanup.ts`)

Runs after ALL compaction paths (auto, manual, session memory):

```typescript
function runPostCompactCleanup(querySource?):
  1. resetMicrocompactState()     — clear cached MC tool tracking
  2. resetContextCollapse()       — if CONTEXT_COLLAPSE feature, main thread only
  3. getUserContext.cache.clear()  — force re-read of CLAUDE.md on next turn
  4. resetGetMemoryFilesCache()   — re-arm InstructionsLoaded hook
  5. clearSystemPromptSections()
  6. clearClassifierApprovals()
  7. clearSpeculativeChecks()
  8. clearBetaTracingState()
  9. sweepFileContentCache()      — if COMMIT_ATTRIBUTION feature
  10. clearSessionMessagesCache()
  
  NOT cleared:
  - sentSkillNames (skills survive across compactions)
  
  Sub-agent safety:
  - Steps 2-4 only run for main-thread compacts (sub-agents share module state)
```

---

## 9. Compact Boundary Messages

Every compaction creates a `SystemCompactBoundaryMessage` containing:

```typescript
{
  type: 'system',
  subtype: 'compact_boundary',
  compactMetadata: {
    trigger: 'auto' | 'manual',
    preCompactTokenCount: number,
    preCompactDiscoveredTools?: string[],   // Loaded deferred tools to carry forward
    preservedSegment?: {                    // For message-preserving compaction
      headUuid: UUID,   // First preserved message
      anchorUuid: UUID, // What sits before head in chain
      tailUuid: UUID,   // Last preserved message
    }
  }
}
```

---

## 10. Message Grouping for Compact (`grouping.ts`)

```typescript
function groupMessagesByApiRound(messages): Message[][]
  // Groups at API-round boundaries (new assistant message.id)
  // Each group = one complete API round-trip
  // Used by: PTL retry (drop oldest groups) + reactive compact
```

---

## 11. Token Estimation

```typescript
function estimateMessageTokens(messages):
  For each message (user/assistant only):
    - text blocks: roughTokenCountEstimation(text)
    - tool_result: sum of text content + 2000 per image/doc
    - thinking: roughTokenCountEstimation(thinking text only)
    - redacted_thinking: roughTokenCountEstimation(data)
    - tool_use: roughTokenCountEstimation(name + JSON(input))
    - other: roughTokenCountEstimation(JSON(block))
  
  Return total * 4/3  // 33% padding for conservative estimate

function roughTokenCountEstimation(text):
  return text.length / 4  // ~4 chars per token heuristic
```

---

## 12. Complete Constants Reference

### Core Thresholds

| Constant | Value | File | Purpose |
|----------|-------|------|---------|
| `AUTOCOMPACT_BUFFER_TOKENS` | 13,000 | autoCompact.ts | Buffer before effective window for auto-trigger |
| `WARNING_THRESHOLD_BUFFER_TOKENS` | 20,000 | autoCompact.ts | Warning UI starts |
| `ERROR_THRESHOLD_BUFFER_TOKENS` | 20,000 | autoCompact.ts | Error UI starts |
| `MANUAL_COMPACT_BUFFER_TOKENS` | 3,000 | autoCompact.ts | Blocking limit = effective - this |
| `MAX_OUTPUT_TOKENS_FOR_SUMMARY` | 20,000 | autoCompact.ts | Reserved for compact summary output |
| `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES` | 3 | autoCompact.ts | Circuit breaker |
| `COMPACT_MAX_OUTPUT_TOKENS` | 20,000 | context.ts | Max output for compact API call |
| `MODEL_CONTEXT_WINDOW_DEFAULT` | 200,000 | context.ts | Default context window |

### Post-Compact Restoration

| Constant | Value | File | Purpose |
|----------|-------|------|---------|
| `POST_COMPACT_MAX_FILES_TO_RESTORE` | 5 | compact.ts | Max files re-read post-compact |
| `POST_COMPACT_TOKEN_BUDGET` | 50,000 | compact.ts | Total budget for file attachments |
| `POST_COMPACT_MAX_TOKENS_PER_FILE` | 5,000 | compact.ts | Per-file token cap |
| `POST_COMPACT_MAX_TOKENS_PER_SKILL` | 5,000 | compact.ts | Per-skill token cap |
| `POST_COMPACT_SKILLS_TOKEN_BUDGET` | 25,000 | compact.ts | Total budget for skills |

### Session Memory

| Constant | Value | File | Purpose |
|----------|-------|------|---------|
| `DEFAULT_SM_COMPACT_CONFIG.minTokens` | 10,000 | sessionMemoryCompact.ts | Min tokens to keep |
| `DEFAULT_SM_COMPACT_CONFIG.minTextBlockMessages` | 5 | sessionMemoryCompact.ts | Min text messages to keep |
| `DEFAULT_SM_COMPACT_CONFIG.maxTokens` | 40,000 | sessionMemoryCompact.ts | Max tokens to keep |
| `MAX_SECTION_LENGTH` | 2,000 | prompts.ts | Per-section token limit |
| `MAX_TOTAL_SESSION_MEMORY_TOKENS` | 12,000 | prompts.ts | Total session memory file limit |
| `minimumMessageTokensToInit` | 10,000 | sessionMemoryUtils.ts | Min before first extraction |
| `minimumTokensBetweenUpdate` | 5,000 | sessionMemoryUtils.ts | Min growth between updates |
| `toolCallsBetweenUpdates` | 3 | sessionMemoryUtils.ts | Min tool calls between updates |

### Microcompact

| Constant | Value | File | Purpose |
|----------|-------|------|---------|
| `IMAGE_MAX_TOKEN_SIZE` | 2,000 | microCompact.ts | Token estimate per image |
| `TIME_BASED_MC_CLEARED_MESSAGE` | `[Old tool result content cleared]` | microCompact.ts | Replacement text |
| `gapThresholdMinutes` (default) | 60 | timeBasedMCConfig.ts | Idle gap before time-based MC |
| `keepRecent` (default) | 5 | timeBasedMCConfig.ts | Recent tool results to preserve |

### API Microcompact

| Constant | Value | File | Purpose |
|----------|-------|------|---------|
| `DEFAULT_MAX_INPUT_TOKENS` | 180,000 | apiMicrocompact.ts | Trigger threshold |
| `DEFAULT_TARGET_INPUT_TOKENS` | 40,000 | apiMicrocompact.ts | Target after clearing |

### Tool Result Limits

| Constant | Value | File | Purpose |
|----------|-------|------|---------|
| `DEFAULT_MAX_RESULT_SIZE_CHARS` | 50,000 | toolLimits.ts | Per-tool result char cap |
| `MAX_TOOL_RESULT_TOKENS` | 100,000 | toolLimits.ts | Per-tool result token cap |
| `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS` | 200,000 | toolLimits.ts | Per-message aggregate cap |

### Retry Limits

| Constant | Value | File | Purpose |
|----------|-------|------|---------|
| `MAX_PTL_RETRIES` | 3 | compact.ts | Max prompt-too-long retries |
| `MAX_COMPACT_STREAMING_RETRIES` | 2 | compact.ts | Max streaming retries |
| `EXTRACTION_WAIT_TIMEOUT_MS` | 15,000 | sessionMemoryUtils.ts | Wait for SM extraction |
| `EXTRACTION_STALE_THRESHOLD_MS` | 60,000 | sessionMemoryUtils.ts | Stale extraction cutoff |

---

## 13. Environment Variable Overrides

| Variable | Effect |
|----------|--------|
| `DISABLE_COMPACT` | Disable ALL compaction |
| `DISABLE_AUTO_COMPACT` | Disable auto-compact only |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | Override context window for autocompact |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | Percentage threshold (e.g., "80") |
| `CLAUDE_CODE_BLOCKING_LIMIT_OVERRIDE` | Override blocking limit |
| `ENABLE_CLAUDE_CODE_SM_COMPACT` | Force-enable session memory compact |
| `DISABLE_CLAUDE_CODE_SM_COMPACT` | Force-disable session memory compact |
| `USE_API_CLEAR_TOOL_RESULTS` | Enable API-level tool result clearing |
| `USE_API_CLEAR_TOOL_USES` | Enable API-level tool use clearing |
| `API_MAX_INPUT_TOKENS` | Override API MC trigger threshold |
| `API_TARGET_INPUT_TOKENS` | Override API MC target |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | Override context window (ant-only) |
| `CLAUDE_CODE_DISABLE_1M_CONTEXT` | Disable 1M context (HIPAA) |

---

## 14. Adaptation Plan for SEAL Sessions (8-12h)

### Problem

SEAL sessions (ADA/JARVIS) run 8-12 hours with heavy tool use (MCP memory calls, bash, file reads). Claude Code's compaction was designed for interactive developer sessions of ~30-60 minutes. Key issues:

1. **Time-based MC fires immediately** after every idle gap >60min (William steps away)
2. **AutoCompact threshold** at ~167K tokens is hit every ~45 min of active tool use
3. **Session memory extraction** runs as a sub-agent consuming additional API tokens
4. **Post-compact file restoration** wastes budget on files irrelevant to SEAL
5. **Compact Instructions in CLAUDE.md** are not being leveraged

### Recommended Configuration

#### A. Environment Variables (set in agent launch scripts)

```bash
# Increase effective context window to delay autocompact
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=180000

# Or use percentage threshold — compact at 90% instead of ~93%
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90

# Disable session memory compact (SEAL has its own memory via MCP)
export DISABLE_CLAUDE_CODE_SM_COMPACT=1
```

#### B. Compact Instructions in CLAUDE.md

Add to the project CLAUDE.md:

```markdown
## Compact Instructions

When summarizing this conversation, preserve in priority order:
1. Agent identity (ADA or JARVIS), current OCEAN state, emotional context
2. All TaskList items with IDs and status
3. Active technical decisions: model paths, GPU locks, service states
4. Unprocessed messages from other agents
5. Current SEAL service states: PostgreSQL:5433, Neo4j:7687, Qdrant:6333
6. Active error investigations and diagnoses
7. MCP tool call history (boot_context, memory_store, task operations)

Do NOT summarize: routine heartbeat checks, loop recreation steps,
counter resets, or boot sequence details (these are in CLAUDE.md).
```

#### C. Pre-Compact Hook (preserves SEAL state)

Create a hook at `.claude/hooks/pre-compact.sh`:

```bash
#!/bin/bash
# Capture SEAL state before compaction
python3 ~/IA/proyecto-seal/messages/session_checkpoint.py \
  --agent "$SEAL_AGENT_NAME" \
  --save --type "pre_compact"
```

#### D. Post-Compact Hook (restores SEAL state)

Create a hook at `.claude/hooks/post-compact.sh`:

```bash
#!/bin/bash
# Read latest checkpoint after compaction
python3 ~/IA/proyecto-seal/messages/session_checkpoint.py \
  --agent "$SEAL_AGENT_NAME" \
  --read
```

#### E. Token Budget Analysis for 200K Context

```
System prompt + tools + CLAUDE.md:     ~25,000 tokens
SEAL boot_context MCP result:          ~5,000 tokens
Active loops (5-8 cron definitions):   ~2,000 tokens
Message channel history:               ~3,000 tokens
─────────────────────────────────────────────────────
Overhead per session start:            ~35,000 tokens

Effective for conversation:            180,000 - 35,000 = 145,000 tokens
AutoCompact fires at:                  180,000 - 13,000 = 167,000 tokens
Actual conversation budget:            167,000 - 35,000 = 132,000 tokens

At ~500 tokens per tool call round-trip:
  → ~264 tool calls before compaction
  → At SEAL's pace (~4 tool calls/min): ~66 minutes between compactions
```

This means compaction fires approximately every hour of active use, which is acceptable for 8-12h sessions if the summary quality is high.

---

## 15. Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `src/services/compact/microCompact.ts` | 531 | Tiers 1-2: cached MC + time-based MC |
| `src/services/compact/autoCompact.ts` | 352 | Threshold calculation, shouldAutoCompact, autoCompactIfNeeded |
| `src/services/compact/sessionMemoryCompact.ts` | 631 | Tier 3: session memory compaction |
| `src/services/compact/compact.ts` | ~1600 | Tier 4: full LLM compact + file restoration |
| `src/services/compact/prompt.ts` | 375 | All compact prompts (base, partial from, partial up_to) |
| `src/services/compact/postCompactCleanup.ts` | 78 | Cache/state cleanup after any compaction |
| `src/services/compact/apiMicrocompact.ts` | 154 | Tier 0: API-native context management |
| `src/services/compact/timeBasedMCConfig.ts` | 44 | Time-based MC GrowthBook config |
| `src/services/compact/cachedMicrocompact.ts` | empty | Ant-only (dead code eliminated in open source) |
| `src/services/compact/snipCompact.ts` | empty | Ant-only (dead code eliminated) |
| `src/services/compact/grouping.ts` | 64 | Message grouping by API round |
| `src/services/compact/compactWarningState.ts` | 19 | Warning suppression state |
| `src/services/compact/compactWarningHook.ts` | 17 | React hook for warning state |
| `src/services/SessionMemory/prompts.ts` | 325 | Session memory template + update prompt |
| `src/services/SessionMemory/sessionMemory.ts` | ~200+ | Background extraction sub-agent |
| `src/services/SessionMemory/sessionMemoryUtils.ts` | ~150+ | Shared state + extraction coordination |
| `src/constants/toolLimits.ts` | 57 | Tool result size limits |
| `src/utils/context.ts` | ~80+ | Context window sizes, 1M support |
| `src/commands/compact/compact.ts` | 288 | /compact command implementation |
