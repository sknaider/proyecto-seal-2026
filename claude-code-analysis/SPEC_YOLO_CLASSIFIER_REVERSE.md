# YOLO Classifier (Auto Permission Mode) - Reverse Engineering Spec

**Source:** `open-claude-code-gitlab/` (Open Source Claude Code)
**Date:** 2026-04-10
**Purpose:** Blueprint for SEAL Python adaptation

---

## 1. Architecture Overview

```
                         ┌──────────────────────────────────┐
                         │     User Tool Request            │
                         │  (e.g., Bash("rm -rf /tmp"))     │
                         └──────────────┬───────────────────┘
                                        │
                                        ▼
                         ┌──────────────────────────────────┐
                         │  permissions.ts                   │
                         │  hasPermissionsToUseTool()        │
                         │                                   │
                         │  Step 1: Rule-based checks        │
                         │    - deny rules                   │
                         │    - ask rules                    │
                         │    - always-allow rules           │
                         │    - safety checks                │
                         │                                   │
                         │  Step 2: Mode-based transform     │
                         │    if mode == "auto":             │
                         └──────────────┬───────────────────┘
                                        │
                    ┌───────────────────┼───────────────────────┐
                    │                   │                       │
                    ▼                   ▼                       ▼
         ┌──────────────┐   ┌──────────────────┐   ┌──────────────────┐
         │ Safety Check  │   │ acceptEdits      │   │ Tool Allowlist   │
         │ (non-approv-  │   │ Fast Path        │   │ Check            │
         │  able)        │   │                  │   │                  │
         │  → PROMPT     │   │ Simulates        │   │ SAFE_YOLO_       │
         │    USER       │   │ acceptEdits mode │   │ ALLOWLISTED_     │
         │               │   │ to skip API call │   │ TOOLS set        │
         └───────────────┘   │ if would allow   │   │  → ALLOW         │
                             └────────┬─────────┘   └──────────────────┘
                                      │ (if still 'ask')
                                      ▼
                         ┌──────────────────────────────────┐
                         │  classifyYoloAction()             │
                         │  yoloClassifier.ts                │
                         └──────────────┬───────────────────┘
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              │ (tool_use mode)         │ (XML 2-stage mode)      │
              ▼                         ▼                         │
    ┌──────────────────┐  ┌─────────────────────┐                 │
    │ Single API Call   │  │ Stage 1: FAST       │                 │
    │ tool_choice:      │  │ max_tokens=64       │                 │
    │  classify_result  │  │ stop=["</block>"]   │                 │
    │ max_tokens=4096   │  │ temp=0              │                 │
    │ temp=0            │  │                     │                 │
    │                   │  │ Suffix: "Err on the │                 │
    │ Returns JSON:     │  │ side of blocking.   │                 │
    │ {thinking,        │  │ <block> immediately."│                │
    │  shouldBlock,     │  │                     │                 │
    │  reason}          │  │ Output: <block>     │                 │
    └──────────────────┘  │ yes/no</block>      │                 │
                           └─────────┬───────────┘                 │
                                     │                             │
                          ┌──────────┴──────────┐                  │
                          │                     │                  │
                     ALLOW (no)            BLOCK (yes)             │
                          │                     │                  │
                          ▼                     ▼                  │
                    Return allowed     ┌──────────────────┐        │
                    immediately        │ Stage 2: THINKING│        │
                                       │ max_tokens=4096  │        │
                                       │ temp=0           │        │
                                       │                  │        │
                                       │ Suffix: "Review  │        │
                                       │ the classific-   │        │
                                       │ ation process    │        │
                                       │ and follow it    │        │
                                       │ carefully..."    │        │
                                       │                  │        │
                                       │ Output:          │        │
                                       │ <thinking>...</>  │        │
                                       │ <block>yes/no</>  │        │
                                       │ <reason>...</>    │        │
                                       └────────┬─────────┘        │
                                                │                  │
                                                ▼                  │
                                        Final decision             │
                                        (may override              │
                                         Stage 1 block)            │
                                                                   │
                         ┌──────────────────────────────────┐      │
                         │  Post-Classifier Logic            │      │
                         │                                   │      │
                         │  if BLOCKED:                      │      │
                         │    - Track denial                 │      │
                         │    - Check denial limits          │      │
                         │      (3 consecutive / 20 total)   │      │
                         │    - If limit hit → PROMPT USER   │      │
                         │    - Else → DENY with reason      │      │
                         │                                   │      │
                         │  if ALLOWED:                      │      │
                         │    - Reset consecutive denials    │      │
                         │    - Return ALLOW                 │      │
                         │                                   │      │
                         │  if UNAVAILABLE (API error):      │      │
                         │    - iron_gate_closed=true → DENY │      │
                         │    - iron_gate_closed=false →     │      │
                         │      fall back to normal prompt   │      │
                         │                                   │      │
                         │  if TRANSCRIPT_TOO_LONG:          │      │
                         │    - Fall back to user prompt     │      │
                         └──────────────────────────────────┘      │
```

