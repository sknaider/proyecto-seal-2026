# SPEC: Claude Code Memory Extraction System — Complete Reverse Engineering

> Source: `open-claude-code-gitlab/` (open-source Claude Code)
> Reverse-engineered: 2026-04-10
> Purpose: Adapt for SEAL (Python + PostgreSQL + MCP)

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Component 1: extractMemories (Durable Memory Extraction)](#2-extractmemories)
3. [Component 2: SessionMemory (Ephemeral Session Notes)](#3-sessionmemory)
4. [Component 3: autoDream (Memory Consolidation)](#4-autodream)
5. [Forked Agent Pattern](#5-forked-agent-pattern)
6. [Memory Type Taxonomy](#6-memory-type-taxonomy)
7. [Memory File Format](#7-memory-file-format)
8. [Data Flow Diagrams](#8-data-flow)
9. [All Configurable Parameters](#9-configurable-parameters)
10. [SEAL Adaptation Plan](#10-seal-adaptation-plan)

---

## 1. Architecture Overview

Claude Code has THREE distinct memory subsystems, all sharing the **forked agent pattern**:

```
                    Main Agent Loop (query.ts)
                           |
                    handleStopHooks() — fires after each complete response
                           |
              +------------+------------+
              |            |            |
    extractMemories   autoDream   SessionMemory
    (durable facts)  (consolidation)  (session notes)
              |            |            |
              v            v            v
         runForkedAgent() — shares parent prompt cache
              |
         Writes to ~/.claude/projects/<slug>/memory/
```

### Key Insight: Prompt Cache Sharing

All three subsystems fork from the main conversation and reuse the parent's **prompt cache**. The API cache key is: `system prompt + tools + model + message prefix + thinking config`. The fork sends the SAME system prompt, tools, and message prefix as the parent, then appends its own extraction prompt at the end. This means the fork's API call gets a cache HIT on the parent's context (~95%+ of tokens are cached reads), making extraction nearly free in terms of input tokens.

### Lifecycle

1. `startBackgroundHousekeeping()` called at CLI startup
2. Calls `initExtractMemories()` — creates closure-scoped state
3. Calls `initAutoDream()` — creates closure-scoped state
4. `initSessionMemory()` — registers post-sampling hook
5. On each turn end, `handleStopHooks()` fires:
   - `executeExtractMemories()` — fire-and-forget
   - `executeAutoDream()` — fire-and-forget
6. SessionMemory runs via registered `postSamplingHook`

---

## 2. extractMemories

**File:** `src/services/extractMemories/extractMemories.ts`
**Prompts:** `src/services/extractMemories/prompts.ts`

### 2.1 Trigger Conditions

Fires at the end of every complete query loop (when the model produces a final response with no tool calls), via `handleStopHooks` in `stopHooks.ts`.

**Pre-conditions (all must pass):**
1. Feature flag `EXTRACT_MEMORIES` is compiled in (build-time)
2. Feature gate `tengu_passport_quail` is enabled (GrowthBook, runtime)
3. `isAutoMemoryEnabled()` returns true (env var / settings)
4. NOT in remote mode (`getIsRemoteMode()`)
5. NOT a subagent (no `agentId`)
6. NOT already in progress (overlap guard)

### 2.2 Throttle Logic

```
turnsSinceLastExtraction++
if turnsSinceLastExtraction < tengu_bramble_lintel (default: 1):
    skip (return early)
else:
    turnsSinceLastExtraction = 0
    proceed with extraction
```

- Default `tengu_bramble_lintel = 1` means extraction runs every eligible turn
- Setting it to 2 means every other turn, 3 means every third, etc.
- **Trailing runs** (from stashed contexts) skip this throttle

### 2.3 Mutual Exclusion with Manual Memory Writes

**Critical design:** The main agent's system prompt ALWAYS includes full memory save instructions. When the main agent writes memories itself, the extraction agent is REDUNDANT.

```typescript
function hasMemoryWritesSince(messages, sinceUuid): boolean
```

Scans assistant messages after the cursor UUID for any `tool_use` blocks where `name === FILE_EDIT_TOOL_NAME || FILE_WRITE_TOOL_NAME` and the `file_path` is within `isAutoMemPath()`. If found:
- Extraction is SKIPPED
- Cursor advances past this range
- The two systems are **mutually exclusive per turn**

### 2.4 Overlap Guard (Coalescing)

```
if inProgress:
    stash current context as pendingContext (overwrite any previous stash)
    return immediately (no extraction)

// After current extraction finishes (in finally block):
if pendingContext exists:
    run trailing extraction with stashed context
    (trailing run skips throttle check)
```

Only ONE stashed context is kept (latest overwrites). This means at most 2 extractions run back-to-back (current + one trailing).

### 2.5 The Exact Extraction Prompt

#### Opener (shared by both auto-only and combined variants):

```
You are now acting as the memory extraction subagent. Analyze the most recent
~{newMessageCount} messages above and use them to update your persistent memory
systems.

Available tools: Read, Grep, Glob, read-only Bash (ls/find/cat/stat/wc/head/tail
and similar), and Edit/Write for paths inside the memory directory only. Bash rm
is not permitted. All other tools -- MCP, Agent, write-capable Bash, etc -- will
be denied.

You have a limited turn budget. Edit requires a prior Read of the same file, so
the efficient strategy is: turn 1 -- issue all Read calls in parallel for every
file you might update; turn 2 -- issue all Write/Edit calls in parallel. Do not
interleave reads and writes across multiple turns.

You MUST only use content from the last ~{newMessageCount} messages to update your
persistent memories. Do not waste any turns attempting to investigate or verify that
content further -- no grepping source files, no reading code to confirm a pattern
exists, no git commands.

## Existing memory files

{manifestOfExistingFiles}

Check this list before writing -- update an existing file rather than creating a
duplicate.
```

#### Auto-only variant (buildExtractAutoOnlyPrompt):

After the opener, appends:

```
If the user explicitly asks you to remember something, save it immediately as
whichever type fits best. If they ask you to forget something, find and remove
the relevant entry.

## Types of memory
[Four-type taxonomy: user, feedback, project, reference — see Section 6]

## What NOT to save in memory
[See Section 6]

## How to save memories

Saving a memory is a two-step process:

**Step 1** -- write the memory to its own file (e.g., `user_role.md`,
`feedback_testing.md`) using this frontmatter format:

```markdown
---
name: {{memory name}}
description: {{one-line description -- used to decide relevance in future
conversations, so be specific}}
type: {{user, feedback, project, reference}}
---

{{memory content -- for feedback/project types, structure as: rule/fact,
then **Why:** and **How to apply:** lines}}
```

**Step 2** -- add a pointer to that file in `MEMORY.md`. `MEMORY.md` is an index,
not a memory -- each entry should be one line, under ~150 characters:
`- [Title](file.md) -- one-line hook`. It has no frontmatter. Never write memory
content directly into `MEMORY.md`.

- `MEMORY.md` is always loaded into your system prompt -- lines after 200 will be
  truncated, so keep the index concise
- Organize memory semantically by topic, not chronologically
- Update or remove memories that turn out to be wrong or outdated
- Do not write duplicate memories. First check if there is an existing memory you
  can update before writing a new one.
```

When `skipIndex = true` (feature flag `tengu_moth_copse`), the MEMORY.md index step is omitted and only Step 1 applies.

### 2.6 Tool Permissions (createAutoMemCanUseTool)

The forked agent gets a restricted `canUseTool` function:

| Tool | Permission |
|------|-----------|
| `Read` | ALLOW (unrestricted) |
| `Grep` | ALLOW (unrestricted) |
| `Glob` | ALLOW (unrestricted) |
| `Bash` | ALLOW only if `tool.isReadOnly(input)` — ls, find, grep, cat, stat, wc, head, tail |
| `Edit` | ALLOW only if `file_path` is within `isAutoMemPath()` |
| `Write` | ALLOW only if `file_path` is within `isAutoMemPath()` |
| `REPL` | ALLOW (wraps primitives, inner calls still gated) |
| Everything else | DENY |

### 2.7 Agent Configuration

```typescript
runForkedAgent({
    promptMessages: [createUserMessage({ content: userPrompt })],
    cacheSafeParams,
    canUseTool,
    querySource: 'extract_memories',
    forkLabel: 'extract_memories',
    skipTranscript: true,    // No sidechain recording
    maxTurns: 5,             // Hard cap on API round-trips
})
```

### 2.8 Post-extraction

1. Cursor (`lastMemoryMessageUuid`) advances to last message UUID
2. Written file paths extracted from agent output
3. Index file (`MEMORY.md`) writes filtered out of user-visible count
4. `appendSystemMessage` called with `createMemorySavedMessage(memoryPaths)` — injects a system message into main conversation: "Saved N memories"
5. Analytics event `tengu_extract_memories_extraction` logged with full token usage

### 2.9 Drain on Shutdown

```typescript
export async function drainPendingExtraction(timeoutMs = 60_000): Promise<void>
```

Called by `print.ts` after response is flushed but before `gracefulShutdownSync`. Awaits all in-flight extractions with a 60s soft timeout (timer uses `.unref()` so it doesn't block Node exit).

---

## 3. SessionMemory

**File:** `src/services/SessionMemory/sessionMemory.ts`
**Prompts:** `src/services/SessionMemory/prompts.ts`
**Utils:** `src/services/SessionMemory/sessionMemoryUtils.ts`

### 3.1 Purpose (Different from extractMemories)

SessionMemory maintains a **single markdown file** with structured notes about the current conversation. It is NOT durable cross-session memory — it's used for:
- **Context compaction**: When the context window fills and auto-compact fires, SessionMemory provides a summary of what happened
- **Session continuity**: The notes file persists to disk, aiding resumption

### 3.2 Trigger Conditions

Registered as a `postSamplingHook` via `registerPostSamplingHook(extractSessionMemory)`.

**Pre-conditions:**
1. Feature gate `tengu_session_memory` enabled (GrowthBook)
2. Auto-compact is enabled (`isAutoCompactEnabled()`)
3. NOT in remote mode
4. `querySource === 'repl_main_thread'` (main thread only)

**Threshold system (dual gate):**

```
Initialization threshold:
    tokenCountWithEstimation(messages) >= minimumMessageTokensToInit (default: 10000)
    Once crossed, markSessionMemoryInitialized() — never checks init again

Update threshold (BOTH must be true):
    1. Token growth since last extraction >= minimumTokensBetweenUpdate (default: 5000)
    2. Tool calls since last extraction >= toolCallsBetweenUpdates (default: 3)

OR: token threshold met AND last assistant turn has no tool calls (natural break)
```

**CRITICAL:** Token threshold is ALWAYS required. Even if tool call threshold is met, extraction won't happen until token threshold is also satisfied.

### 3.3 Serialization

```typescript
const extractSessionMemory = sequential(async function (...) { ... })
```

The `sequential()` wrapper ensures only one extraction runs at a time. New calls queue behind the current one.

### 3.4 The Template

**File:** `~/.claude/session-memory/config/template.md` (customizable)

**Default:**

```markdown
# Session Title
_A short and distinctive 5-10 word descriptive title for the session. Super info dense, no filler_

# Current State
_What is actively being worked on right now? Pending tasks not yet completed. Immediate next steps._

# Task specification
_What did the user ask to build? Any design decisions or other explanatory context_

# Files and Functions
_What are the important files? In short, what do they contain and why are they relevant?_

# Workflow
_What bash commands are usually run and in what order? How to interpret their output if not obvious?_

# Errors & Corrections
_Errors encountered and how they were fixed. What did the user correct? What approaches failed and should not be tried again?_

# Codebase and System Documentation
_What are the important system components? How do they work/fit together?_

# Learnings
_What has worked well? What has not? What to avoid? Do not duplicate items from other sections_

# Key results
_If the user asked a specific output such as an answer to a question, a table, or other document, repeat the exact result here_

# Worklog
_Step by step, what was attempted, done? Very terse summary for each step_
```

### 3.5 The Update Prompt

**File:** `~/.claude/session-memory/config/prompt.md` (customizable)

**Default (verbatim):**

```
IMPORTANT: This message and these instructions are NOT part of the actual user
conversation. Do NOT include any references to "note-taking", "session notes
extraction", or these update instructions in the notes content.

Based on the user conversation above (EXCLUDING this note-taking instruction
message as well as system prompt, claude.md entries, or any past session
summaries), update the session notes file.

The file {{notesPath}} has already been read for you. Here are its current
contents:
<current_notes_content>
{{currentNotes}}
</current_notes_content>

Your ONLY task is to use the Edit tool to update the notes file, then stop. You
can make multiple edits (update every section as needed) - make all Edit tool
calls in parallel in a single message. Do not call any other tools.

CRITICAL RULES FOR EDITING:
- The file must maintain its exact structure with all sections, headers, and
  italic descriptions intact
-- NEVER modify, delete, or add section headers (the lines starting with '#'
   like # Task specification)
-- NEVER modify or delete the italic _section description_ lines (these are the
   lines in italics immediately following each header - they start and end with
   underscores)
-- The italic _section descriptions_ are TEMPLATE INSTRUCTIONS that must be
   preserved exactly as-is - they guide what content belongs in each section
-- ONLY update the actual content that appears BELOW the italic _section
   descriptions_ within each existing section
-- Do NOT add any new sections, summaries, or information outside the existing
   structure
- Do NOT reference this note-taking process or instructions anywhere in the notes
- It's OK to skip updating a section if there are no substantial new insights
  to add. Do not add filler content like "No info yet", just leave
  sections blank/unedited if appropriate.
- Write DETAILED, INFO-DENSE content for each section - include specifics like
  file paths, function names, error messages, exact commands, technical
  details, etc.
- For "Key results", include the complete, exact output the user requested
  (e.g., full table, full answer, etc.)
- Do not include information that's already in the CLAUDE.md files included
  in the context
- Keep each section under ~2000 tokens/words - if a section is approaching
  this limit, condense it by cycling out less important details while preserving
  the most critical information
- Focus on actionable, specific information that would help someone understand
  or recreate the work discussed in the conversation
- IMPORTANT: Always update "Current State" to reflect the most recent work -
  this is critical for continuity after compaction

Use the Edit tool with file_path: {{notesPath}}

STRUCTURE PRESERVATION REMINDER:
Each section has TWO parts that must be preserved exactly as they appear in the
current file:
1. The section header (line starting with #)
2. The italic description line (the _italicized text_ immediately after the
   header - this is a template instruction)

You ONLY update the actual content that comes AFTER these two preserved lines.
The italic description lines starting and ending with underscores are part of the
template structure, NOT content to be edited or removed.

REMEMBER: Use the Edit tool in parallel and stop. Do not continue after the
edits. Only include insights from the actual user conversation, never from these
note-taking instructions. Do not delete or change section headers or italic
_section descriptions_.
```

**Dynamic additions:**
- If total session memory > 12000 tokens: CRITICAL budget warning appended
- If any section > 2000 tokens: per-section condensation warnings appended

### 3.6 Tool Permissions (SessionMemory-specific)

MUCH more restricted than extractMemories:

| Tool | Permission |
|------|-----------|
| `Edit` | ALLOW only if `file_path === memoryPath` (exact match, single file) |
| Everything else | DENY |

### 3.7 Size Limits

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_SECTION_LENGTH` | 2000 tokens | Per-section cap |
| `MAX_TOTAL_SESSION_MEMORY_TOKENS` | 12000 tokens | Total file cap |

### 3.8 Compact Integration

`truncateSessionMemoryForCompact(content)` — used when session memory is inserted into compact messages. Truncates at per-section char limit (`MAX_SECTION_LENGTH * 4` chars) at line boundaries. Returns `{ truncatedContent, wasTruncated }`.

---

## 4. autoDream

**File:** `src/services/autoDream/autoDream.ts`
**Prompt:** `src/services/autoDream/consolidationPrompt.ts`
**Config:** `src/services/autoDream/config.ts`
**Lock:** `src/services/autoDream/consolidationLock.ts`

### 4.1 Purpose

Background memory consolidation. Fires the `/dream` prompt as a forked subagent when time-gate passes AND enough sessions have accumulated. This is a PERIODIC maintenance task, not per-turn.

### 4.2 Gate Order (cheapest first)

```
1. KAIROS mode check: if getKairosActive() → skip (uses disk-skill dream)
2. Remote mode check: if getIsRemoteMode() → skip
3. Auto-memory check: if !isAutoMemoryEnabled() → skip
4. Feature flag: isAutoDreamEnabled() — user setting OR GrowthBook tengu_onyx_plover

5. Time gate:
   lastConsolidatedAt = lock file mtime (stat)
   hoursSince = (now - lastConsolidatedAt) / 3600000
   if hoursSince < minHours (default: 24) → skip

6. Scan throttle:
   if timeSinceLastScan < 10 minutes → skip
   (prevents repeated session scans when time-gate passes but session-gate doesn't)

7. Session gate:
   sessionIds = sessions with mtime > lastConsolidatedAt (excluding current)
   if sessionIds.length < minSessions (default: 5) → skip

8. Lock:
   tryAcquireConsolidationLock() — write PID, verify PID
   if lock held by live process → skip
   Dead PID or stale (>1 hour) → reclaim
```

### 4.3 Default Configuration

```typescript
{
    minHours: 24,      // Hours since last consolidation
    minSessions: 5,    // Minimum sessions since last consolidation
}
```

Configurable via GrowthBook `tengu_onyx_plover` feature flag. User can also set `autoDreamEnabled` in `settings.json`.

### 4.4 Lock Mechanism

**File:** `.consolidate-lock` inside the memory directory

- **Lock file mtime = lastConsolidatedAt** (the timestamp of last successful consolidation)
- **Lock file body = PID** of the holding process
- Acquisition: write PID, re-read to verify (race-safe: last writer wins, loser bails)
- Stale threshold: 1 hour (PID reuse guard)
- Rollback on failure: rewind mtime to pre-acquire value, clear PID body

### 4.5 The Consolidation Prompt (verbatim)

```
# Dream: Memory Consolidation

You are performing a dream -- a reflective pass over your memory files.
Synthesize what you've learned recently into durable, well-organized memories so
that future sessions can orient quickly.

Memory directory: `{memoryRoot}`
This directory already exists -- write to it directly with the Write tool (do not
run mkdir or check for its existence).

Session transcripts: `{transcriptDir}` (large JSONL files -- grep narrowly,
don't read whole files)

---

## Phase 1 -- Orient

- `ls` the memory directory to see what already exists
- Read `MEMORY.md` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates
- If `logs/` or `sessions/` subdirectories exist (assistant-mode layout), review
  recent entries there

## Phase 2 -- Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Daily logs** (`logs/YYYY/MM/YYYY-MM-DD.md`) if present -- these are the
   append-only stream
2. **Existing memories that drifted** -- facts that contradict something you see
   in the codebase now
3. **Transcript search** -- if you need specific context (e.g., "what was the
   error message from yesterday's build failure?"), grep the JSONL transcripts
   for narrow terms:
   `grep -rn "<narrow term>" {transcriptDir}/ --include="*.jsonl" | tail -50`

Don't exhaustively read transcripts. Look only for things you already suspect
matter.

## Phase 3 -- Consolidate

For each thing worth remembering, write or update a memory file at the top level
of the memory directory. Use the memory file format and type conventions from your
system prompt's auto-memory section -- it's the source of truth for what to save,
how to structure it, and what NOT to save.

Focus on:
- Merging new signal into existing topic files rather than creating near-duplicates
- Converting relative dates ("yesterday", "last week") to absolute dates so they
  remain interpretable after time passes
- Deleting contradicted facts -- if today's investigation disproves an old memory,
  fix it at the source

## Phase 4 -- Prune and index

Update `MEMORY.md` so it stays under 200 lines AND under ~25KB. It's an
**index**, not a dump -- each entry should be one line under ~150 characters:
`- [Title](file.md) -- one-line hook`. Never write memory content directly into
it.

- Remove pointers to memories that are now stale, wrong, or superseded
- Demote verbose entries: if an index line is over ~200 chars, it's carrying
  content that belongs in the topic file -- shorten the line, move the detail
- Add pointers to newly important memories
- Resolve contradictions -- if two files disagree, fix the wrong one

---

Return a brief summary of what you consolidated, updated, or pruned. If nothing
changed (memories are already tight), say so.

## Additional context

**Tool constraints for this run:** Bash is restricted to read-only commands
(`ls`, `find`, `grep`, `cat`, `stat`, `wc`, `head`, `tail`, and similar).
Anything that writes, redirects to a file, or modifies state will be denied.
Plan your exploration with this in mind -- no need to probe.

Sessions since last consolidation ({count}):
- session-uuid-1
- session-uuid-2
...
```

### 4.6 Tool Permissions

Uses the SAME `createAutoMemCanUseTool(memoryRoot)` as extractMemories — Read/Grep/Glob unrestricted, read-only Bash, Edit/Write only within memory dir.

### 4.7 Progress Tracking

`makeDreamProgressWatcher()` — watches forked agent messages:
- Extracts text blocks from assistant turns (reasoning/summary)
- Counts tool_use blocks
- Collects file paths from Edit/Write operations
- Reports via `addDreamTurn()` to the DreamTask state

### 4.8 Post-completion

- `completeDreamTask()` updates task state
- If files were touched, injects `createMemorySavedMessage` with `verb: 'Improved'`
- On failure: `rollbackConsolidationLock(priorMtime)` rewinds mtime
- Scan throttle (10 min) acts as backoff on repeated failures

---

## 5. Forked Agent Pattern

**File:** `src/utils/forkedAgent.ts`

### 5.1 Core Concept

A "forked agent" is a **perfect fork** of the main conversation that:
1. Shares the parent's prompt cache (identical system prompt, tools, model)
2. Runs in an isolated context (prevents mutation of parent state)
3. Sends the parent's full message history as prefix, then appends its own prompt

### 5.2 CacheSafeParams

```typescript
type CacheSafeParams = {
    systemPrompt: SystemPrompt     // Must match parent
    userContext: { [k: string]: string }
    systemContext: { [k: string]: string }
    toolUseContext: ToolUseContext  // Contains tools, model, options
    forkContextMessages: Message[] // Parent's conversation history
}
```

### 5.3 Isolation (createSubagentContext)

Creates a new `ToolUseContext` from the parent with:

| Field | Behavior |
|-------|----------|
| `readFileState` | CLONED from parent (ensures Edit works after Read) |
| `abortController` | NEW child controller linked to parent (parent abort propagates) |
| `getAppState` | Wrapped: sets `shouldAvoidPermissionPrompts = true` |
| `setAppState` | NO-OP (isolated, no UI updates) |
| `setInProgressToolUseIDs` | NO-OP |
| `setResponseLength` | NO-OP |
| `addNotification` | undefined |
| `setToolJSX` | undefined |
| `agentId` | NEW unique ID |
| `queryTracking` | NEW chain, depth = parent.depth + 1 |
| `contentReplacementState` | CLONED (for cache-prefix stability) |

### 5.4 runForkedAgent Flow

```
1. Create isolated context via createSubagentContext()
2. Build initialMessages = [...forkContextMessages, ...promptMessages]
3. (Optional) Record initial transcript to sidechain
4. Enter query loop:
   for await (message of query({
       messages: initialMessages,
       systemPrompt, userContext, systemContext,
       canUseTool,
       toolUseContext: isolatedContext,
       querySource, maxTurns, ...
   })):
       - Accumulate usage from message_delta events
       - Collect output messages
       - Call onMessage callback if provided
       - Record to sidechain transcript
5. Release cloned file state cache
6. Log tengu_fork_agent_query event
7. Return { messages, totalUsage }
```

---

## 6. Memory Type Taxonomy

Four types, constrained to context NOT derivable from current project state:

### 6.1 `user`

- **What:** User's role, goals, responsibilities, knowledge, preferences
- **When to save:** When you learn details about the user
- **Scope:** Always private
- **Example:** "user is a data scientist, focused on observability/logging"

### 6.2 `feedback`

- **What:** Guidance on how to approach work (corrections AND confirmations)
- **When to save:** User corrects approach OR confirms non-obvious approach
- **Body structure:** Rule, then **Why:** line, then **How to apply:** line
- **Scope:** Default private; team only for project-wide conventions
- **Example:** "integration tests must hit real DB, not mocks. Why: mock/prod divergence masked broken migration"

### 6.3 `project`

- **What:** Ongoing work, goals, bugs, incidents not in code/git
- **When to save:** Learn who is doing what, why, by when
- **Body structure:** Fact/decision, then **Why:** line, then **How to apply:** line
- **Key rule:** Convert relative dates to absolute dates
- **Example:** "merge freeze begins 2026-03-05 for mobile release cut"

### 6.4 `reference`

- **What:** Pointers to information in external systems
- **When to save:** Learn about resources and their purpose
- **Example:** "pipeline bugs tracked in Linear project INGEST"

### 6.5 What NOT to Save

- Code patterns, conventions, architecture, file paths, project structure (derivable)
- Git history, recent changes, who-changed-what (git log/blame)
- Debugging solutions or fix recipes (fix is in the code)
- Anything in CLAUDE.md files
- Ephemeral task details, temporary state, current conversation context
- Activity logs/PR lists (unless user identifies something surprising/non-obvious)

---

## 7. Memory File Format

### 7.1 Individual Memory File

```markdown
---
name: {{memory name}}
description: {{one-line description -- used for relevance matching}}
type: {{user, feedback, project, reference}}
---

{{memory content}}
```

### 7.2 MEMORY.md (Index)

```markdown
- [Title](file.md) -- one-line hook
- [Another Topic](another.md) -- brief description
```

- Max 200 lines
- Max ~25KB
- Each entry under ~150 characters
- No frontmatter
- Never write memory content directly into it

### 7.3 Memory Scan

`scanMemoryFiles()` reads all `.md` files (recursive, excluding MEMORY.md), parses frontmatter (first 30 lines), returns headers sorted newest-first, capped at 200 files.

`formatMemoryManifest()` formats as:
```
- [type] filename (ISO-timestamp): description
- [feedback] user_testing_pref.md (2026-04-09T...): User prefers integration tests
```

---

## 8. Data Flow

### 8.1 extractMemories Flow

```
User sends message
    -> Main agent processes, produces response
    -> handleStopHooks() fires
    -> executeExtractMemories() called (fire-and-forget)
        -> Check: not subagent, gate enabled, auto-memory enabled, not remote
        -> Check: not already in progress (stash if so)
        -> Check: hasMemoryWritesSince? If yes, advance cursor, skip
        -> Check: turnsSinceLastExtraction >= threshold?
        -> Scan memory directory (scanMemoryFiles -> formatMemoryManifest)
        -> Build prompt (opener + types + what-not-to-save + how-to-save)
        -> runForkedAgent:
            -> API call: parent context (cached) + extraction prompt
            -> Agent reads existing memory files (turn 1)
            -> Agent writes/edits memory files (turn 2)
            -> Max 5 turns
        -> Advance cursor to last message UUID
        -> Extract written paths from agent output
        -> appendSystemMessage("Saved N memories") into main conversation
```

### 8.2 SessionMemory Flow

```
Post-sampling hook fires
    -> Check: gate enabled, main thread, auto-compact enabled
    -> Check: initialization threshold (10K tokens)
    -> Check: update threshold (5K token growth AND 3+ tool calls)
         OR   (token threshold AND no tool calls in last turn)
    -> Setup: ensure directory, create file if needed, read current contents
    -> Build prompt: template variables substituted, section size warnings
    -> runForkedAgent:
        -> API call: parent context (cached) + update prompt
        -> Agent uses Edit tool on single file
    -> Record extraction token count
    -> Update lastSummarizedMessageId
```

### 8.3 autoDream Flow

```
handleStopHooks() fires
    -> executeAutoDream() called (fire-and-forget)
    -> Time gate: hours since last consolidation >= 24?
    -> Scan throttle: > 10 min since last session scan?
    -> Session gate: >= 5 sessions modified since last consolidation?
    -> Lock: acquire PID-based lock file
    -> Register DreamTask in app state
    -> Build consolidation prompt (4 phases: orient, gather, consolidate, prune)
    -> runForkedAgent:
        -> Agent ls memory dir, read MEMORY.md
        -> Agent grep transcripts for specific terms
        -> Agent write/edit/delete memory files
        -> Agent update MEMORY.md index
    -> Complete DreamTask
    -> appendSystemMessage("Improved N memories")
    -> On failure: rollback lock, scan throttle acts as backoff
```

---

## 9. Configurable Parameters

### 9.1 Feature Flags (GrowthBook)

| Flag | Default | Purpose |
|------|---------|---------|
| `tengu_passport_quail` | false | Master gate for extractMemories |
| `tengu_bramble_lintel` | 1 | Turns between extractions (1 = every turn) |
| `tengu_moth_copse` | false | Skip MEMORY.md index step |
| `tengu_session_memory` | false | Master gate for SessionMemory |
| `tengu_sm_config` | {} | Remote config for SessionMemory thresholds |
| `tengu_onyx_plover` | null | autoDream config: `{ enabled, minHours, minSessions }` |
| `tengu_coral_fern` | false | Enable "Searching past context" section |

### 9.2 User Settings (settings.json)

| Setting | Default | Purpose |
|---------|---------|---------|
| `autoMemoryEnabled` | true | Enable/disable all auto-memory |
| `autoDreamEnabled` | undefined | Override autoDream gate (undefined = use GrowthBook) |
| `autoMemoryDirectory` | computed | Custom memory directory path (supports ~/) |

### 9.3 Environment Variables

| Variable | Purpose |
|----------|---------|
| `CLAUDE_CODE_DISABLE_AUTO_MEMORY` | 1/true = disable all auto-memory |
| `CLAUDE_CODE_SIMPLE` | 1 = bare mode, no background agents |
| `CLAUDE_CODE_REMOTE` | Remote mode flag |
| `CLAUDE_CODE_REMOTE_MEMORY_DIR` | Override memory base dir in remote mode |
| `CLAUDE_COWORK_MEMORY_PATH_OVERRIDE` | Full-path override for Cowork |
| `CLAUDE_COWORK_MEMORY_EXTRA_GUIDELINES` | Extra guidelines injected into memory prompt |

### 9.4 SessionMemory Defaults

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `minimumMessageTokensToInit` | 10000 | Tokens before first extraction |
| `minimumTokensBetweenUpdate` | 5000 | Token growth between updates |
| `toolCallsBetweenUpdates` | 3 | Tool calls between updates |
| `MAX_SECTION_LENGTH` | 2000 tokens | Per-section cap |
| `MAX_TOTAL_SESSION_MEMORY_TOKENS` | 12000 tokens | Total file cap |

### 9.5 autoDream Defaults

| Parameter | Default | Purpose |
|-----------|---------|---------|
| `minHours` | 24 | Hours between consolidations |
| `minSessions` | 5 | Sessions required since last consolidation |
| `SESSION_SCAN_INTERVAL_MS` | 600000 (10 min) | Scan throttle |
| `HOLDER_STALE_MS` | 3600000 (1 hour) | Lock stale threshold |

### 9.6 extractMemories Limits

| Parameter | Value | Purpose |
|-----------|-------|---------|
| `maxTurns` | 5 | Hard cap on agent turns |
| `drainTimeout` | 60000ms | Shutdown drain timeout |
| `MAX_MEMORY_FILES` | 200 | Cap on scanned memory files |
| `FRONTMATTER_MAX_LINES` | 30 | Lines read for frontmatter parsing |
| `MAX_ENTRYPOINT_LINES` | 200 | MEMORY.md line cap |
| `MAX_ENTRYPOINT_BYTES` | 25000 | MEMORY.md byte cap |

---

## 10. SEAL Adaptation Plan

### 10.1 Mapping Claude Code -> SEAL

| Claude Code Concept | SEAL Equivalent |
|---------------------|-----------------|
| Memory directory (`~/.claude/projects/<slug>/memory/`) | PostgreSQL `soul_memories` table |
| MEMORY.md (index) | PostgreSQL query with embeddings + Qdrant hybrid search |
| Individual .md files (topic files) | Rows in `soul_memories` with `content`, `type`, `importance` |
| Frontmatter (name, description, type) | Columns: `title`, `description`, `memory_type` |
| `scanMemoryFiles()` / `formatMemoryManifest()` | `memory_list()` / `memory_search()` MCP tools |
| `runForkedAgent()` | Python subprocess or async task calling Claude API |
| `canUseTool` (permission gating) | MCP tool whitelist in extraction agent config |
| SessionMemory (single file) | `working_state_get/update` MCP tools |
| autoDream (consolidation) | `sleep_gate` + `soul_consolidate.py` cron |
| MEMORY.md line/byte caps | SQL query limits + embedding-based relevance filtering |

### 10.2 extractMemories -> SEAL Implementation

```python
# seal_extract_memories.py

import asyncio
from datetime import datetime
from typing import Optional

class SEALMemoryExtractor:
    """Background memory extraction, adapted from Claude Code's extractMemories."""

    def __init__(self, agent_name: str, db_pool):
        self.agent_name = agent_name
        self.db = db_pool
        self.last_message_id: Optional[int] = None
        self.turns_since_extraction = 0
        self.in_progress = False
        self.pending_context = None
        self.extraction_interval = 1  # Every N turns

    async def should_extract(self, messages: list, last_message_id: int) -> bool:
        """Check if extraction should run (mirrors Claude Code's gate checks)."""
        # Skip if manual memory_store happened since last extraction
        if await self._has_manual_stores_since(self.last_message_id):
            self.last_message_id = last_message_id
            return False

        # Throttle check
        self.turns_since_extraction += 1
        if self.turns_since_extraction < self.extraction_interval:
            return False

        return True

    async def extract(self, messages: list, context: dict):
        """Run extraction as a background task."""
        if self.in_progress:
            self.pending_context = (messages, context)
            return

        self.in_progress = True
        self.turns_since_extraction = 0

        try:
            # Get existing memories for dedup
            existing = await self.db.fetch(
                "SELECT title, description, memory_type, updated_at "
                "FROM soul_memories WHERE agent=$1 ORDER BY updated_at DESC LIMIT 200",
                self.agent_name
            )

            manifest = self._format_manifest(existing)
            prompt = self._build_prompt(len(messages), manifest)

            # Call Claude API with extraction prompt
            # (In SEAL: this would be an MCP call or direct API call)
            result = await self._run_extraction_agent(prompt, messages)

            # Process results — store via memory_store MCP tool
            for memory in result.extracted_memories:
                await self._store_or_update(memory)

            self.last_message_id = messages[-1]['id'] if messages else None

        finally:
            self.in_progress = False
            if self.pending_context:
                trailing = self.pending_context
                self.pending_context = None
                await self.extract(*trailing)

    def _build_prompt(self, new_msg_count: int, manifest: str) -> str:
        return f"""You are the memory extraction subagent for {self.agent_name}.
Analyze the most recent ~{new_msg_count} messages and extract durable memories.

## Existing memories
{manifest}

## Types of memory
- user: User's role, goals, preferences
- feedback: Corrections and confirmed approaches (include Why + How to apply)
- project: Ongoing work context not in code/git (use absolute dates)
- reference: Pointers to external systems

## What NOT to save
- Code patterns derivable from reading current state
- Git history, debugging solutions, CLAUDE.md content
- Ephemeral task details

## Output format
For each memory to save/update, output JSON:
{{"action": "create|update|delete", "title": "...", "description": "...",
  "type": "user|feedback|project|reference", "content": "...",
  "importance": 5-10, "existing_id": null|id}}

Only extract what is genuinely worth persisting across sessions."""
```

### 10.3 SessionMemory -> SEAL Implementation

Map to `working_state_get/update` MCP tools:

```python
# The session memory template becomes the working_state schema
SEAL_SESSION_TEMPLATE = {
    "session_title": "",
    "current_state": "",
    "task_specification": "",
    "files_and_functions": "",
    "errors_and_corrections": "",
    "learnings": "",
    "key_results": "",
    "worklog": ""
}
```

Trigger conditions:
- After 10K tokens of context accumulated (init threshold)
- After 5K token growth AND 3+ tool calls (update threshold)
- OR: token threshold met AND natural conversation break

### 10.4 autoDream -> SEAL Implementation

Map to existing `soul_consolidate.py` + `sleep_gate_cron.py`:

```python
# Consolidation fires when:
# 1. Hours since last consolidation >= 24
# 2. Sessions since last consolidation >= 5
# 3. No other consolidation in progress (lock)

# SEAL already has:
# - sleep_gate: determines if consolidation should run
# - soul_consolidate.py: actual consolidation logic
# - session_delta_capture.py: captures session deltas

# Enhancement: Add the 4-phase dream prompt:
# Phase 1: Orient (read existing memories)
# Phase 2: Gather (search recent sessions for signal)
# Phase 3: Consolidate (merge, update, correct)
# Phase 4: Prune (remove stale, update index/embeddings)
```

### 10.5 Key Differences for SEAL

1. **Storage:** PostgreSQL + pgvector + Qdrant instead of markdown files
2. **Index:** Embedding-based search replaces MEMORY.md line scanning
3. **Dedup:** SQL query `WHERE title ILIKE '%...' OR description ILIKE '%...'` + vector similarity
4. **Extraction agent:** Can be a direct Claude API call (no need for full forked agent with shared cache, since SEAL agents don't have the same prompt-cache architecture)
5. **Tool permissions:** MCP tool whitelist rather than filesystem-path restrictions
6. **Multi-agent:** SEAL has ADA + JARVIS, each needing their own extraction with cross-agent visibility
7. **Importance scoring:** SEAL already has `importance` field (1-10); Claude Code uses type taxonomy only

### 10.6 What to Implement First (Priority Order)

1. **Automatic extraction after each response** — the core loop from extractMemories
2. **Mutual exclusion with manual memory_store** — prevent duplicates
3. **The four-type taxonomy** — user/feedback/project/reference with structured Why/How
4. **Consolidation dream** — periodic maintenance, already partially exists in SEAL
5. **Session memory** — structured session notes for compaction continuity
6. **Throttle tuning** — configurable extraction interval

---

## Appendix: File Reference

| File | Purpose |
|------|---------|
| `src/services/extractMemories/extractMemories.ts` | Main extraction logic, lifecycle, tool permissions |
| `src/services/extractMemories/prompts.ts` | Extraction prompt templates |
| `src/services/SessionMemory/sessionMemory.ts` | Session memory extraction and hook registration |
| `src/services/SessionMemory/prompts.ts` | Session memory template, update prompt, section analysis |
| `src/services/SessionMemory/sessionMemoryUtils.ts` | Threshold config, state tracking, content reading |
| `src/services/autoDream/autoDream.ts` | Dream consolidation lifecycle, progress tracking |
| `src/services/autoDream/consolidationPrompt.ts` | 4-phase consolidation prompt |
| `src/services/autoDream/config.ts` | autoDream enabled check |
| `src/services/autoDream/consolidationLock.ts` | PID-based lock, mtime-as-timestamp pattern |
| `src/utils/forkedAgent.ts` | Fork pattern, cache sharing, context isolation |
| `src/memdir/memoryTypes.ts` | 4-type taxonomy, frontmatter format, what-not-to-save |
| `src/memdir/memdir.ts` | Memory prompt building, MEMORY.md handling, directory setup |
| `src/memdir/memoryScan.ts` | Memory file scanning, frontmatter parsing, manifest formatting |
| `src/memdir/paths.ts` | Path resolution, auto-memory enabled check, path validation |
| `src/query/stopHooks.ts` | Integration point: fires extractMemories + autoDream |
| `src/utils/backgroundHousekeeping.ts` | Initialization: initExtractMemories + initAutoDream |
