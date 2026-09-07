# SPEC_05: Bridge & Permissions Systems — Complete Deep Analysis

> Source: `openclaude-ref/src/bridge/` (33 files, ~12.8K lines) and `openclaude-ref/src/utils/permissions/` (25 files)
> Analyzed from actual source code, not extraction summaries.

---

## PART A: BRIDGE SYSTEM

### A.1 Architecture Overview

The Bridge is Claude Code's **Remote Control** system — it connects a local CLI instance to claude.ai's web interface so users can interact with their CLI session from a browser or phone. There are two distinct bridge architectures:

1. **Standalone Bridge** (`bridgeMain.ts`) — `claude remote-control` command, spawns child CLI processes
2. **REPL Bridge** (`replBridge.ts` + `initReplBridge.ts`) — embeds inside the running REPL, forwards events bidirectionally

Both share the same transport layer and API client.

### A.2 Core Types (`types.ts`)

```typescript
// Work dispatch from server
type WorkData = {
  type: 'session' | 'healthcheck'
  id: string
}

// The secret decoded from work items — contains JWT, API URL, git sources
type WorkSecret = {
  version: number
  session_ingress_token: string
  api_base_url: string
  sources: Array<{ type: string; git_info?: {...} }>
  auth: Array<{ type: string; token: string }>
  claude_code_args?: Record<string, string> | null
  mcp_config?: unknown | null
  environment_variables?: Record<string, string> | null
  use_code_sessions?: boolean  // CCR v2 selector
}

// How sessions are spawned
type SpawnMode = 'single-session' | 'worktree' | 'same-dir'

// Worker type for web filtering
type BridgeWorkerType = 'claude_code' | 'claude_code_assistant'

// Session lifecycle
type SessionDoneStatus = 'completed' | 'failed' | 'interrupted'
type SessionActivityType = 'tool_start' | 'text' | 'result' | 'error'
```

**BridgeConfig** — the full registration payload:
- `dir`, `machineName`, `branch`, `gitRepoUrl`
- `maxSessions`, `spawnMode`, `verbose`, `sandbox`
- `bridgeId` (client UUID), `workerType`, `environmentId`
- `reuseEnvironmentId` (for reconnect — must be backend-format ID)
- `apiBaseUrl`, `sessionIngressUrl`, `debugFile`, `sessionTimeoutMs`

**BridgeApiClient** — the environment lifecycle API:
- `registerBridgeEnvironment(config)` → `{environment_id, environment_secret}`
- `pollForWork(envId, secret, signal, reclaimOlderThanMs)` → `WorkResponse | null`
- `acknowledgeWork(envId, workId, sessionToken)`
- `stopWork(envId, workId, force)`
- `deregisterEnvironment(envId)`
- `sendPermissionResponseEvent(sessionId, event, token)`
- `archiveSession(sessionId)`
- `reconnectSession(envId, sessionId)` — force-stop stale workers, re-queue
- `heartbeatWork(envId, workId, token)` → `{lease_extended, state}`

### A.3 Transport Layer (`replBridgeTransport.ts`)

Two transport versions behind a unified `ReplBridgeTransport` interface:

**v1: HybridTransport** (WebSocket reads + POST writes to Session-Ingress)
```typescript
function createV1ReplTransport(hybrid: HybridTransport): ReplBridgeTransport
// Thin wrapper — v1 Session-Ingress WS doesn't use SSE sequence numbers
// getLastSequenceNum() always returns 0
// reportState/reportMetadata/reportDelivery are no-ops
```