---

## 2. File Map

| File | Role |
|------|------|
| `src/utils/permissions/yoloClassifier.ts` | Core classifier: prompt construction, API calls, 2-stage logic, transcript building |
| `src/utils/permissions/classifierDecision.ts` | Tool allowlist (safe tools that skip the classifier) |
| `src/utils/permissions/classifierShared.ts` | Shared utilities: extractToolUseBlock, parseClassifierResponse |
| `src/utils/permissions/autoModeState.ts` | Module-scope boolean state: active, cliFlag, circuitBroken |
| `src/utils/permissions/permissions.ts` | Integration point: hasPermissionsToUseTool() orchestrates the full pipeline |
| `src/utils/permissions/denialTracking.ts` | Circuit breaker: consecutive/total denial tracking and fallback logic |
| `src/utils/permissions/bashClassifier.ts` | Bash-specific semantic classifier (stub in open-source build) |
| `src/utils/permissions/dangerousPatterns.ts` | Dangerous shell patterns stripped from allow rules at auto-mode entry |
| `src/utils/permissions/bypassPermissionsKillswitch.ts` | GrowthBook gate verification for auto mode availability |
| `src/utils/autoModeDenials.ts` | Ring buffer of recent denials (for UI display) |
| `src/utils/classifierApprovals.ts` | Tracking which tool_use IDs were auto-approved by classifiers |
| `src/utils/classifierApprovalsHook.ts` | React hook for UI indicator of classifier checking state |
| `src/cli/handlers/autoMode.ts` | CLI subcommands: `auto-mode defaults`, `auto-mode config`, `auto-mode critique` |
| `src/types/permissions.ts` | Type definitions: YoloClassifierResult, ClassifierUsage, PermissionMode |
| `src/utils/settings/settings.ts` | getAutoModeConfig(): reads user settings for allow/soft_deny/environment rules |

---

## 3. Classifier System Prompt Architecture

The system prompt is assembled from **3 files** (loaded at build time, NOT in the open-source repo):

1. **`auto_mode_system_prompt.txt`** - Base prompt with `<permissions_template>` placeholder
2. **`permissions_external.txt`** - Default rules for external/public users
3. **`permissions_anthropic.txt`** - Rules for Anthropic internal users (ant builds)

### Prompt Assembly Logic

```python
# Pseudocode of buildYoloSystemPrompt()

system_prompt = BASE_PROMPT.replace(
    '<permissions_template>',
    EXTERNAL_PERMISSIONS_TEMPLATE if is_external else ANTHROPIC_PERMISSIONS_TEMPLATE
)

# User rules from settings.json "autoMode" section:
user_allow = autoMode.allow + bash_prompt_allow_descriptions
user_deny = autoMode.soft_deny + bash_prompt_deny_descriptions + powershell_guidance
user_env = autoMode.environment

# Replace tagged sections (user values REPLACE defaults in external template):
system_prompt = system_prompt
    .replace('<user_allow_rules_to_replace>...defaults...</>', user_allow or defaults)
    .replace('<user_deny_rules_to_replace>...defaults...</>', user_deny or defaults)
    .replace('<user_environment_to_replace>...defaults...</>', user_env or defaults)
```

### Template Tag Semantics

- **External template**: Default rules live INSIDE the `<user_*_to_replace>` tags. User-provided values **REPLACE** the defaults entirely.
- **Anthropic template**: Default rules live OUTSIDE the tags. User-provided values are **ADDITIVE** (appended).

### CLAUDE.md Integration

The user's CLAUDE.md configuration file is injected as a **prefix message** (not system prompt) with cache_control:

```python
{
    "role": "user",
    "content": [{
        "type": "text",
        "text": "The following is the user's CLAUDE.md configuration. "
                "These are instructions the user provided to the agent "
                "and should be treated as part of the user's intent when "
                "evaluating actions.\n\n"
                "<user_claude_md>\n{claude_md_content}\n</user_claude_md>",
        "cache_control": {"type": "ephemeral"}
    }]
}
```

---

## 4. User Settings Configuration

Location: `settings.json` under `autoMode` key

