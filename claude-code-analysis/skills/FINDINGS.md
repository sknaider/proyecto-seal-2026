# Bundled Skills Findings — Claude Code v2.1.88

Technical analysis of TypeScript skill implementations for team automation infrastructure.

---

## 1. /loop Skill — Local Cron Task Scheduling

### Internal Mechanism

`loop.ts` registers a skill that creates **local** recurring cron tasks using the `CronCreate` tool.

**Parsing Algorithm:**
1. **Leading token match** — First whitespace-delimited token matching `^\d+[smhd]$` is the interval (e.g. `5m`, `2h`). Rest is prompt.
2. **Trailing "every" clause** — If no leading token, look for trailing `every <N><unit>` or `every <N> <unit-word>` (e.g. `every 20m`, `every 5 minutes`). Extract as interval, strip from prompt.
3. **Default** — If no interval found, use `10m` (DEFAULT_INTERVAL).

**Interval → Cron Conversion:**

| Pattern | Cron Output | Notes |
|---------|-------------|-------|
| `Nm` where N ≤ 59 | `*/N * * * *` | every N minutes |
| `Nm` where N ≥ 60 | `0 */H * * *` | rounds to hours (H = N/60, must divide 24) |
| `Nh` where N ≤ 23 | `0 */N * * *` | every N hours |
| `Nd` | `0 0 */N * *` | every N days at midnight local |
| `Ns` | treat as `ceil(N/60)m` | cron minimum granularity is 1 minute |

**Key Limitation:** Non-uniform intervals (e.g., `7m` → `*/7` gives uneven gaps at :56→:00) trigger a rounding warning to the user before scheduling. The skill picks the nearest clean interval.