**v2: SSETransport + CCRClient** (SSE reads + POST writes to CCR /worker/*)
```typescript
async function createV2ReplTransport(opts: {
  sessionUrl: string
  ingressToken: string          // JWT, NOT OAuth
  sessionId: string
  initialSequenceNum?: number   // Resume from last SSE position
  epoch?: number                // From POST /bridge response
  heartbeatIntervalMs?: number
  heartbeatJitterFraction?: number
  outboundOnly?: boolean        // Mirror mode — write-only
  getAuthToken?: () => string   // Per-instance, multi-session safe
}): Promise<ReplBridgeTransport>
```

**v2 internals:**
- `registerWorker(sessionUrl, ingressToken)` → epoch number (or uses pre-supplied epoch from /bridge)
- SSE URL derived: `sessionUrl + '/worker/events/stream'`
- Write path: `CCRClient.writeEvent()` → `SerialBatchEventUploader` (batches up to 100)
- Epoch mismatch (409): close transport, throw, poll loop recovers
- Delivery ACK: immediate `received` + `processed` on every SSE frame (prevents phantom prompt flood)
- Connect timeout codes: 4090 = epoch mismatch, 4091 = init failure, 4092 = SSE budget exhaustion

### A.4 REPL Bridge Core (`replBridge.ts`) — The Main Engine

**Entry:** `initBridgeCore(params: BridgeCoreParams)` → `BridgeCoreHandle | null`

**Lifecycle:**
1. Import `bridgePointer.js` (crash recovery)
2. Read perpetual mode pointer (if applicable)
3. Register environment via API → `{environment_id, environment_secret}`
4. Try reconnect in place (perpetual mode) OR create fresh session
5. Write crash-recovery pointer (`bridge-pointer.json`)
6. Start poll loop for work items

**Handle returned:**
```typescript
type ReplBridgeHandle = {
  bridgeSessionId: string
  environmentId: string
  sessionIngressUrl: string
  writeMessages(messages: Message[]): void
  writeSdkMessages(messages: SDKMessage[]): void
  sendControlRequest(request: SDKControlRequest): void
  sendControlResponse(response: SDKControlResponse): void
  sendControlCancelRequest(requestId: string): void
  sendResult(): void
  teardown(): Promise<void>
}

type BridgeCoreHandle = ReplBridgeHandle & {
  getSSESequenceNum(): number  // For daemon persistence across restarts
}
```

**State management:**
- `currentSessionId` — mutable, updated on reconnect
- `transport: ReplBridgeTransport | null` — the active transport
- `v2Generation` — monotonic counter preventing stale transport installs
- `lastTransportSequenceNum` — SSE high-water mark across transport swaps
- `currentWorkId`, `currentIngressToken` — current work item
- `recentPostedUUIDs` — BoundedUUIDSet(2000) for echo dedup
- `recentInboundUUIDs` — BoundedUUIDSet(2000) for re-delivery dedup
- `flushGate: FlushGate<Message>` — queues writes during initial history flush

**Reconnection strategy (3 max):**
```
Strategy 1: Reconnect in place
  → Re-register with reuseEnvironmentId
  → If same env returned: reconnectSession() to re-queue
  → currentSessionId stays same, URL stays valid

Strategy 2: Fresh session fallback
  → Archive old session
  → Create new session on the now-registered env
  → Reset SSE seq, inbound UUID set, title derivation
  → Rewrite bridge pointer with new IDs
```

**Poll loop mechanics:**
- `isAtCapacity()` = `transport !== null`
- At capacity: heartbeat-only polling (60s intervals, 5x headroom under 300s lease TTL)
- Heartbeat fatal (expired JWT/work gone): tear down work item, fast-poll
- Environment lost (404): trigger `reconnectEnvironmentWithSession()`
- Poll error backoff: 2s initial → 60s cap → 15min give-up

**Title derivation:**
- Count-1: immediate `deriveTitle()` placeholder + fire-and-forget Haiku generation
- Count-3: re-generate using full conversation text
- Explicit titles (from `/rename` or `--name`) never overwritten

### A.5 Env-Less Bridge (`remoteBridgeCore.ts`) — v2 Direct Path

Skips the Environments API entirely. Gated by `tengu_bridge_repl_v2` GrowthBook flag.

**Simplified flow:**
1. `POST /v1/code/sessions` (OAuth) → `session.id`
2. `POST /v1/code/sessions/{id}/bridge` (OAuth) → `{worker_jwt, expires_in, api_base_url, worker_epoch}`
3. `createV2ReplTransport(worker_jwt, worker_epoch)` → SSE + CCRClient
4. `createTokenRefreshScheduler()` → proactive JWT refresh 5min before expiry
5. 401 on SSE → rebuild transport with fresh /bridge credentials (same seq-num)

**Key difference:** No register/poll/ack/stop/heartbeat/deregister. Each `/bridge` call bumps epoch server-side. JWT refresh = full transport rebuild (epoch changes).

**Auth recovery:** Serialized via `authRecoveryInFlight` flag. Proactive refresh and 401 recovery can race on laptop wake — flag prevents double epoch bump.

### A.6 Session Runner (`sessionRunner.ts`) — Standalone Bridge

Spawns child Claude CLI processes for standalone `claude remote-control`:

**Environment isolation:**
```typescript
const CHILD_ENV_ALLOWLIST = [
  'PATH', 'HOME', 'USERPROFILE', 'TEMP', 'TMP',
  'NODE_OPTIONS', 'NODE_ENV',
  'CLAUDE_CODE_ENVIRONMENT_KIND', 'CLAUDE_CODE_FORCE_SANDBOX',
  'TERM', 'COLORTERM', 'FORCE_COLOR', ...
]
// Everything else STRIPPED — child authenticates via CLAUDE_CODE_SESSION_ACCESS_TOKEN
```

**Permission forwarding:**
```typescript
type PermissionRequest = {
  type: 'control_request'
  request_id: string
  request: {
    subtype: 'can_use_tool'
    tool_name: string
    input: Record<string, unknown>
    tool_use_id: string
  }
}
```
Child emits `control_request` on stdout → bridge forwards to server → web user approves/denies → `control_response` sent back.

### A.7 Bridge Pointer (`bridgePointer.ts`) — Crash Recovery

```typescript
type BridgePointer = {
  sessionId: string
  environmentId: string
  source: 'standalone' | 'repl'
}
// Stored at: ~/.claude/projects/{sanitized_dir}/bridge-pointer.json
// TTL: 4 hours (checked via file mtime, not embedded timestamp)
```

**Worktree-aware read:** `readBridgePointerAcrossWorktrees()` fans out across git worktree siblings (capped at 50) to find the freshest pointer. Fast path: check current dir first.

### A.8 Bridge Messaging (`bridgeMessaging.ts`)

**Message eligibility filter:**
- Eligible: `user`, `assistant`, `system` (subtype `local_command`)
- NOT eligible: `tool_result`, `progress`, virtual messages (REPL inner calls)

**Ingress routing:**
```
Parse JSON → normalize control message keys
  → control_response? → onPermissionResponse()
  → control_request? → onControlRequest()
  → SDKMessage with UUID in recentPostedUUIDs? → SKIP (echo)
  → SDKMessage with UUID in recentInboundUUIDs? → SKIP (re-delivery)
  → SDKMessage → onInboundMessage()
```

### A.9 Bridge Permission Callbacks (`bridgePermissionCallbacks.ts`)

```typescript
type BridgePermissionCallbacks = {
  sendRequest(requestId, toolName, input, toolUseId, description, suggestions?, blockedPath?): void
  sendResponse(requestId, response: {behavior: 'allow'|'deny', updatedInput?, updatedPermissions?, message?}): void
  cancelRequest(requestId): void
  onResponse(requestId, handler): () => void  // returns unsubscribe
}
```

### A.10 Supporting Bridge Files

| File | Purpose |
|------|---------|
| `bridgeApi.ts` | HTTP client: register, poll, ack, stop, deregister, heartbeat, reconnect |
| `bridgeConfig.ts` | OAuth token, base URL, token override extraction |
| `bridgeDebug.ts` | Ant-only: fault injection, `/bridge-kick` command support |
| `bridgeEnabled.ts` | GrowthBook gates: `isBridgeEnabledBlocking()`, `isCseShimEnabled()`, `isEnvLessBridgeEnabled()` |
| `bridgeMain.ts` | Standalone `claude remote-control` command — spawns sessions, manages worktrees |
| `bridgeStatusUtil.ts` | Duration formatting utilities |
| `bridgeUI.ts` | Terminal status display: idle, reconnecting, session count, QR toggle |
| `capacityWake.ts` | Signal to wake poll loop when transport drops |
| `codeSessionApi.ts` | POST /v1/code/sessions (env-less path) |
| `createSession.ts` | POST /v1/sessions (env-based path), PATCH title |
| `envLessBridgeConfig.ts` | GrowthBook config for env-less bridge: timeouts, retry, heartbeat |
| `flushGate.ts` | Queue messages during initial history flush |
| `inboundAttachments.ts` | Handle inbound file attachments from web |
| `inboundMessages.ts` | Parse and route inbound SDK messages |
| `jwtUtils.ts` | Token refresh scheduler with configurable buffer |
| `pollConfig.ts` | GrowthBook-backed poll interval config |
| `pollConfigDefaults.ts` | Default poll intervals: fast=2s, heartbeat=60s |
| `replBridgeHandle.ts` | React hook for REPL bridge lifecycle |
| `sessionIdCompat.ts` | `cse_*` ↔ `session_*` ID translation |
| `trustedDevice.ts` | Trusted device token for API auth |
| `workSecret.ts` | Decode base64url work secret, build SDK URLs, register worker |

---

## PART B: PERMISSIONS SYSTEM

### B.1 Architecture Overview

The permissions system is a **multi-layer decision pipeline** that determines whether Claude can execute each tool invocation. It combines:
- Static rules (from settings files and CLI)
- Mode-based policies (default, plan, acceptEdits, bypassPermissions, auto, dontAsk)
- AI classifier (auto mode)
- Safety checks (hardcoded dangerous paths/patterns)
- Hook system (PreToolUse extensibility)

### B.2 Permission Modes (`PermissionMode.ts`)

```typescript
type PermissionMode =
  | 'default'           // Ask for everything not explicitly allowed
  | 'plan'              // Read-only, Claude explains what it would do
  | 'acceptEdits'       // Auto-allow file edits in working directory
  | 'bypassPermissions' // --dangerously-skip-permissions: allow (almost) everything
  | 'dontAsk'           // Convert all 'ask' to 'deny' (reject silently)
  | 'auto'              // INTERNAL: AI classifier decides (TRANSCRIPT_CLASSIFIER gate)
  | 'bubble'            // INTERNAL

type ExternalPermissionMode = 'default' | 'plan' | 'acceptEdits' | 'bypassPermissions' | 'dontAsk'
```

**Mode cycling (Shift+Tab):**
```
External users: default → acceptEdits → plan → [bypassPermissions] → [auto] → default
Ant users:      default → [bypassPermissions] → [auto] → default  (skip acceptEdits and plan)
```

### B.3 Permission Rules

**Rule structure:**
```typescript
type PermissionRuleValue = {
  toolName: string              // e.g., "Bash", "mcp__server1__tool1"
  ruleContent?: string          // e.g., "npm install", "prefix:*"
}

type PermissionBehavior = 'allow' | 'deny' | 'ask'

type PermissionRule = {
  source: PermissionRuleSource
  ruleBehavior: PermissionBehavior
  ruleValue: PermissionRuleValue
}

type PermissionRuleSource =
  | 'userSettings'     // ~/.claude/settings.json (global)
  | 'projectSettings'  // .claude/settings.json (project, committed)
  | 'localSettings'    // .claude/settings.local.json (gitignored)
  | 'policySettings'   // Enterprise managed
  | 'flagSettings'     // --settings flag
  | 'cliArg'           // --allowed-tools
  | 'command'           // Slash command frontmatter
  | 'session'           // In-memory this session only
```

**Rule parsing (`permissionRuleParser.ts`):**
```
"Bash"                    → { toolName: "Bash" }                    (tool-wide)
"Bash(npm install)"       → { toolName: "Bash", ruleContent: "npm install" }  (exact)
"Bash(npm:*)"            → { toolName: "Bash", ruleContent: "npm:*" }  (prefix)
"Bash(*)"                → { toolName: "Bash" }                    (tool-wide, * stripped)
"Bash(python -c \"print\\(1\\)\")" → escaped parentheses handled
```

**Legacy tool name aliases:**
```
Task → Agent
KillShell → TaskStop
AgentOutputTool → TaskOutput
BashOutputTool → TaskOutput
Brief → BriefTool (internal)
```

### B.4 Shell Rule Matching (`shellRuleMatching.ts`)

Three rule types for shell commands:

```typescript
type ShellPermissionRule =
  | { type: 'exact'; command: string }     // "npm install"
  | { type: 'prefix'; prefix: string }     // From "npm:*" legacy syntax
  | { type: 'wildcard'; pattern: string }  // "git *" matches "git add" AND bare "git"
```

**Wildcard matching:**
- `*` matches any sequence including newlines (dotAll flag)
- `\*` matches literal asterisk
- `\\` matches literal backslash
- Trailing `command *` makes space+args optional (so `git *` matches bare `git`)
- Multi-wildcard patterns like `* run *` do NOT get this treatment
- Case-insensitive option for PowerShell

### B.5 The Decision Pipeline (`permissions.ts` — `hasPermissionsToUseToolInner`)

This is the **exact flow** for every tool invocation:

```
Step 1a: DENY RULES — entire tool denied?
  → getDenyRuleForTool() checks all sources
  → If match: return {deny}

Step 1b: ASK RULES — entire tool has ask rule?
  → getAskRuleForTool() checks all sources
  → Exception: Bash + sandbox enabled + sandboxed command → fall through
  → If match and no exception: return {ask}

Step 1c: TOOL-SPECIFIC CHECK
  → tool.inputSchema.parse(input)
  → tool.checkPermissions(parsedInput, context) → PermissionResult
  → Each tool implements its own logic (Bash splits subcommands, Edit checks paths)

Step 1d: TOOL DENIED?
  → If tool.checkPermissions returned 'deny': return it

Step 1e: REQUIRES USER INTERACTION?
  → tool.requiresUserInteraction?.() && result is 'ask'
  → Return ask (even in bypass mode)

Step 1f: CONTENT-SPECIFIC ASK RULES
  → If tool returned ask + decisionReason.type === 'rule' + ruleBehavior === 'ask'
  → Return ask (respected even in bypass mode)

Step 1g: SAFETY CHECKS
  → If tool returned ask + decisionReason.type === 'safetyCheck'
  → Return ask (bypass-immune: .git/, .claude/, .vscode/, shell configs)
  → Non-classifierApprovable safetyChecks also immune to auto mode

Step 2a: BYPASS MODE CHECK
  → mode === 'bypassPermissions' OR (mode === 'plan' && isBypassPermissionsModeAvailable)
  → return {allow}

Step 2b: TOOL-WIDE ALLOW RULE
  → toolAlwaysAllowedRule() — checks exact name + MCP server-level patterns
  → MCP: rule "mcp__server1" matches tool "mcp__server1__tool1"
  → If match: return {allow}

Step 3: CONVERT PASSTHROUGH → ASK
  → If tool returned 'passthrough': convert to 'ask'

--- WRAPPER (hasPermissionsToUseTool) applies post-processing: ---

Step 4: DONT_ASK MODE
  → If mode === 'dontAsk' && behavior === 'ask': convert to {deny}

Step 5: AUTO MODE (TRANSCRIPT_CLASSIFIER gate)
  → If mode === 'auto' (or plan+autoModeActive):
    Step 5a: Non-classifier-approvable safetyCheck → deny in headless, ask in CLI
    Step 5b: Tool requires user interaction → return ask as-is
    Step 5c: PowerShell (without POWERSHELL_AUTO_MODE) → ask/deny
    Step 5d: AcceptEdits fast-path — simulate acceptEdits mode, skip classifier if allowed
    Step 5e: Safe tool allowlist → auto-allow (read-only tools, task mgmt, etc.)
    Step 5f: Run YOLO classifier → allow/block/unavailable

Step 6: HEADLESS/ASYNC AGENTS
  → shouldAvoidPermissionPrompts?
  → Run PermissionRequest hooks first
  → If no hook decides: return {deny, "Permission prompts not available"}

Step 7: DENIAL TRACKING
  → On allow: reset consecutive denials
  → On classifier block: increment, check limits (3 consecutive OR 20 total)
  → Limit exceeded in CLI: fall back to manual prompting
  → Limit exceeded in headless: throw AbortError
```

### B.6 YOLO/Auto Mode Classifier (`yoloClassifier.ts`)

An AI-powered security classifier that decides allow/block for tool invocations in auto mode.

**Architecture:**
- System prompt: `auto_mode_system_prompt.txt` + permissions template (external or anthropic-internal)
- User prompt: serialized conversation transcript (compact JSONL format)
- Tool: `classify_result` with `{thinking, shouldBlock, reason}` schema
- Model: resolved via `resolveAntModel` (Haiku-class)
- Max transcript: 200K chars, max block value: 32K chars

**Transcript serialization:**
```
User messages → {"user": "text"}
Tool calls → {"Bash": "ls -la"} (via tool.toAutoClassifierInput())
Assistant text is EXCLUDED (model-authored, could influence classifier)
Reversed order: most recent first, budget-constrained
```

**CLAUDE.md injection:**
```
The classifier receives the user's CLAUDE.md configuration wrapped in:
<user_claude_md>...</user_claude_md>
This allows user intent to influence classifier decisions.
```

**User-customizable rules (settings.autoMode):**
```typescript
type AutoModeRules = {
  allow: string[]        // Actions the classifier should allow
  soft_deny: string[]    // Actions requiring extra scrutiny
  environment: string[]  // Description of the working environment
}
```

**Error handling:**
- Transcript too long: fall back to manual prompting (permanent condition)
- API unavailable + `tengu_iron_gate_closed` gate: deny (fail closed)
- API unavailable + gate off: fall back to manual (fail open)
- Error dumps saved to `~/.claude/tmp/auto-mode-classifier-errors/{sessionId}.txt`

### B.7 Dangerous Patterns (`dangerousPatterns.ts` + `permissionSetup.ts`)

Rules that are **stripped when entering auto mode** because they bypass the classifier:

**Cross-platform code execution:**
```
python, python3, node, deno, tsx, ruby, perl, php, lua
npx, bunx, npm run, yarn run, pnpm run, bun run
bash, sh, ssh
```

**Bash-specific additions:**
```
zsh, fish, eval, exec, env, xargs, sudo
Ant-only: fa run, coo, gh, gh api, curl, wget, git, kubectl, aws, gcloud, gsutil
```

**PowerShell-specific:**
```
pwsh, powershell, cmd, wsl
iex, invoke-expression, icm, invoke-command
start-process, saps, start, start-job, sajb, start-threadjob
register-objectevent/engineevent/wmievent/scheduledjob
new-pssession, enter-pssession
add-type, new-object
+ .exe variants for all patterns
```

**Agent rules:** ANY Agent allow rule is dangerous (bypasses sub-agent evaluation).

**Detection logic (`isDangerousBashPermission`):**
```
Rule content matches if:
  1. undefined or empty (tool-wide allow = allows ALL)
  2. standalone wildcard (*)
  3. pattern:* (prefix syntax)
  4. pattern* (wildcard at end)
  5. pattern * (wildcard with space)
  6. pattern -...* (flag wildcard)
```

### B.8 Path Validation (`pathValidation.ts`)

**Security layers for file operations:**

1. **UNC paths** — Block `\\server\share` paths (credential leak)
2. **Tilde expansion** — Block `~user`, `~+`, `~-` (TOCTOU gap)
3. **Shell expansion** — Block `$VAR`, `${VAR}`, `$(cmd)`, `%VAR%`, `=cmd` (TOCTOU)
4. **Glob patterns** — Block in write/create ops; validate base dir for reads
5. **Deny rules** — Check first (highest precedence)
6. **Internal editable paths** — plan files, scratchpad, agent memory (allow through safety check)
7. **Safety checks** — Windows patterns, Claude config files, dangerous files/dirs
8. **Working directory** — Auto-allow reads; writes only in acceptEdits mode
9. **Internal readable paths** — project temp dir, session memory
10. **Sandbox write allowlist** — Explicit writable dirs configured by user
11. **Allow rules** — Last check

**Dangerous files protected from auto-edit:**
```
.gitconfig, .gitmodules, .bashrc, .bash_profile,
.zshrc, .zprofile, .profile, .ripgreprc, .mcp.json, .claude.json
```

**Dangerous directories:**
```
.git, .vscode, .idea, .claude
```

**Dangerous removal paths:**
- `*` or anything ending in `/*`
- Root `/`, home `~`, direct children of root (`/usr`, `/tmp`)
- Windows drive roots (`C:\`) and direct children (`C:\Windows`)

### B.9 Denial Tracking (`denialTracking.ts`)

```typescript
type DenialTrackingState = {
  consecutiveDenials: number
  totalDenials: number
}

const DENIAL_LIMITS = {
  maxConsecutive: 3,   // 3 blocks in a row → fall back to prompting
  maxTotal: 20,        // 20 total blocks in session → fall back to prompting
}
```

On allow: `consecutiveDenials = 0` (total preserved).
On deny: both increment.
On limit exceeded in headless: `throw AbortError`.
On limit exceeded in CLI: fall back to manual approval, reset counters.

### B.10 Auto Mode Allowlisted Tools (`classifierDecision.ts`)

Tools that **skip the classifier entirely** in auto mode (always auto-allowed):

```
Read-only:     FileRead, Grep, Glob, LSP, ToolSearch, ListMcpResources, ReadMcpResource
Task mgmt:     TodoWrite, TaskCreate/Get/Update/List/Stop/Output
Plan/UI:       AskUserQuestion, EnterPlanMode, ExitPlanMode
Coordination:  TeamCreate, TeamDelete, SendMessage, Workflow
Misc:          Sleep, TerminalCapture(ant), OverflowTest(ant), VerifyPlanExecution(ant)
Internal:      classify_result (the classifier tool itself)
```

### B.11 Shadowed Rule Detection (`shadowedRuleDetection.ts`)

Detects rules that will **never fire** due to higher-precedence rules:

1. **Allow shadowed by deny:** Tool-wide deny rule blocks specific allow rules
   - `Bash` in deny + `Bash(ls:*)` in allow → allow is unreachable
2. **Allow shadowed by ask:** Tool-wide ask rule overrides specific allow rules
   - `Bash` in ask + `Bash(ls:*)` in allow → user always prompted
   - Exception: Bash with sandbox auto-allow from personal settings

### B.12 Permission Explainer (`permissionExplainer.ts`)

Uses Haiku to generate structured explanations for permission prompts:

```typescript
type PermissionExplanation = {
  riskLevel: 'LOW' | 'MEDIUM' | 'HIGH'
  explanation: string    // What the command does (1-2 sentences)
  reasoning: string      // Why Claude is running it (starts with "I")
  risk: string           // What could go wrong (under 15 words)
}
```

Enabled by default; user can opt out via `permissionExplainerEnabled: false` in config.

### B.13 Permission Loading (`permissionsLoader.ts`)

**Source priority (all merged, not overridden):**
```
policySettings  → Enterprise managed (if allowManagedPermissionRulesOnly: true, ONLY these)
userSettings    → ~/.claude/settings.json
projectSettings → .claude/settings.json (committed)
localSettings   → .claude/settings.local.json (gitignored)
flagSettings    → --settings flag
cliArg          → --allowed-tools
command         → Slash command frontmatter
session         → In-memory
```

**Editable sources:** userSettings, projectSettings, localSettings.
**Read-only sources:** policySettings, flagSettings.

### B.14 Permission Setup (`permissionSetup.ts`)

**Auto mode entry:**
1. Find dangerous permissions (interpreters, wildcards, agents)
2. Strip them from context (stash in `strippedDangerousRules`)
3. Log each stripped rule
4. On exit: restore from stash

**Auto mode gate checks:**
- GrowthBook `tengu_auto_mode_enabled` gate
- Model support: `modelSupportsAutoMode()`
- Settings opt-in: `hasAutoModeOptIn()`
- Not disabled by circuit breaker: `!isAutoModeCircuitBroken()`
- Not in fast mode with `disableFastMode` config

**Mode transition (`transitionPermissionMode`):**
- Plan enter/exit: inject attachments
- Auto enter: `setAutoModeActive(true)`, strip dangerous permissions
- Auto exit: `setAutoModeActive(false)`, restore permissions

### B.15 Bypass Permissions Killswitch (`bypassPermissionsKillswitch.ts`)

Server-side killswitch to disable `--dangerously-skip-permissions`:
- Runs once before first query
- Checks GrowthBook gate via `shouldDisableBypassPermissions()`
- If disabled: creates context with `isBypassPermissionsModeAvailable: false`
- Reset on `/login` (re-checks with new org)

### B.16 Auto Mode State (`autoModeState.ts`)

Simple module-level boolean flags:
```typescript
let autoModeActive = false       // Is auto mode currently active?
let autoModeFlagCli = false      // Was --auto passed on CLI?
let autoModeCircuitBroken = false // Has the circuit breaker fired?
```

### B.17 MCP Tool Permission Matching

MCP tools use fully-qualified names: `mcp__serverName__toolName`

**Rule matching:**
- Exact: `mcp__server1__tool1` matches only that tool
- Server-level: `mcp__server1` matches ALL tools from server1
- Wildcard: `mcp__server1__*` matches ALL tools from server1
- In skip-prefix mode (`CLAUDE_AGENT_SDK_MCP_NO_PREFIX`): MCP tools have unprefixed display names but rules use the full `mcp__` name to avoid collisions with builtins

### B.18 Permission Updates

```typescript
type PermissionUpdate =
  | { type: 'addRules'; rules: PermissionRuleValue[]; behavior; destination }
  | { type: 'replaceRules'; rules: PermissionRuleValue[]; behavior; destination }
  | { type: 'removeRules'; rules: PermissionRuleValue[]; behavior; destination }
  | { type: 'setMode'; mode: ExternalPermissionMode; destination }
  | { type: 'addDirectories'; directories: string[]; destination }
  | { type: 'removeDirectories'; directories: string[]; destination }

type PermissionUpdateDestination =
  'userSettings' | 'projectSettings' | 'localSettings' | 'session' | 'cliArg'
```

---

## PART C: BRIDGE ↔ PERMISSIONS INTEGRATION

### C.1 Remote Permission Flow

When a tool invocation needs approval in a bridge session:

```
CLI Process:
  1. hasPermissionsToUseTool() returns {ask}
  2. CLI emits control_request:
     { type: 'control_request', request_id, request: { subtype: 'can_use_tool', tool_name, input, tool_use_id } }

Bridge (standalone):
  3. sessionRunner detects PermissionRequest on child stdout
  4. Forwards via api.sendPermissionResponseEvent() → server → web UI

Web UI (claude.ai):
  5. User sees tool invocation details
  6. User clicks Allow/Deny
  7. Server sends control_response back

Bridge:
  8. Transport receives control_response
  9. bridgePermissionCallbacks.sendResponse() fires
  10. Handler resolves the permission prompt

CLI Process:
  11. Tool executes (if allowed)
```

### C.2 Permission Mode from Bridge

The bridge can set the permission mode remotely:
```typescript
onSetPermissionMode?: (mode: PermissionMode) => { ok: true } | { ok: false; error: string }
```

Guard requirements before calling `transitionPermissionMode`:
- `auto`: check `isAutoModeGateEnabled()` first
- `bypassPermissions`: check `isBypassPermissionsModeDisabled()` AND `isBypassPermissionsModeAvailable`
- Without these guards, `transitionPermissionMode` throws and corrupts the 3-way invariant

### C.3 Bridge Session Environment Isolation

Standalone bridge child processes get a **stripped environment** (only allowlisted vars). The child:
- Has NO access to parent's API keys, DB passwords, proxy secrets
- Authenticates via `CLAUDE_CODE_SESSION_ACCESS_TOKEN` only
- Operates in `CLAUDE_CODE_ENVIRONMENT_KIND=bridge`
- Can be force-sandboxed via `CLAUDE_CODE_FORCE_SANDBOX=1`

---

## PART D: KEY PATTERNS AND HIDDEN DETAILS

### D.1 BoundedUUIDSet

Ring buffer for UUID dedup — 2000 capacity. Used for:
- `recentPostedUUIDs`: our echoes coming back on the transport
- `recentInboundUUIDs`: server re-deliveries we already forwarded
- `initialMessageUUIDs`: unbounded fallback for initial history

### D.2 FlushGate

During initial history flush, live writes are queued. After flush completes, queued messages drain in order. On transport close: queued messages are dropped with warning.

### D.3 Capacity Wake

Signal mechanism to wake the poll loop out of heartbeat sleep when the transport drops. Without this, there's a 60s delay before fast-polling resumes.

### D.4 Session ID Compatibility

Two ID formats:
- `session_*` — compat layer format
- `cse_*` — infra/CCR v2 format

`toCompatSessionId()` and `toInfraSessionId()` translate between them. The `/bridge/reconnect` endpoint may require either depending on server gate state.

### D.5 Perpetual Mode

For assistant-mode sessions that survive CLI restarts:
- Bridge pointer NOT cleared on clean exit
- On next start: read pointer → re-register with `reuseEnvironmentId` → `reconnectSession()`
- `reusedPriorSession` flag gates SSE seq-num seeding

### D.6 Overly Broad Bash/PowerShell Rules

`Bash` or `Bash(*)` in allow list = effectively bypass mode for shell commands. Detected and warned about. Stripped on auto mode entry.

### D.7 Classifier Two-Stage Architecture

The YOLO classifier supports a two-stage pipeline (stage1 + stage2), with separate usage/latency tracking for each stage. This enables escalation patterns where a fast first pass can approve simple cases and a deeper second pass handles complex ones.

### D.8 The Iron Gate

`tengu_iron_gate_closed` GrowthBook flag controls fail-closed vs fail-open behavior when the auto mode classifier API is unavailable:
- Gate on (default): deny the action (fail closed)
- Gate off: fall back to manual prompting (fail open)
- 30-minute refresh interval for the gate check