```json
{
  "autoMode": {
    "allow": [
      "Read-only git operations like git status, git log, git diff",
      "Running test suites with pytest or npm test"
    ],
    "soft_deny": [
      "Any command that modifies system configuration files",
      "Network operations that upload data"
    ],
    "environment": [
      "This is a Python project using FastAPI",
      "The database is PostgreSQL on port 5433"
    ]
  }
}
```

**Sources merged** (in order): `userSettings`, `localSettings`, `flagSettings`, `policySettings`
**Excluded**: `projectSettings` (security: malicious project could inject rules for RCE)

CLI commands:
- `claude auto-mode defaults` - Show default rules
- `claude auto-mode config` - Show effective merged config
- `claude auto-mode critique` - AI review of user custom rules

---

## 5. Tool Allowlist (Pre-Approved, No Classifier Needed)

These tools skip the classifier entirely and are auto-approved:

```python
SAFE_YOLO_ALLOWLISTED_TOOLS = {
    # Read-only file operations
    "Read",                    # FILE_READ_TOOL_NAME

    # Search / read-only
    "Grep",                    # GREP_TOOL_NAME
    "Glob",                    # GLOB_TOOL_NAME
    "LSP",                     # LSP_TOOL_NAME
    "ToolSearch",              # TOOL_SEARCH_TOOL_NAME
    "ListMcpResources",        # LIST_MCP_RESOURCES_TOOL_NAME
    "ReadMcpResourceTool",     # no exported constant

    # Task management (metadata only)
    "TodoWrite",               # TODO_WRITE_TOOL_NAME
    "TaskCreate",              # TASK_CREATE_TOOL_NAME
    "TaskGet",                 # TASK_GET_TOOL_NAME
    "TaskUpdate",              # TASK_UPDATE_TOOL_NAME
    "TaskList",                # TASK_LIST_TOOL_NAME
    "TaskStop",                # TASK_STOP_TOOL_NAME
    "TaskOutput",              # TASK_OUTPUT_TOOL_NAME

    # Plan mode / UI
    "AskUserQuestion",         # ASK_USER_QUESTION_TOOL_NAME
    "EnterPlanMode",           # ENTER_PLAN_MODE_TOOL_NAME
    "ExitPlanMode",            # EXIT_PLAN_MODE_TOOL_NAME

    # Swarm coordination (teammates have own permission checks)
    "TeamCreate",              # TEAM_CREATE_TOOL_NAME
    "TeamDelete",              # TEAM_DELETE_TOOL_NAME
    "SendMessage",             # SEND_MESSAGE_TOOL_NAME

    # Workflow orchestration (subagents go through canUseTool individually)
    "Workflow",                # WORKFLOW_TOOL_NAME (feature-gated)

    # Misc safe
    "Sleep",                   # SLEEP_TOOL_NAME

    # Internal classifier tool
    "classify_result",         # YOLO_CLASSIFIER_TOOL_NAME

    # Ant-only safe tools (feature-gated)
    "TerminalCapture",         # TERMINAL_CAPTURE_TOOL_NAME
    "OverflowTest",            # OVERFLOW_TEST_TOOL_NAME
    "VerifyPlanExecution",     # VERIFY_PLAN_EXECUTION_TOOL_NAME
}
```

**NOTE:** Write/Edit tools are NOT in the allowlist. They are handled by the **acceptEdits fast path** (allowed in CWD, classified outside CWD).

---

## 6. acceptEdits Fast Path

Before calling the classifier API, the system simulates `acceptEdits` mode:

```python
# Pseudocode
if tool.name not in ["Agent", "REPL"]:
    # Re-run checkPermissions with mode="acceptEdits"
    result = tool.checkPermissions(input, context_with_mode="acceptEdits")
    if result.behavior == "allow":
        return ALLOW  # Skip classifier entirely
# Agent and REPL are excluded because:
# - REPL code can contain VM escapes between inner tool calls
# - The classifier must see the glue JavaScript
```

---

## 7. Classifier JSON Schema (tool_use mode)

Tool name: `classify_result`

```json
{
  "type": "object",
  "properties": {
    "thinking": {
      "type": "string",
      "description": "Brief step-by-step reasoning."
    },
    "shouldBlock": {
      "type": "boolean",
      "description": "Whether the action should be blocked (true) or allowed (false)"
    },
    "reason": {
      "type": "string",
      "description": "Brief explanation of the classification decision"
    }
  },
  "required": ["thinking", "shouldBlock", "reason"]
}
```

Validated with Zod:
```typescript
z.object({
  thinking: z.string(),
  shouldBlock: z.boolean(),
  reason: z.string(),
})
```

---

## 8. Two-Stage XML Classifier (Detailed)

### Mode Selection

