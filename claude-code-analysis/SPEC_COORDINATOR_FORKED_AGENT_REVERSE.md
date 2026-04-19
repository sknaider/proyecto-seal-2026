# Claude Code Multi-Agent Architecture: Coordinator Mode & Forked Agent
## Complete Reverse Engineering Specification

**Source:** `open-claude-code-gitlab/src/`
**Date:** 2026-04-10
**Author:** ADA (reverse engineering for SEAL adaptation)

---

## 1. Architecture Overview

Claude Code implements three distinct multi-agent patterns:

```
                          +------------------+
                          |  User Terminal   |
                          +--------+---------+
                                   |
                    +--------------+--------------+
                    |                             |
            +-------+--------+           +-------+--------+
            | Coordinator    |           | Fork Subagent  |
            | Mode           |           | Mode           |
            | (JARVIS-like)  |           | (cache-sharing)|
            +-------+--------+           +-------+--------+
                    |                             |
          +---------+---------+           +-------+-------+
          |         |         |           |               |
       +--+--+  +--+--+  +--+--+      +--+--+         +--+--+
       |Wrkr1|  |Wrkr2|  |Wrkr3|      |Fork1|         |Fork2|
       +-----+  +-----+  +-----+      +-----+         +-----+

    Third pattern: Agent Swarms (Tmux/In-Process Teammates)
                          +------------------+
                          |  Team Leader     |
                          +--------+---------+
                                   |
                    +--------------+--------------+
                    |              |              |
             +------+-----+ +----+------+ +-----+------+
             | Teammate A | | Teammate B| | Teammate C |
             | (tmux pane)| | (in-proc) | | (tmux pane)|
             +------------+ +-----------+ +------------+
```

---

## 2. Coordinator Mode (JARVIS Pattern)

### 2.1 Activation

**File:** `src/coordinator/coordinatorMode.ts`

```typescript
// Activated via environment variable
export function isCoordinatorMode(): boolean {
  return isEnvTruthy(process.env.CLAUDE_CODE_COORDINATOR_MODE)
}
```

Feature-gated behind `COORDINATOR_MODE` build flag. Mutually exclusive with Fork Subagent mode.

### 2.2 Tool Restrictions

The coordinator is restricted to **orchestration-only tools**:

```typescript
// src/constants/tools.ts
export const COORDINATOR_MODE_ALLOWED_TOOLS = new Set([
  'Agent',           // Spawn workers
  'TaskStop',        // Stop running workers
  'SendMessage',     // Continue existing workers
  'SyntheticOutput', // Internal output formatting
])
```

The coordinator CANNOT use: Bash, Read, Edit, Write, Glob, Grep, or any filesystem tool. It delegates ALL implementation work to workers.

Workers get the full async agent tool set:

```typescript
export const ASYNC_AGENT_ALLOWED_TOOLS = new Set([
  'Read', 'WebSearch', 'TodoWrite', 'Grep', 'WebFetch',
  'Glob', ...SHELL_TOOL_NAMES, 'Edit', 'Write',
  'NotebookEdit', 'Skill', 'SyntheticOutput', 'ToolSearch',
  'EnterWorktree', 'ExitWorktree',
])
```

### 2.3 System Prompt

**File:** `src/coordinator/coordinatorMode.ts` - `getCoordinatorSystemPrompt()`

The coordinator's system prompt defines:

1. **Role:** "You are a coordinator. Your job is to direct workers."
2. **Available Tools:** Agent (spawn), SendMessage (continue), TaskStop (kill), subscribe_pr_activity
3. **Result Delivery:** Workers report back via `<task-notification>` XML injected as user-role messages
4. **Key Rules:**
   - Don't use workers to check on other workers
   - Don't delegate trivial tasks (file reads, simple commands)
   - Don't set model parameter on workers (they need the default)
   - Continue workers via SendMessage to reuse their loaded context
   - After launching agents, tell user what was launched and END your response
   - NEVER fabricate or predict agent results

### 2.4 Workflow Phases

| Phase | Who | Purpose |
|-------|-----|---------|
| **Research** | Workers (parallel) | Investigate codebase, find files, understand problem |
| **Synthesis** | **Coordinator** | Read findings, understand the problem, craft implementation specs |
| **Implementation** | Workers | Make targeted changes per spec, commit |
| **Verification** | Workers | Test changes work, run typechecks |