**Execution:**
- Calls `CronCreate` tool with parsed `cron` expression, `prompt`, and `recurring: true`
- **Immediately executes the prompt once** (doesn't wait for first cron fire) — slash commands invoked via Skill tool
- Displays job ID and explains auto-expiration (default: `DEFAULT_MAX_AGE_DAYS`)

### Key Constraints
- Minimum granularity: 1 minute
- Maximum age: 7 days (auto-delete) — must call `CronDelete` to stop earlier
- Slack-command style invocation: `/loop [interval] <prompt>`

### Useful for SEAL Team

**Example SEAL loop commands:**
```
/loop 5m Ejecuta: bash ~/IA/proyecto-seal/messages/check_ada.sh — si dice NEW, lee los mensajes nuevos
/loop 2m /babysit-prs
/loop 1h Revisa estado del entrenamiento y temperatura GPU
```

These would run as persistent background tasks in the active Claude Code session, creating a coordinator-level monitor without requiring remote infrastructure.

---

## 2. /batch Skill — Parallel Worktree Agent Orchestration

### Architecture

`batch.ts` implements a **three-phase workflow** for large-scale mechanical changes across a codebase using isolated git worktrees.

**Prerequisites:**
- Must be in a git repository (checked via `getIsGit()`)
- Requires 5–30 parallel agents (configurable via MIN_AGENTS / MAX_AGENTS)
- Each agent gets an isolated worktree + independent task

### Phase 1: Research & Plan (Plan Mode)

1. **Enter plan mode** — Call `EnterPlanModeTool`
2. **Research scope** — Launch subagents (foreground) to explore all files, patterns, and conventions
3. **Decompose work** — Break into 5–30 self-contained units, each:
   - Independently implementable in isolated git worktree
   - Mergeable on its own (no inter-unit dependencies)
   - Roughly uniform in size
4. **Determine e2e test recipe** — How workers verify changes actually work:
   - `claude-in-chrome` skill for UI changes
   - `tmux` / CLI-verifier for CLI changes
   - Dev-server + curl pattern for API changes
   - Existing e2e/integration test suite
   - Use `AskUserQuestion` tool if no concrete path found
5. **Write plan file** — Include research summary, numbered work units (title, file list, description), e2e recipe, worker instructions

### Phase 2: Spawn Workers (After Approval)

1. Launch **one background agent per work unit** using `AgentTool`
2. **Critical requirement:** All agents must use `isolation: "worktree"` + `run_in_background: true`
3. Each agent receives fully self-contained prompt:
   - Overall goal
   - This unit's specific task (title, file list, change description — verbatim from plan)
   - Codebase conventions discovered
   - e2e test recipe
   - Worker instructions (shared template)

**Worker Instructions (Embedded):**
1. Implement the change
2. Invoke `simplify` skill (code review via Skill tool)
3. Run unit tests (check package.json scripts, Makefile targets, `npm test`, `bun test`, `pytest`, `go test`)
4. Test e2e per recipe
5. Commit with clear message + push
6. Create PR with `gh pr create` (descriptive title)
7. End with `PR: <url>` line (or `PR: none — <reason>`)

### Phase 3: Track Progress

- Render initial status table (# | Unit | Status | PR)
- Parse `PR: <url>` line from each background agent
- Re-render table as notifications arrive
- Final summary: "22/24 units landed as PRs"

### Key Parameters

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| MIN_AGENTS | const | 5 | Minimum parallelism |
| MAX_AGENTS | const | 30 | Maximum parallelism |
| Worktree isolation | required | — | Each agent is sandboxed; PRs merge independently |
| disableModelInvocation | const | true | User must explicitly request `/batch` |

### Surprising Behavior

- **Agents run in background** — notifications arrive asynchronously; coordinator re-renders table as PRs land
- **No inter-agent communication** — each unit must be truly independent; dependencies block PR landing
- **Synchronous plan entry, async worker exit** — plan is approved before spawning workers; workers' async completion drives final table update

### Useful for SEAL Team

**Potential SEAL batch tasks:**
```
/batch add comprehensive logging to all medical AI inference functions
/batch migrate all Spanish medical dataset loaders from polars to DuckDB
/batch add HIPAA audit timestamps to all data access functions
```

**Constraint for SEAL:** Each unit must be mergeable without waiting for sibling units; difficult for tightly-coupled ML training pipelines.

---

## 3. /schedule Skill — Remote Agent Scheduling (Cloud Cron)

### Scope

`scheduleRemoteAgents.ts` manages **remote triggers** — cloud-hosted Claude Code agents that run on a cron schedule in Anthropic's CCR (Cloud Code Runtime) infrastructure.

**Not local cron:** Runs in sandboxed remote environment, not on user's machine.

### API Actions (via RemoteTrigger Tool)

```typescript
RemoteTrigger tool actions:
- list()                    // GET /v1/code/triggers
- get(trigger_id)           // GET /v1/code/triggers/{trigger_id}
- create(body)              // POST /v1/code/triggers
- update(trigger_id, body)  // POST /v1/code/triggers/{trigger_id}
- run(trigger_id)           // POST /v1/code/triggers/{trigger_id}/run
```

**Cannot delete triggers** — users directed to https://claude.ai/code/scheduled

### Create Trigger Shape

```json
{
  "name": "AGENT_NAME",
  "cron_expression": "CRON_EXPR",
  "enabled": true,
  "job_config": {
    "ccr": {
      "environment_id": "ENVIRONMENT_ID",
      "session_context": {
        "model": "claude-sonnet-4-6",
        "sources": [
          {"git_repository": {"url": "https://github.com/ORG/REPO"}}
        ],
        "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
      },
      "events": [
        {"data": {
          "uuid": "<lowercase v4 uuid>",
          "session_id": "",
          "type": "user",
          "parent_tool_use_id": null,
          "message": {"content": "PROMPT_HERE", "role": "user"}
        }}
      ]
    }
  },
  "mcp_connections": [
    {"connector_uuid": "uuid", "name": "server-name", "url": "https://..."}
  ]
}
```

**Important:** Must generate fresh UUID yourself in `events[].data.uuid`.

### Cron Constraints

- **Minimum interval:** 1 hour (e.g., `*/30 * * * *` rejected)
- **Always UTC** — user provides local time; skill converts to UTC
- Standard 5-field cron: `minute hour day-of-month month day-of-week`

### Workflow

**CREATE:**
1. Understand goal — what should remote agent do?
2. Craft prompt — specific, clear, explicit actions (PRs, commits, analysis)
3. Set schedule — convert local time to UTC
4. Choose model — default `claude-sonnet-4-6`
5. Validate connections — infer needed MCP connectors from description; warn if missing
6. Review and confirm
7. Create and link: https://claude.ai/code/scheduled/{TRIGGER_ID}

**UPDATE:**
1. List triggers
2. Pick one, ask what to change
3. Show current vs proposed
4. Confirm and update

**LIST:**
1. Fetch all triggers
2. Display human-readable format (name, schedule, enabled/disabled, next run, repos)

**RUN NOW:**
1. List if not specified
2. Confirm which trigger
3. Execute immediately

### MCP Connector Management

- Reads from user's connected `claudeai-proxy` MCP servers
- Sanitizes connector names (removes "claude.ai" prefix, replaces invalid chars)
- Deduplicates by `connector_uuid`
- Available connectors listed in prompt; warn if user requests unavailable service

### Setup Preconditions Checked

1. **Environments** — Fetches list; creates default if none exist
2. **GitHub access** — Checks if repo has Claude GitHub App installed (or `/web-setup` needed)
3. **Git repo** — Detects current repo; converts to HTTPS URL
4. **Auth** — Requires claude.ai account token (API accounts not supported)

### Surprising Behavior

- **Base58-encoded tagged IDs** — MCP connector IDs are `mcpsrv_01{base58(uuid.int)}` format; skill decodes client-side
- **Setup notes surface early** — Displayed in `AskUserQuestion` dialog before user picks action (CREATE/UPDATE/LIST/RUN)
- **Remote isolation** — Agent cannot access local files, local services, or local env vars; everything must be self-contained in prompt

### Useful for SEAL Team

**Example remote trigger for SEAL:**
```
/schedule
  name: SEAL Daily Medical AI Validation
  cron: "0 2 * * *"  (2am UTC = 9pm Peru time)
  prompt: "Run validation suite on medical-ai-spark model. Check HIPAA compliance, run test cases, post results to Slack."
  environment: medical-ai-spark
  mcp_connections: [Slack connector]
```

**Key advantage:** SEAL could offload nightly validation runs to remote cloud infrastructure without keeping local machine running.

---

## 4. /update-config Skill — Settings Management

### Scope

`updateConfig.ts` provides structured prompts for modifying `settings.json` files (user, project, local scopes).

**Not a direct config tool** — generates comprehensive prompt explaining settings schema, hooks, and merge semantics.

### Settings File Hierarchy

| File | Scope | Git | Use |
|------|-------|-----|-----|
| `~/.claude/settings.json` | Global | N/A | Personal preferences for all projects |
| `.claude/settings.json` | Project | Commit | Team-wide hooks, permissions, plugins |
| `.claude/settings.local.json` | Project | Gitignore | Personal overrides for this project |

**Load order:** user → project → local (later overrides earlier)

### Configurable Sections

1. **Permissions** — allow/deny/ask rules
   ```json
   {
     "permissions": {
       "allow": ["Bash(npm:*)", "Edit(.claude)", "Read"],
       "deny": ["Bash(rm -rf:*)"],
       "ask": ["Write(/etc/*)"],
       "defaultMode": "default|plan|acceptEdits|dontAsk",
       "additionalDirectories": ["/extra/dir"]
     }
   }
   ```

2. **Environment Variables**
   ```json
   { "env": { "DEBUG": "true", "MY_API_KEY": "value" } }
   ```

3. **Model & Agent**
   ```json
   {
     "model": "sonnet|opus|haiku|<full-model-id>",
     "agent": "agent-name",
     "alwaysThinkingEnabled": true
   }
   ```

4. **Attribution** (Commits & PRs)
   ```json
   {
     "attribution": {
       "commit": "Custom commit trailer text",
       "pr": "Custom PR description text"
     }
   }
   ```

5. **MCP Server Management**
   ```json
   {
     "enableAllProjectMcpServers": true,
     "enabledMcpjsonServers": ["server1", "server2"],
     "disabledMcpjsonServers": ["blocked-server"]
   }
   ```

6. **Plugins**
   ```json
   {
     "enabledPlugins": {
       "formatter@anthropic-tools": true
     }
   }
   ```

7. **Other** — language, cleanupPeriodDays, respectGitignore, spinnerTips*, syntaxHighlightingDisabled

### Hooks System (Critical)

**Hooks run commands/prompts/agents at lifecycle events.** These are automation primitives for "from now on when X happens, do Y."

**Hook Events:**

| Event | Matcher | Purpose |
|-------|---------|---------|
| PreToolUse | Tool name | Run before tool, can block |
| PostToolUse | Tool name | Run after successful tool |
| PostToolUseFailure | Tool name | Run after tool fails |
| PermissionRequest | Tool name | Run before permission prompt |
| Notification | Notification type | Run on notifications |
| Stop | — | Run when Claude stops |
| PreCompact | "manual"/"auto" | Before compaction |
| PostCompact | "manual"/"auto" | After compaction |
| UserPromptSubmit | — | When user submits |
| SessionStart | — | When session starts |

**Hook Structure:**
```json
{
  "hooks": {
    "EVENT_NAME": [
      {
        "matcher": "ToolName|OtherTool",
        "hooks": [
          {
            "type": "command|prompt|agent",
            "command": "your-command",
            "timeout": 60,
            "statusMessage": "Running..."
          }
        ]
      }
    ]
  }
}
```

**Hook Types:**
- **command** — Shell command (stdin receives JSON with tool metadata)
- **prompt** — LLM evaluates condition (PostToolUse, PreToolUse, PermissionRequest only)
- **agent** — Sub-agent with tools (PostToolUse, PreToolUse, PermissionRequest only)

**Hook JSON Output** (from command/prompt/agent):
```json
{
  "systemMessage": "Warning shown to user",
  "continue": false,
  "stopReason": "Message when blocking",
  "suppressOutput": false,
  "decision": "block",
  "reason": "Explanation",
  "hookSpecificOutput": {
    "hookEventName": "PostToolUse",
    "additionalContext": "Context injected to model",
    "permissionDecision": "allow|deny|ask"
  }
}
```

### Common Hook Patterns (Provided in Skill)

**Auto-format after writes:**
```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_response.filePath // .tool_input.file_path' | { read -r f; prettier --write \"$f\"; } 2>/dev/null || true"
      }]
    }]
  }
}
```

**Log all bash commands:**
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_input.command' >> ~/.claude/bash-log.txt"
      }]
    }]
  }
}
```

**Run tests after code changes:**
```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Write|Edit",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_input.file_path // .tool_response.filePath' | grep -E '\\.(ts|js)$' && npm test || true"
      }]
    }]
  }
}
```

### Hook Verification Workflow (Embedded in Skill)

1. **Dedup check** — Read target file; ask if hook already exists on same event+matcher
2. **Construct command** — Build raw (no error suppression yet) command that safely extracts JSON stdin
3. **Pipe-test** — Synthesize stdin payload and run raw command; verify exit code and side effect
4. **Write JSON** — Merge into target file with Edit tool; gitignore `.claude/settings.local.json` if new
5. **Validate syntax** — `jq -e '.hooks.<event>[]...'` to catch malformation
6. **Prove firing** — Only for PreToolUse/PostToolUse on triggerable matchers; add sentinel, trigger tool, read sentinel, clean up
7. **Handoff** — Tell user hook is live; point to `/hooks` UI menu for management

### Critical Behaviors

- **Always read before write** — Never replace entire file
- **Merge arrays** — Never replace permissions or hooks arrays; always preserve existing
- **Schema validation** — Generated dynamically from Zod schema via `toJSONSchema()`
- **Config tool vs direct edit:**
  - Use Config tool for simple settings (theme, model, language)
  - Edit settings.json directly for hooks, permissions, env vars, MCP config

### Useful for SEAL Team

**Example hook for SEAL medical AI training:**
```json
{
  "hooks": {
    "PostToolUse": [{
      "matcher": "Bash",
      "hooks": [{
        "type": "command",
        "command": "jq -r '.tool_input.command' | grep -q 'medgemma.*train' && echo '{}' >> ~/IA/proyecto-seal/training_log.jsonl || true"
      }]
    }]
  }
}
```

This logs all training commands autonomously without Claude thinking about it.

---

## 5. /remember Skill — Memory Organization

### Scope

`remember.ts` reviews auto-memory entries and proposes promotions to CLAUDE.md / CLAUDE.local.md / team memory.

**Permission:** ANT-ONLY (USER_TYPE === 'ant')

### Workflow

1. **Gather memory layers** — Read CLAUDE.md, CLAUDE.local.md, and review auto-memory from system prompt
2. **Classify entries** — Determine best destination for each auto-memory entry:
   - **CLAUDE.md** — Project conventions for all contributors (e.g., "use bun not npm", "API routes use kebab-case")
   - **CLAUDE.local.md** — Personal instructions (e.g., "I prefer concise responses", "don't auto-commit")
   - **Team memory** — Org-wide knowledge (only if team memory configured)
   - **Stay in auto-memory** — Temporary context, uncertain patterns
3. **Identify cleanup** — Duplicates, outdated entries, conflicts
4. **Present report** — Grouped by action type (Promotions, Cleanup, Ambiguous, No action)

### Key Distinctions

- Memory instructions ≠ user preferences for external tools (editor theme, IDE keybindings)
- Workflow practices (PR conventions) are ambiguous — ask user if personal or team-wide
- When unsure, ask rather than guess

### Output Structure

```
## Promotions
- Entry X → CLAUDE.md (rationale)
- Entry Y → CLAUDE.local.md (rationale)