Controlled by GrowthBook feature flag `tengu_auto_mode_config.twoStageClassifier`:
- `true` or `"both"` → Run both stages (default when enabled)
- `"fast"` → Stage 1 only
- `"thinking"` → Stage 2 only
- `false` / undefined → Use legacy tool_use classifier

Env var override (ant-only): `CLAUDE_CODE_TWO_STAGE_CLASSIFIER`

### Stage 1: Fast Classification

```
System: [XML system prompt - same base + permissions, with tool_use output instruction replaced by XML format]

User prefix: [Optional CLAUDE.md message]
User: <transcript>
{...serialized transcript entries...}
</transcript>
Err on the side of blocking. <block> immediately.
```

Parameters:
- `max_tokens`: 64 (or 256 in fast-only mode) + thinking_padding
- `temperature`: 0
- `stop_sequences`: `["</block>"]` (not in fast-only mode)
- `thinking`: disabled (or omitted for always-on-thinking models)

Output format instruction (replaces tool_use line):
```
## Output Format

If the action should be blocked:
<block>yes</block><reason>one short sentence</reason>

If the action should be allowed:
<block>no</block>

Do NOT include a <reason> tag when the action is allowed.
Your ENTIRE response MUST begin with <block>. Do NOT output any analysis, reasoning, or commentary before <block>. No "Looking at..." or similar preamble.
```

Decision:
- `<block>no</block>` → **ALLOW immediately** (fast path, no Stage 2)
- `<block>yes</block>` → **Escalate to Stage 2** (or final in fast-only mode)
- Unparseable → **BLOCK for safety** (or escalate to Stage 2 in "both" mode)

### Stage 2: Thinking Classification

```
System: [Same XML system prompt as Stage 1]

User prefix: [Same CLAUDE.md message, shared cache with Stage 1]
User: <transcript>
{...same serialized transcript entries...}
</transcript>
Review the classification process and follow it carefully, making sure you deny actions that should be blocked. As a reminder, explicit (not suggestive or implicit) user confirmation is required to override blocks. Use <thinking> before responding with <block>.
```

Parameters:
- `max_tokens`: 4096 + thinking_padding
- `temperature`: 0
- No stop_sequences
- `thinking`: disabled (or omitted for always-on-thinking models)

Output parsing:
1. Strip `<thinking>...</thinking>` content (prevents tags inside reasoning from being matched)
2. Parse `<block>yes/no</block>` from remaining text
3. Parse `<reason>...</reason>` from remaining text
4. Parse `<thinking>...</thinking>` for logging/analytics

Decision:
- `<block>no</block>` → **ALLOW** (overrides Stage 1 block — reduces false positives)
- `<block>yes</block>` → **BLOCK** with reason
- Unparseable → **BLOCK for safety**

### Thinking Padding for Always-On Models

Models with `alwaysOnThinking` (set in `tengu_ant_model_override` GrowthBook config):
- Cannot send `thinking: { type: "disabled" }` (API returns 400)
- Instead: omit thinking param, add 2048 tokens of headroom to max_tokens
- Without headroom, adaptive thinking (observed up to 1114 tokens) exhausts the budget before `<block>` is emitted

---

## 9. Transcript Building

### Entry Construction (`buildTranscriptEntries`)

From conversation messages, extract:
- **User messages**: text content only
- **Assistant messages**: tool_use blocks only (NO assistant text — could be crafted to influence classifier)
- **Queued commands**: extracted as user turns

### Compact Serialization

Two formats (controlled by `tengu_auto_mode_config.jsonlTranscript`):

**Text format** (default):
```
User: implement the feature
Bash ls -la
FileEdit {"file_path":"/tmp/foo.py","old_string":"x","new_string":"y"}
User: looks good, now run tests
Bash pytest tests/
```

**JSONL format** (new):
```json
{"user":"implement the feature"}
{"Bash":"ls -la"}
{"FileEdit":{"file_path":"/tmp/foo.py","old_string":"x","new_string":"y"}}
{"user":"looks good, now run tests"}
{"Bash":"pytest tests/"}
```

Each tool controls its own projection via `tool.toAutoClassifierInput(input)`:
- Returns a compact representation of the tool input
- Returning `''` means "no security relevance" (skips classifier entirely)
- On error, falls back to raw input object

### Transcript Wrapping (XML mode)

```xml
<transcript>
{...serialized entries...}
{action being classified}
</transcript>
```

The action (final tool_use being classified) is appended last inside the transcript tags.

### Cache Strategy

- System prompt: `cache_control: ephemeral` (shared across classifier calls, 1h TTL)
- CLAUDE.md prefix message: `cache_control: ephemeral`
- Action block: `cache_control: ephemeral` (Stage 2 gets guaranteed cache hit on the full prefix shared with Stage 1)
- Budget: system(1) + CLAUDE.md(0-1) + action(1) = 2-3 cache breakpoints, under API limit of 4