**Concurrency Rules:**
- Read-only tasks (research): run in parallel freely
- Write-heavy tasks (implementation): one at a time per set of files
- Verification: can run alongside implementation on different file areas

### 2.5 Worker Prompt Requirements

Workers CANNOT see the coordinator's conversation. Every prompt must be self-contained:
- Include file paths, line numbers, error messages
- State what "done" looks like
- Never write "based on your findings" (that delegates understanding)
- Add purpose statement ("This research will inform a PR description")

**Continue vs. Spawn Decision Matrix:**

| Situation | Mechanism | Why |
|-----------|-----------|-----|
| Research explored exact files to edit | Continue (SendMessage) | Worker has files in context |
| Research was broad, implementation narrow | Spawn fresh (Agent) | Avoid dragging exploration noise |
| Correcting a failure | Continue | Worker has error context |
| Verifying another worker's code | Spawn fresh | Fresh eyes, no implementation assumptions |
| Wrong approach entirely | Spawn fresh | Clean slate avoids anchoring |

### 2.6 Task Notification XML Format

**File:** `src/tasks/LocalAgentTask/LocalAgentTask.tsx`

Workers report results via XML injected as user-role messages:

```xml
<task-notification>
<task-id>{agentId}</task-id>
<tool-use-id>{toolUseId}</tool-use-id>
<output-file>{path/to/output}</output-file>
<status>completed|failed|killed</status>
<summary>Agent "description" completed|failed: {error}|was stopped</summary>
<result>{agent's final text response}</result>
<usage>
  <total_tokens>N</total_tokens>
  <tool_uses>N</tool_uses>
  <duration_ms>N</duration_ms>
</usage>
<worktree>
  <worktreePath>/path/to/worktree</worktreePath>
  <worktreeBranch>branch-name</worktreeBranch>
</worktree>
</task-notification>
```

**XML Tags defined in:** `src/constants/xml.ts`
- `<result>` and `<usage>` are optional sections
- `<task-id>` value = agent ID for SendMessage routing
- `<worktree>` only present when isolation: "worktree" was used

### 2.7 Scratchpad Directory

**File:** `src/utils/permissions/filesystem.ts`

Feature-gated behind `tengu_scratch` Statsig gate.

**Path format:** `/tmp/claude-{uid}/{sanitized-cwd}/{sessionId}/scratchpad/`

**Properties:**
- Created with `0o700` permissions (owner-only)
- Workers can read AND write without permission prompts
- Path traversal protection via `normalize()` before checking
- Announced to coordinator via `getCoordinatorUserContext()`:
  ```
  Scratchpad directory: {path}
  Workers can read and write here without permission prompts.
  Use this for durable cross-worker knowledge.
  ```
- Coordinator tells workers about this path in their prompts
- Acts as shared memory between workers within a session

### 2.8 Worker Context for Coordinator

**File:** `src/coordinator/coordinatorMode.ts` - `getCoordinatorUserContext()`

The coordinator receives context about what workers can do:

```typescript
// Lists available tools for workers
let content = `Workers spawned via the Agent tool have access to these tools: ${workerTools}`

// Lists available MCP servers
if (mcpClients.length > 0) {
  content += `\nWorkers also have access to MCP tools from connected MCP servers: ${serverNames}`
}

// Scratchpad info (if enabled)
if (scratchpadDir && isScratchpadGateEnabled()) {
  content += `\nScratchpad directory: ${scratchpadDir}\nWorkers can read and write here...`
}
```

---

## 3. Forked Agent Pattern (Cache-Sharing)

### 3.1 Activation

**File:** `src/tools/AgentTool/forkSubagent.ts`

```typescript
export function isForkSubagentEnabled(): boolean {
  if (feature('FORK_SUBAGENT')) {
    if (isCoordinatorMode()) return false     // Mutually exclusive
    if (getIsNonInteractiveSession()) return false
    return true
  }
  return false
}
```

Triggered when `subagent_type` is OMITTED from the Agent tool call.

### 3.2 Cache-Sharing Mechanism