## Cleanup
- Duplicate Z (found in CLAUDE.md, remove from auto-memory)
- Outdated W (contradicted by auto-memory entry V, update CLAUDE.md)
- Conflict P ↔ Q (resolve which is more recent)

## Ambiguous
- Entry A (ask user if personal or team-wide)

## No Action Needed
- Brief note on entries staying put
```

### Useful for SEAL Team

After training the medical AI model, SEAL agents could run `/remember` to consolidate session learnings into shared team memory:
- Training parameters that worked → CLAUDE.md
- Personal debugging preferences → CLAUDE.local.md
- Regulatory requirements discovered → Team memory

---

## 6. Other Notable Skills

### /simplify — Code Review via Parallel Agents

`simplify.ts` launches **three parallel agents** to review code changes:

1. **Agent 1: Code Reuse Review** — Flags duplicates, suggests existing utilities
2. **Agent 2: Code Quality Review** — Detects hacky patterns (redundant state, parameter sprawl, copy-paste, leaky abstractions, stringly-typed code)
3. **Agent 3: Efficiency Review** — Finds unnecessary work, missed concurrency, hot-path bloat, memory leaks

All agents receive full diff. Coordinator waits for completion, aggregates findings, fixes each issue.

**Invocation:** `Skill tool with skill: "simplify"` (embedded in batch worker instructions)

### /verify — Run Application to Verify Changes (ANT-ONLY)

`verify.ts` runs an app to verify a code change does what it should.

**Content:** Loads from `verifyContent.js` (separate file with dynamic markdown)

**Permission:** ANT-ONLY

### /debug — Debug Current Session

`debug.ts` reads session debug log (tails last 20 lines by default, reads up to 64KB) and helps diagnose issues.

**Key feature:** Lazily enables debug logging if not already on; captures subsequent activity in session.

**Provides:** Debug log path, last N lines, guidance on [ERROR] and [WARN] patterns.

### /stuck — Diagnose Frozen Sessions (ANT-ONLY)

`stuck.ts` investigates frozen/stuck/slow Claude Code processes on the machine.

**What it looks for:**
- High CPU (≥90% sustained)
- Process state D (uninterruptible sleep — I/O hang)
- Process state T (stopped — Ctrl+Z)
- Process state Z (zombie)
- Very high RSS (≥4GB — possible memory leak)
- Stuck child processes (git, node, shell)

**Output:** Posts diagnostic report to Slack #claude-code-feedback (two-message structure: top-level symptom, threaded details).

### /keybindings-help — Customize Keyboard Shortcuts

`keybindings.ts` manages `~/.claude/keybindings.json` customization.

**Dynamic generation** — Reference tables built from source-of-truth arrays (contexts, actions, reserved shortcuts).

**Features:**
- Keystroke syntax (modifiers: ctrl, alt, shift, meta; special keys; chords)
- Unbinding (set to null)
- Validation (via /doctor command)
- Reserved shortcuts warning (terminal, OS-level)

**Example:** `ctrl+k ctrl+t` (chord binding)

### /skillify — Capture Session as Reusable Skill (ANT-ONLY)

`skillify.ts` converts a session's repeatable process into a reusable SKILL.md file.

**Four rounds of user interview:**
1. Confirm name, description, goals
2. Present steps, discuss forking vs inline, pick save location (repo-specific or personal)
3. Break down each step (success criteria, data dependencies, parallelism, execution model)
4. Confirm trigger phrases and gotchas

**Output:** SKILL.md with frontmatter + body in `.claude/skills/<name>/` or `~/.claude/skills/<name>/`

### /claude-api — Build Apps with Claude API

`claudeApi.ts` provides language-specific documentation for Claude API / Anthropic SDK.

**Language detection** — Scans cwd for file extensions / config files (tsconfig.json, requirements.txt, etc.)

**Content bundled** — Lazy-loads 247KB markdown from `claudeApiContent.js`; includes README, streaming, batches, tool-use, files API, error codes.

**Common task references** — Quick nav to docs based on task (chat UI, batches, function calling, agent with built-in tools, file uploads, etc.)

---

## 7. Architecture Patterns & Infrastructure

### Skill Registration

All skills register via `registerBundledSkill()` function:

```typescript
registerBundledSkill({
  name: 'skill-name',
  description: '...',
  whenToUse: '...',
  argumentHint: '[optional]',
  userInvocable: true,
  isEnabled: () => featureFlag(...),
  allowedTools: ['Read', 'Bash'],
  async getPromptForCommand(args: string, context?: ToolUseContext) {
    return [{ type: 'text', text: builtPrompt }]
  },
})
```

### Feature Flags

Skills lazy-load via feature flags:
```typescript
if (feature('AGENT_TRIGGERS')) {
  const { registerLoopSkill } = require('./loop.js')
  registerLoopSkill()
}
```

This keeps disabled skills out of memory until needed.

### Tool Dependencies

Many skills declare `allowedTools` to constrain permissions:

| Skill | Allowed Tools |
|-------|---------------|
| loop | (none — uses CronCreate) |
| batch | (uses EnterPlanModeTool, ExitPlanModeTool, AgentTool, SkillTool) |
| schedule | RemoteTriggerTool, AskUserQuestion |
| update-config | Read (for reading existing settings) |
| simplify | AgentTool (for parallel review agents) |
| debug | Read, Grep, Glob |
| keybindings-help | Read |
| verify | (from verifyContent.js) |
| claude-api | Read, Grep, Glob, WebFetch |

### Context Passing

Skills receive `ToolUseContext` to access:
- `context.options.mcpClients` — List of connected MCP servers
- `context.messages` — Message history (for skillify)

---

## 8. Surprising Behaviors & Limitations

### /loop Limitations

1. **Only 1-minute granularity** — Seconds rounded up; no sub-minute cron
2. **Auto-expire after 7 days** — Cannot schedule permanent recurring tasks
3. **Immediate execution** — Prompt runs once **now**, then on cron schedule (not just cron schedule)
4. **Non-uniform intervals warned** — `7m` → `*/7` gives uneven gaps; user is told what was rounded

### /batch Limitations

1. **No inter-agent communication** — Each PR must merge independently; tightly coupled tasks fail
2. **Async worker completion** — Can't synchronously wait; notifications drive table updates
3. **No task cancellation** — Once agents spawned, must wait for all to complete or manually interrupt
4. **disableModelInvocation: true** — Cannot auto-trigger /batch (user must explicitly invoke)

### /schedule Limitations

1. **Minimum 1-hour interval** — No sub-hourly remote triggers
2. **UTC-only cron** — Must convert local time to UTC (skill does conversion, but user must confirm)
3. **Cannot delete via API** — Users directed to https://claude.ai/code/scheduled
4. **Requires claude.ai account** — API accounts not supported
5. **Base58-encoded connector IDs** — Client-side decoding required (internal implementation detail)

### /update-config Limitations

1. **Schema mutation is complex** — JSON deeply nested; errors silently disable entire settings file
2. **Hook watcher limitation** — Doesn't watch `.claude/` if settings file didn't exist when session started (user must open `/hooks` or restart)
3. **No validation before write** — Hook command syntax checked only after file write (though `jq` validation provided in workflow)
4. **Copy-paste error-prone** — jq stdin extraction fragile if not done carefully

### MCP Connector Decoding (schedule skill)

- Connector IDs tagged as `mcpsrv_01{base58(uuid.int)}`
- Skill decodes base58 → uuid on client side
- Base58 alphabet: Bitcoin-style (no 0, O, I, l to avoid confusion)
- Todo: Before shipping publicly, API should return raw UUID directly (this is noted in source)

### Hook JSON Output Handling

- Hooks can return complex nested `hookSpecificOutput` with event-specific actions
- `permissionDecision: "allow"|"deny"|"ask"` (PreToolUse only)
- `additionalContext` injected back to model context (PostToolUse)
- If hook errors silently, no feedback in UI (only shown if hook is slow or errors)

---

## 9. Recommended SEAL Team Integration

### Immediate Use Cases

1. **Medical AI Training Monitoring**
   ```
   /loop 5m Check GPU temperature and training loss on DGX Spark
   ```

2. **Daily Model Validation**
   ```
   /schedule
     name: Medical AI Daily Validation
     cron: "0 2 * * *"
     prompt: Validate medical-ai-spark model against test cases, check HIPAA compliance
   ```

3. **Batch Documentation Translations**
   ```
   /batch Translate all Spanish medical prompts to English with medical accuracy preservation
   ```

4. **Configuration Persistence**
   ```
   /update-config [hooks-only]
   Add hook: log all medical AI inference functions to audit trail
   ```

5. **Session Memory Consolidation**
   ```
   /remember
   (After training session: promotes hyperparameters to CLAUDE.md, debugging tips to CLAUDE.local.md)
   ```

### Architecture Recommendation

- **Local cron (JARVIS/ADA monitoring)** → `/loop` skill (5–30 min intervals, auto-expires in 7 days)
- **Cloud cron (nightly validation)** → `/schedule` skill (1+ hour intervals, persistent)
- **Bulk refactoring** → `/batch` skill (decomposes into 5–30 worktree agents, one PR per unit)
- **Team memory** → `/remember` skill (consolidates session learnings into CLAUDE.md)
- **Automation rules** → `/update-config` skill with PostToolUse/PreToolUse hooks

### Known Friction Points for SEAL

1. **Loop auto-expiration** — 7-day limit means long-running monitoring requires manual refresh
2. **Schedule minimum interval** — No sub-hourly remote validation (1-hour minimum)
3. **Batch complexity** — Decomposing medical AI training into independent units is hard
4. **Hook silent failure** — If settings watcher misses initial load, hook never fires (user must restart)

---

## 10. Summary Table

| Skill | Scope | Interval | Infrastructure | Multi-agent | Useful for SEAL |
|-------|-------|----------|-----------------|-------------|---|
| /loop | Local cron | 1m–auto-expire 7d | CronCreate tool | No | JARVIS/ADA monitors, coordinators |
| /batch | Parallel worktrees | N/A | EnterPlanMode + AgentTool | Yes (5–30) | Large refactors, bulk operations |
| /schedule | Remote cloud cron | 1h–infinite | RemoteTriggerTool + CCR | No | Nightly validation, async reports |
| /update-config | Settings.json | N/A | Edit + Read | No | Hooks, permissions, env vars |
| /remember | Memory org | N/A | Auto-memory review | No | Team consolidation |
| /simplify | Code review | N/A | 3 parallel agents | Yes (3) | Post-implementation cleanup |
| /verify | App verification | N/A | Custom (ANT-ONLY) | No | E2E testing |
| /debug | Session diagnostics | N/A | Debug log tail | No | Troubleshooting |
| /stuck | Process diagnostics | N/A | Slack MCP | No | External monitoring |
| /keybindings-help | Keyboard config | N/A | Read + Edit | No | Productivity tuning |
| /skillify | Session → skill | N/A | Interview + Write | No | Workflow capture (ANT-ONLY) |
| /claude-api | API docs | N/A | WebFetch | No | Building Claude apps |

---

*Analysis completed: March 31, 2026*
*Target: SEAL team automation infrastructure design*