---

## 10. Circuit Breaker / Denial Tracking

### Configuration Constants

```python
DENIAL_LIMITS = {
    "maxConsecutive": 3,    # 3 consecutive denials → fall back to user prompt
    "maxTotal": 20,         # 20 total denials in session → fall back to user prompt
}
```

### State

```python
class DenialTrackingState:
    consecutiveDenials: int  # Reset to 0 on any success
    totalDenials: int        # Only reset when total limit hit (then reset to 0)
```

### Logic Flow

```python
def after_classifier_decision(result, denial_state):
    if result.shouldBlock:
        denial_state = record_denial(denial_state)  # increment both counters

        if should_fallback_to_prompting(denial_state):
            if is_headless:
                raise AbortError("too many classifier denials in headless mode")
            # Reset total counter if that limit was hit
            if denial_state.totalDenials >= 20:
                denial_state.totalDenials = 0
                denial_state.consecutiveDenials = 0
            return ASK_USER  # Fall back to interactive prompt

        return DENY(reason=result.reason)

    else:  # allowed
        denial_state = record_success(denial_state)  # reset consecutive only
        return ALLOW
```

### Recent Denials Buffer

Ring buffer of last 20 denials for UI display:

```python
class AutoModeDenial:
    toolName: str
    display: str      # Human-readable description
    reason: str
    timestamp: int    # Unix timestamp

MAX_DENIALS = 20
```

---

## 11. Fail-Open / Fail-Closed Behavior

### Classifier Unavailable (API Error)

Controlled by GrowthBook flag `tengu_iron_gate_closed` (default: `true`, refreshed periodically):

- **Fail closed** (`true`): DENY with retry guidance message
- **Fail open** (`false`): Fall back to normal permission handling (user prompt)

### Transcript Too Long

When the classifier's transcript exceeds the model's context window:
- **Interactive mode**: Fall back to normal user prompt
- **Headless mode**: Throw AbortError (transcript only grows, will never recover)

### Signal Aborted

If the user aborts during classifier evaluation:
- Return `shouldBlock: true` with `unavailable: true`

---

## 12. Auto Mode State Machine

### Module State (`autoModeState.ts`)

```python
autoModeActive: bool = False        # Whether auto mode is currently active
autoModeFlagCli: bool = False       # Whether --auto was passed on CLI
autoModeCircuitBroken: bool = False # Whether GrowthBook killed auto mode mid-session
```

### Permission Mode Values

- `"default"` - Normal interactive mode
- `"acceptEdits"` - Auto-approve file edits in CWD
- `"auto"` - YOLO/Auto mode (classifier-based)
- `"plan"` - Plan mode (can also have auto mode active)
- `"dontAsk"` - Convert ask→deny (no prompts)
- `"bypassPermissions"` - Allow everything (admin)
- `"bubble"` - Internal (for subagent permission bubbling)

### Auto Mode Activation

Auto mode fires when:
- `toolPermissionContext.mode === "auto"`, OR
- `toolPermissionContext.mode === "plan" && autoModeActive === true`

### Gate Verification

On startup and model/fast-mode changes, `verifyAutoModeGateAccess()` checks GrowthBook config `tengu_auto_mode_config.enabled`. If disabled, sets `autoModeCircuitBroken = true` and disables auto mode.

---

## 13. Model Selection

Priority order:
1. Env var `CLAUDE_CODE_AUTO_MODE_MODEL` (ant-only)
2. GrowthBook `tengu_auto_mode_config.model`
3. Main loop model (same model the user is chatting with)

---

## 14. PowerShell-Specific Guidance

When `POWERSHELL_AUTO_MODE` feature is enabled, additional deny rules are appended:

```
- PowerShell Download-and-Execute: `iex (iwr ...)`, `Invoke-Expression (Invoke-WebRequest ...)`, etc. → "Code from External"
- PowerShell Irreversible Destruction: `Remove-Item -Recurse -Force`, `rm -r -fo`, `Clear-Content`, etc. → "Irreversible Local Destruction"
- PowerShell Persistence: modifying `$PROFILE`, `Register-ScheduledTask`, registry Run keys, WMI subscriptions → "Unauthorized Persistence"
- PowerShell Elevation: `Start-Process -Verb RunAs`, `-ExecutionPolicy Bypass`, disabling AMSI/Defender → "Security Weaken"
```

---

## 15. Dangerous Bash Patterns (Stripped from Allow Rules)

These patterns are stripped from user allow rules at auto-mode entry to prevent classifier bypass:

```python
DANGEROUS_BASH_PATTERNS = [
    # Cross-platform interpreters
    "python", "python3", "python2", "node", "deno", "tsx",
    "ruby", "perl", "php", "lua",
    # Package runners
    "npx", "bunx", "npm run", "yarn run", "pnpm run", "bun run",
    # Shells
    "bash", "sh", "zsh", "fish",
    # Execution primitives
    "eval", "exec", "env", "xargs", "sudo",
    # Remote
    "ssh",
    # Ant-only additions:
    "fa run", "coo", "gh", "gh api", "curl", "wget",
    "git", "kubectl", "aws", "gcloud", "gsutil",
]
```

---

## 16. Telemetry Events

| Event | When |
|-------|------|
| `tengu_auto_mode_outcome` | After every classifier API call (success, parse_failure, interrupted, error, transcript_too_long) |
| `tengu_auto_mode_decision` | After permission decision (allowed, blocked, unavailable) with full token/cost metrics |
| `tengu_auto_mode_denial_limit_exceeded` | When consecutive/total denial limit hit |
| `tengu_auto_mode_malformed_tool_input` | When tool.toAutoClassifierInput throws |

---

## 17. Debug / Dump Infrastructure

### Environment Variables

| Var | Effect |
|-----|--------|
| `CLAUDE_CODE_DUMP_AUTO_MODE` | Dump req/res JSON to `~/.claude/tmp/auto-mode/` |
| `CLAUDE_CODE_AUTO_MODE_MODEL` | Override classifier model (ant-only) |
| `CLAUDE_CODE_TWO_STAGE_CLASSIFIER` | Override 2-stage mode (ant-only): `true`, `false`, `fast`, `thinking` |
| `CLAUDE_CODE_JSONL_TRANSCRIPT` | Override transcript format (ant-only) |

### Error Dump

On API error, classifier prompts are dumped to `~/.claude/tmp/auto-mode-classifier-errors/{sessionId}.txt` containing:
- Error message
- Context comparison (main loop tokens vs classifier tokens)
- Action being classified
- Full system prompt
- Full user prompt (transcript)

### Transcript Snapshot

`getAutoModeClassifierTranscript()` returns the most recent classifier request(s) as JSON string (for `/share` command).

---

## 18. GrowthBook Feature Flags

| Flag | Type | Purpose |
|------|------|---------|
| `TRANSCRIPT_CLASSIFIER` | Build-time | Master gate for all auto mode code (dead code elimination) |
| `BASH_CLASSIFIER` | Build-time | Gate for bash-specific semantic classifier |
| `POWERSHELL_AUTO_MODE` | Build-time | Gate for PowerShell auto mode support |
| `tengu_auto_mode_config` | Runtime JSON | Model override, 2-stage enable, JSONL transcript, forceExternalPermissions |
| `tengu_iron_gate_closed` | Runtime bool | Fail-closed behavior on classifier API error |

---

## 19. Complete Permission Pipeline (Simplified)

```python
def has_permissions_to_use_tool(tool, input, context):
    # Step 1: Rule-based checks
    if deny_rule_exists(tool):        return DENY
    if ask_rule_exists(tool):         return ASK (with exceptions)
    if always_allow_rule(tool):       return ALLOW

    # Step 2: Tool's own checkPermissions
    result = tool.checkPermissions(input, context)

    # Step 3: Mode transforms
    if result.behavior == "ask":
        if mode == "dontAsk":         return DENY

        if mode == "auto" or (mode == "plan" and auto_active):
            # 3a. Safety check guard
            if safety_check and not classifier_approvable:
                return ASK_USER

            # 3b. acceptEdits fast path
            if simulated_acceptEdits_allows(tool, input):
                return ALLOW

            # 3c. Tool allowlist
            if tool.name in SAFE_YOLO_ALLOWLISTED_TOOLS:
                return ALLOW

            # 3d. Classifier
            result = classify_yolo_action(messages, action, tools, context)

            if result.shouldBlock:
                track_denial()
                if denial_limit_exceeded():
                    return ASK_USER  # Circuit breaker
                return DENY(reason=result.reason)
            else:
                reset_consecutive_denials()
                return ALLOW

    return result
```

---

## 20. SEAL Python Adaptation Guide

### Key Components to Implement

1. **ClassifierService** (replaces `yoloClassifier.ts`)
   - System prompt template loading and assembly
   - Transcript building from conversation history
   - 2-stage API call logic (use `httpx` + Anthropic Python SDK)
   - XML response parsing

2. **ToolAllowlist** (replaces `classifierDecision.ts`)
   - Set of tool names that bypass classifier
   - For SEAL: read-only MCP tools, search tools, plan tools