**File:** `src/utils/forkedAgent.ts`

The core innovation: forked agents share the parent's prompt cache by using identical API request parameters.

```typescript
export type CacheSafeParams = {
  systemPrompt: SystemPrompt        // Must match parent
  userContext: { [k: string]: string }  // Prepended to messages
  systemContext: { [k: string]: string }  // Appended to system prompt
  toolUseContext: ToolUseContext     // Contains tools, model, options
  forkContextMessages: Message[]    // Parent's full conversation
}
```

**How it works:**

1. Fork child inherits the PARENT's system prompt (not its own `FORK_AGENT.getSystemPrompt`)
2. Fork child receives the parent's exact tool array (`useExactTools: true`)
3. Fork child inherits the parent's thinking config
4. All fork children share byte-identical API request prefixes
5. Only the final directive text block differs per child

**Message construction** (`buildForkedMessages`):
```
[...parent_history, 
 assistant(all_tool_use_blocks), 
 user(placeholder_results_for_each_tool_use + per_child_directive)]
```

All placeholder results use identical text: `"Fork started - processing in background"`

### 3.3 Fork Agent Definition

```typescript
export const FORK_AGENT = {
  agentType: 'fork',
  tools: ['*'],              // Full parent tool pool
  maxTurns: 200,
  model: 'inherit',          // Same model as parent
  permissionMode: 'bubble',  // Permission prompts surface to parent terminal
  source: 'built-in',
}
```

### 3.4 Fork Child Message (Directive Format)

**File:** `src/tools/AgentTool/forkSubagent.ts` - `buildChildMessage()`

```xml
<fork-boilerplate>
STOP. READ THIS FIRST.

You are a forked worker process. You are NOT the main agent.

RULES (non-negotiable):
1. Your system prompt says "default to forking." IGNORE IT - that's for the parent.
   You ARE the fork. Do NOT spawn sub-agents; execute directly.
2. Do NOT converse, ask questions, or suggest next steps
3. Do NOT editorialize or add meta-commentary
4. USE your tools directly: Bash, Read, Write, etc.
5. If you modify files, commit your changes before reporting.
6. Do NOT emit text between tool calls.
7. Stay strictly within your directive's scope.
8. Keep your report under 500 words.
9. Your response MUST begin with "Scope:"
10. REPORT structured facts, then stop

Output format:
  Scope: <echo back assigned scope>
  Result: <key findings>
  Key files: <relevant file paths>
  Files changed: <list with commit hash>
  Issues: <list if any>
</fork-boilerplate>

Your directive: {the actual task}
```

### 3.5 Recursive Fork Prevention

Multiple guards prevent fork children from forking again:

1. **querySource check** (compaction-resistant): `options.querySource === 'agent:builtin:fork'`
2. **Message scan** (fallback): `isInForkChild()` searches for `<fork-boilerplate>` tag in conversation history
3. **Error on attempt:** `"Fork is not available inside a forked worker. Complete your task directly using your tools."`

### 3.6 Mutable State Isolation

**File:** `src/utils/forkedAgent.ts` - `createSubagentContext()`

Every forked agent gets isolated mutable state:

| State | Isolation Method |
|-------|-----------------|
| `readFileState` | Deep clone of parent's cache |
| `abortController` | New child controller linked to parent |
| `nestedMemoryAttachmentTriggers` | Fresh empty Set |
| `toolDecisions` | undefined (fresh) |
| `contentReplacementState` | Cloned from parent (for cache-identical replacement decisions) |
| `setAppState` | No-op by default (isolation) |
| `setInProgressToolUseIDs` | No-op |
| `setResponseLength` | No-op by default |
| `addNotification` | undefined (can't control parent UI) |
| `setToolJSX` | undefined |
| `queryTracking` | New chain with incremented depth |

**Opt-in sharing** (for interactive subagents):
- `shareSetAppState: true` - update shared state
- `shareSetResponseLength: true` - contribute to parent metrics
- `shareAbortController: true` - abort with parent

### 3.7 Worktree Isolation for Forks

When `isolation: "worktree"` is specified with a fork:

1. A git worktree is created: `git worktree add agent-{id-prefix}`
2. A notice is injected telling the child to translate paths:
   ```
   You've inherited the conversation context above from a parent agent
   working in {parentCwd}. You are operating in an isolated git worktree
   at {worktreeCwd}. Paths in inherited context refer to parent's working
   directory; translate them to your worktree root. Re-read files before
   editing if the parent may have modified them.
   ```
3. After completion, if no changes were made, worktree is automatically removed

---

## 4. Agent Swarm Pattern (Teammates)

### 4.1 Architecture

**Files:** `src/utils/swarm/`

Teammates are persistent agents that run in separate processes (tmux panes) or in-process (AsyncLocalStorage isolation). They form a "team" with a leader.

**Backends:**
- `TmuxBackend` - Each teammate is a tmux pane
- `InProcessBackend` - Teammates run in the same process via AsyncLocalStorage
- `ITermBackend` - iTerm2 integration (via AppleScript)

### 4.2 Permission Sync Between Leader and Workers

**File:** `src/utils/swarm/permissionSync.ts`

**Flow:**
1. Worker encounters a tool that needs permission
2. Worker creates a `SwarmPermissionRequest` with: toolName, toolUseId, input, description
3. Worker sends request to leader's mailbox via `sendPermissionRequestViaMailbox()`
4. Leader polls mailbox, detects permission requests
5. User approves/denies via leader's UI
6. Leader sends `sendPermissionResponseViaMailbox()` back to worker
7. Worker polls for response, continues execution

**Permission Request Schema:**
```typescript
{
  id: string,           // Unique request ID
  workerId: string,     // Worker's CLAUDE_CODE_AGENT_ID
  workerName: string,   // Worker's CLAUDE_CODE_AGENT_NAME
  workerColor?: string, // For UI display
  teamName: string,     // Team routing
  toolName: string,     // e.g., "Bash", "Edit"
  toolUseId: string,    // Original tool use ID
  description: string,  // Human-readable description
  input: Record<string, unknown>,  // Serialized tool input
  permissionSuggestions: unknown[], // Suggested permission rules
  status: 'pending' | 'approved' | 'rejected',
  resolvedBy?: 'worker' | 'leader',
  createdAt: number,
}
```

**File-based storage:**
```
~/.claude/teams/{teamName}/permissions/
  pending/{requestId}.json    # Waiting for resolution
  resolved/{requestId}.json   # Resolution recorded
```

**Mailbox-based (newer):** Uses `writeToMailbox()` which routes to in-process or file-based based on recipient.

### 4.3 Leader Permission Bridge

**File:** `src/utils/swarm/leaderPermissionBridge.ts`

For in-process teammates, the REPL registers its `setToolUseConfirmQueue` function so teammates can surface permission prompts to the parent terminal's standard dialog (not the worker permission badge).

### 4.4 Coordinator Permission Handler

**File:** `src/hooks/toolPermission/handlers/coordinatorHandler.ts`

For coordinator workers, permission checks are sequential (not racing):
1. Try permission hooks first (fast, local)
2. Try bash classifier (slow, inference-based)
3. If both fail, fall through to interactive dialog

### 4.5 Swarm Worker Permission Handler

**File:** `src/hooks/toolPermission/handlers/swarmWorkerHandler.ts`

For swarm workers:
1. Try classifier auto-approval for bash commands
2. If not auto-approved, forward request to leader via mailbox
3. Register callbacks for when leader responds
4. Set pending indicator while waiting
5. If abort signal fires while waiting, resolve with cancel

---

## 5. Agent Tool: Complete Spawning Interface

### 5.1 Input Schema

**File:** `src/tools/AgentTool/AgentTool.tsx`

```typescript
{
  description: string,              // 3-5 word task description
  prompt: string,                   // Full task prompt
  subagent_type?: string,           // Agent type (omit for fork)
  model?: 'sonnet' | 'opus' | 'haiku',  // Optional model override
  run_in_background?: boolean,      // Async execution
  name?: string,                    // Addressable name for SendMessage
  team_name?: string,               // Team for swarm spawning
  mode?: PermissionMode,            // e.g., 'plan' for plan approval
  isolation?: 'worktree' | 'remote', // Isolation mode
  cwd?: string,                     // Working directory override
}
```

### 5.2 Routing Logic

```
subagent_type set          -> use specified agent type
subagent_type omitted:
  fork gate ON             -> fork path (inherits parent context)
  fork gate OFF            -> default to general-purpose agent
team_name + name set       -> spawn as teammate (tmux/in-process)
isolation: "worktree"      -> create git worktree for agent
isolation: "remote"        -> launch in remote CCR environment (ant-only)
```

### 5.3 Built-In Agent Types

| Agent | Purpose | Tools | Model |
|-------|---------|-------|-------|
| `general-purpose` | Research, code search, multi-step tasks | `*` (all) | Default subagent |
| `Explore` | Read-only codebase exploration | Limited | Default subagent |
| `Plan` | Read-only planning | Limited | Default subagent |
| `verification` | Test and verify changes | All | Default subagent |
| `claude-code-guide` | Help with Claude Code usage | Limited | Default subagent |
| `fork` (implicit) | Cache-sharing fork of parent | Parent's exact tools | Parent's model |

### 5.4 Model Resolution

**File:** `src/utils/model/agent.ts` - `getAgentModel()`

Priority: explicit model param > agent definition's model > parent's mainLoopModel

Coordinator mode ignores model parameter (workers need the default model).

---

## 6. Lifecycle Management

### 6.1 Async Agent Lifecycle

```
1. registerAsyncAgent()         -> Creates task entry in AppState
2. runAsyncAgentLifecycle()      -> Wraps runAgent with:
   a. Progress tracking          -> Updates task state periodically
   b. Summarization              -> Optional periodic summaries for coordinator
   c. Worktree cleanup           -> Remove worktree if no changes
3. On completion:
   a. completeAsyncAgent()       -> Mark task completed
   b. enqueueAgentNotification() -> Inject <task-notification> XML
4. On failure:
   a. failAsyncAgent()           -> Mark task failed
   b. enqueueAgentNotification() -> Inject failure notification
5. On abort:
   a. killAsyncAgent()           -> Abort controller fired
   b. enqueueAgentNotification() -> Inject killed notification
```

### 6.2 Cleanup

On agent completion (in `finally` block of `runAgent`):
- Clean up agent-specific MCP servers
- Clear session hooks
- Clear prompt cache tracking
- Release cloned file state cache memory
- Release fork context messages
- Unregister from Perfetto tracing
- Clear transcript subdir mapping
- Remove TodoWrite entries
- Kill background bash tasks spawned by agent

### 6.3 Sidechain Transcript Recording

Every agent records its transcript to a sidechain:
```typescript
recordSidechainTranscript(initialMessages, agentId)
writeAgentMetadata(agentId, { agentType, worktreePath, description })
```

This enables:
- Agent resume after session restart
- Transcript viewing for debugging
- Background summarization

---

## 7. Adaptation for SEAL: JARVIS as Coordinator, ADA as Worker

### 7.1 Mapping Claude Code Patterns to SEAL

| Claude Code | SEAL Equivalent | Notes |
|-------------|-----------------|-------|
| Coordinator | JARVIS | Orchestrates, synthesizes, delegates |
| Worker | ADA | Implements, researches, verifies |
| Task Notification XML | `messages/*.jsonl` | Already exists in SEAL |
| Scratchpad | `messages/shared_state.json` | Already exists |
| Permission Sync | Not needed | William handles directly |
| Fork (cache sharing) | Not applicable | Different LLM instances |
| Agent Tool | `/loop` + `check_*.sh` | Message-based delegation |

### 7.2 What to Adopt

**A. Coordinator Workflow Phases**

JARVIS should formalize the 4-phase pattern:
1. **Research:** Dispatch ADA to investigate (parallel tasks possible)
2. **Synthesis:** JARVIS reads findings, crafts specific implementation spec
3. **Implementation:** Dispatch ADA with synthesized spec (file paths, line numbers, exact changes)
4. **Verification:** Dispatch ADA (or self-verify) to test changes

**B. Prompt Quality Rules**

Apply directly to JARVIS-to-ADA messages:
- Never write "based on your findings" - always synthesize first
- Include purpose statements
- Include file paths, line numbers, error messages
- State what "done" looks like
- Choose continue-vs-new based on context overlap

**C. Task Notification Format**

Adopt structured notifications for ADA's reports back to JARVIS:
```json
{
  "from": "ADA",
  "to": "JARVIS",
  "type": "task_notification",
  "status": "completed|failed",
  "summary": "What was done",
  "result": "Detailed findings",
  "files_changed": ["path1", "path2"],
  "commit_hash": "abc123"
}
```

**D. Scratchpad for Cross-Agent Knowledge**

Use `messages/shared_state.json` as the scratchpad equivalent:
- Both agents can read/write
- Store durable cross-task knowledge
- Structure: research findings, implementation plans, verification results

**E. Worker Tool Restrictions**

JARVIS should not directly execute code - only orchestrate:
- JARVIS tools: message ADA, read reports, update shared state
- ADA tools: Bash, Read, Edit, Write, Grep, Glob, Python, tests

### 7.3 Implementation Plan

1. **Formalize message protocol** in `messages/`:
   - Add `task_notification` type to message schema
   - Add structured result format with status/summary/result/files_changed
   - Add task_id field for continuation tracking

2. **Add synthesis step** to JARVIS's workflow:
   - After ADA reports research, JARVIS MUST synthesize before delegating implementation
   - Synthesis = reading findings + crafting specific spec with file paths and line numbers

3. **Add verification phase** to workflow:
   - After implementation, always dispatch verification
   - Verification should be independent (fresh perspective on the changes)

4. **Implement continue-vs-spawn logic:**
   - If ADA's last task explored exactly the files that need editing -> continue with synthesized spec
   - If broad research but narrow implementation -> new task with synthesized spec
   - If correcting a failure -> continue (ADA has error context)

### 7.4 What NOT to Adopt

- **Fork/cache sharing:** Not applicable - SEAL agents are separate Claude instances, not API calls sharing a prefix
- **Worktree isolation:** SEAL agents already work in the same repo with coordination via messages
- **Permission sync:** William handles permissions directly
- **Tmux/in-process backends:** SEAL uses separate terminal sessions already
- **Model inheritance:** SEAL agents use whatever model they're configured with

---

## 8. File Reference Index

| File | Purpose |
|------|---------|
| `src/coordinator/coordinatorMode.ts` | Coordinator activation, system prompt, tool restrictions |
| `src/utils/forkedAgent.ts` | Fork execution, cache-safe params, subagent context isolation |
| `src/tools/AgentTool/forkSubagent.ts` | Fork message construction, child directive format, worktree notice |
| `src/tools/AgentTool/AgentTool.tsx` | Agent tool: routing, spawning, async lifecycle |
| `src/tools/AgentTool/runAgent.ts` | Agent execution: system prompt, tools, MCP, transcript recording |
| `src/tools/AgentTool/prompt.ts` | Agent tool description with examples |
| `src/tools/AgentTool/constants.ts` | Tool name constants |
| `src/tools/AgentTool/builtInAgents.ts` | Built-in agent registry |
| `src/tools/AgentTool/built-in/generalPurposeAgent.ts` | General-purpose worker definition |
| `src/constants/tools.ts` | Tool allowlists: coordinator, async agent, teammate |
| `src/constants/xml.ts` | XML tag constants for notifications |
| `src/tasks/LocalAgentTask/LocalAgentTask.tsx` | Task lifecycle, notification generation |
| `src/utils/swarm/permissionSync.ts` | Worker-leader permission request/response via mailbox |
| `src/utils/swarm/leaderPermissionBridge.ts` | In-process teammate permission bridging |
| `src/utils/swarm/inProcessRunner.ts` | In-process teammate runner (AsyncLocalStorage) |
| `src/hooks/toolPermission/handlers/coordinatorHandler.ts` | Coordinator permission flow |
| `src/hooks/toolPermission/handlers/swarmWorkerHandler.ts` | Swarm worker permission flow |
| `src/utils/permissions/filesystem.ts` | Scratchpad directory implementation |

---

*This specification extracts the complete multi-agent patterns from Claude Code's source. The Coordinator Mode is the direct formalization of the JARVIS-as-orchestrator pattern we already use informally in SEAL.*