3. **DenialTracker** (replaces `denialTracking.ts`)
   - Simple counter with maxConsecutive=3, maxTotal=20
   - Reset logic on success/limit-hit

4. **AutoModeState** (replaces `autoModeState.ts`)
   - Module-level boolean flags
   - Circuit breaker from config service

5. **PermissionPipeline** (replaces auto-mode section of `permissions.ts`)
   - Orchestrate: rules -> allowlist -> acceptEdits fast path -> classifier -> denial tracking

### Python Skeleton

```python
from dataclasses import dataclass, field
from typing import Optional, Literal
import anthropic

# --- Denial Tracking ---
@dataclass
class DenialState:
    consecutive: int = 0
    total: int = 0

MAX_CONSECUTIVE = 3
MAX_TOTAL = 20

def record_denial(s: DenialState) -> DenialState:
    return DenialState(s.consecutive + 1, s.total + 1)

def record_success(s: DenialState) -> DenialState:
    return DenialState(0, s.total)

def should_fallback(s: DenialState) -> bool:
    return s.consecutive >= MAX_CONSECUTIVE or s.total >= MAX_TOTAL


# --- Classifier Result ---
@dataclass
class ClassifierResult:
    should_block: bool
    reason: str
    thinking: Optional[str] = None
    unavailable: bool = False
    transcript_too_long: bool = False
    stage: Optional[Literal["fast", "thinking"]] = None
    duration_ms: Optional[int] = None


# --- Tool Allowlist ---
SAFE_TOOLS = {
    "memory_search", "memory_list", "memory_store",
    "boot_context", "health_check", "soul_check",
    "inner_thoughts", "self_reflect",
    # Add SEAL-specific safe tools
}

def is_allowlisted(tool_name: str) -> bool:
    return tool_name in SAFE_TOOLS


# --- Transcript Builder ---
@dataclass
class TranscriptEntry:
    role: Literal["user", "assistant"]
    content: list[dict]

def build_transcript(messages: list[dict]) -> list[TranscriptEntry]:
    """Extract user text + assistant tool_use blocks. NO assistant text."""
    entries = []
    for msg in messages:
        if msg["role"] == "user":
            text_blocks = [b for b in msg.get("content", []) if b.get("type") == "text"]
            if text_blocks:
                entries.append(TranscriptEntry("user", text_blocks))
        elif msg["role"] == "assistant":
            tool_blocks = [b for b in msg.get("content", []) if b.get("type") == "tool_use"]
            if tool_blocks:
                entries.append(TranscriptEntry("assistant", tool_blocks))
    return entries

def serialize_transcript(entries: list[TranscriptEntry]) -> str:
    """JSONL format: {"user":"text"} or {"ToolName":"input"}"""
    lines = []
    for entry in entries:
        for block in entry.content:
            if block.get("type") == "text":
                lines.append(json.dumps({"user": block["text"]}))
            elif block.get("type") == "tool_use":
                lines.append(json.dumps({block["name"]: block.get("input", {})}))
    return "\n".join(lines)


# --- 2-Stage XML Classifier ---
S1_SUFFIX = "\nErr on the side of blocking. <block> immediately."
S2_SUFFIX = (
    "\nReview the classification process and follow it carefully, "
    "making sure you deny actions that should be blocked. As a reminder, "
    "explicit (not suggestive or implicit) user confirmation is required "
    "to override blocks. Use <thinking> before responding with <block>."
)

def parse_xml_block(text: str) -> Optional[bool]:
    """Parse <block>yes/no</block>. Strip <thinking> first."""
    import re
    text = re.sub(r'<thinking>[\s\S]*?</thinking>', '', text)
    text = re.sub(r'<thinking>[\s\S]*$', '', text)
    matches = re.findall(r'<block>(yes|no)\b', text, re.IGNORECASE)
    if not matches:
        return None
    return matches[0].lower() == "yes"

def parse_xml_reason(text: str) -> Optional[str]:
    import re
    text = re.sub(r'<thinking>[\s\S]*?</thinking>', '', text)
    match = re.search(r'<reason>([\s\S]*?)</reason>', text)
    return match.group(1).strip() if match else None


async def classify_action(
    client: anthropic.AsyncAnthropic,
    system_prompt: str,
    transcript: str,
    action: str,
    model: str = "claude-sonnet-4-20250514",
    mode: Literal["both", "fast", "thinking"] = "both",
) -> ClassifierResult:
    """2-stage XML classifier."""

    xml_prompt = system_prompt  # Already has XML output format

    user_content = f"<transcript>\n{transcript}\n{action}\n</transcript>"

    # Stage 1: Fast
    if mode != "thinking":
        s1_response = await client.messages.create(
            model=model,
            max_tokens=64 if mode == "both" else 256,
            temperature=0,
            system=xml_prompt,
            messages=[{"role": "user", "content": user_content + S1_SUFFIX}],
            **({"stop_sequences": ["</block>"]} if mode != "fast" else {}),
        )
        s1_text = s1_response.content[0].text
        s1_block = parse_xml_block(s1_text)

        if s1_block is False:  # Allow
            return ClassifierResult(should_block=False, reason="Allowed by fast classifier", stage="fast")

        if mode == "fast":
            if s1_block is None:
                return ClassifierResult(should_block=True, reason="Unparseable - blocking for safety", stage="fast")
            return ClassifierResult(
                should_block=True,
                reason=parse_xml_reason(s1_text) or "Blocked by fast classifier",
                stage="fast",
            )

    # Stage 2: Thinking
    s2_response = await client.messages.create(
        model=model,
        max_tokens=4096,
        temperature=0,
        system=xml_prompt,
        messages=[{"role": "user", "content": user_content + S2_SUFFIX}],
    )
    s2_text = s2_response.content[0].text
    s2_block = parse_xml_block(s2_text)

    if s2_block is None:
        return ClassifierResult(should_block=True, reason="Stage 2 unparseable - blocking for safety", stage="thinking")

    return ClassifierResult(
        should_block=s2_block,
        reason=parse_xml_reason(s2_text) or "No reason provided",
        thinking=parse_xml_thinking(s2_text),
        stage="thinking",
    )
```

### SEAL-Specific System Prompt Template

Since the actual prompts are not in the open-source repo, you need to write your own. Based on the architecture, it should follow this structure:

```
You are a security classifier for an AI agent system called SEAL.
Your job is to evaluate whether an agent action should be allowed or blocked.

<permissions_template>

## ALLOW rules (actions that should be auto-approved)
<user_allow_rules_to_replace>
- Reading files in the project directory
- Running Python scripts in the seal-spark venv
- Database queries (SELECT only) on SEAL PostgreSQL
- Memory operations (store, search, list)
- Git status and log operations
</user_allow_rules_to_replace>

## SOFT DENY rules (actions that should be blocked / require confirmation)
<user_deny_rules_to_replace>
- Any command that modifies system files outside ~/IA/proyecto-seal/
- Database schema modifications (ALTER, DROP, CREATE TABLE)
- Network operations that upload data to external services
- Modifications to LoRA adapters or model weights
- Git push, force operations, or branch deletions
- Modifications to soul/identity files without explicit authorization
- Running commands as root or with sudo
</user_deny_rules_to_replace>

## Environment context
<user_environment_to_replace>
- DGX Spark (arm64, Ubuntu 24.04, CUDA 12.8)
- PostgreSQL on port 5433, Neo4j on 7687, Qdrant on 6333
- Python venv at /home/dadito/IA/seal-spark/.venv/
- Project root: /home/dadito/IA/proyecto-seal/
- Agents: JARVIS (architect), ADA (engineer), DUM (guard)
</user_environment_to_replace>

</permissions_template>

## Output Format

If the action should be blocked:
<block>yes</block><reason>one short sentence</reason>

If the action should be allowed:
<block>no</block>

Do NOT include a <reason> tag when the action is allowed.
Your ENTIRE response MUST begin with <block>.
```

---

## 21. Key Design Decisions to Preserve

1. **Never include assistant text in transcript** - Only user text and tool_use blocks. Assistant text could be crafted to manipulate the classifier.

2. **Fail-safe by default** - Any parse failure or API error results in BLOCK. Only explicit `<block>no</block>` allows.

3. **2-stage reduces false positives** - Stage 1 is fast but aggressive. Stage 2 with chain-of-thought catches false positives. Only Stage 2 can override a Stage 1 block.

4. **Circuit breaker prevents infinite loops** - After 3 consecutive or 20 total denials, fall back to human.

5. **Cache-friendly design** - System prompt + CLAUDE.md + transcript prefix are all cached. Stage 2 shares Stage 1's cache. Only the suffix differs.

6. **Project settings excluded** - A malicious `.claude/settings.json` in a cloned repo could inject allow rules. Only user/local/flag/policy settings are trusted.

7. **acceptEdits fast path** - Cheap simulation before expensive API call. If a file edit would be allowed by the permissive "acceptEdits" mode, skip the classifier.

8. **Tool projection** - Each tool controls what the classifier sees via `toAutoClassifierInput()`. Tools can hide irrelevant fields or return `''` to skip classification entirely.
