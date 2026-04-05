# Claude Code v2.1.88 — Settings Analysis & Configuration Levers

**Extracted from:** Source map analysis of `/tmp/claude-code-2.1.88/package/cli.js.map`  
**Analysis Date:** 2025-03-31  
**Target Use:** Team SEAL agents running 24/7 — understanding all configuration pressure points

---

## EXECUTIVE SUMMARY

Claude Code v2.1.88 has **20 environment variables**, **16 feature flags** (via Bun's `feature()` system), **19 numeric limits**, and a 5-tier settings hierarchy. This document covers all hidden levers, undocumented flags, and killswitches that affect agent behavior, inference limits, and system behavior.

---

## 1. ENVIRONMENT VARIABLES

Complete list of all `process.env.*` variables recognized by Claude Code:

### 1.1 Authentication & OAuth

| Variable | Purpose | Type | Default | Scope |
|---|---|---|---|---|
| `CLAUDE_CODE_CUSTOM_OAUTH_URL` | Override OAuth endpoint URL | URL string | `undefined` | Global |
| `USE_LOCAL_OAUTH` | Use local OAuth server (dev mode) | boolean | `false` | Global |
| `USE_STAGING_OAUTH` | Route to staging OAuth | boolean | `false` | Global |
| `CLAUDE_LOCAL_OAUTH_API_BASE` | Local OAuth API endpoint | URL | `http://localhost:PORT` | Dev |
| `CLAUDE_LOCAL_OAUTH_APPS_BASE` | Local OAuth apps registry | URL | `http://localhost:PORT` | Dev |
| `CLAUDE_LOCAL_OAUTH_CONSOLE_BASE` | Local OAuth console | URL | `http://localhost:PORT` | Dev |
| `CLAUDE_CODE_OAUTH_CLIENT_ID` | Custom OAuth client ID | string | Anthropic default | Dev |

**Critical Notes:**
- `USE_LOCAL_OAUTH=true` + `CLAUDE_CODE_OAUTH_CLIENT_ID` = complete OAuth hijack capability
- `CLAUDE_CODE_CUSTOM_OAUTH_URL` bypasses Anthropic's auth entirely if set
- These are **not gated by managed-settings.json** — can be set by any .env file in project root

### 1.2 Debugging & Telemetry

| Variable | Purpose | Type | Default | Scope |
|---|---|---|---|---|
| `ENABLE_GROWTHBOOK_DEV` | Enable GrowthBook dev mode (exposes all feature flags) | boolean | `false` | Dev |
| `NODE_ENV` | Node runtime environment | `'development'` \| `'production'` | `'production'` | System |
| `CLAUDE_CODE_OVERRIDE_DATE` | Override system date (for testing) | ISO string | `undefined` | Test |
| `CLAUDE_CODE_SIMPLE` | Disable verbose output | boolean | `false` | Display |

**Critical Notes:**
- `ENABLE_GROWTHBOOK_DEV=true` exposes **ALL GrowthBook feature flags** (see section 3.2)
- `CLAUDE_CODE_OVERRIDE_DATE` allows manipulation of time-based logic (session tracking, feature rollouts, cache TTLs)
- Not validated — accepts any string

### 1.3 Feature Gates & Experimental

| Variable | Purpose | Type | Default | Scope |
|---|---|---|---|---|
| `CLAUDE_CODE_ENABLE_XAA` | Enable XAA (experimental agent architecture) | boolean | `false` | Agent |
| `CLAUDE_CODE_USE_COWORK_PLUGINS` | Use CoPilot-style plugins | boolean | `false` | Agent |
| `USER_TYPE` | User classification for feature gates | `'ant'` \| default | default | Agent |

**Critical Notes:**
- `USER_TYPE=ant` gates internal Anthropic-only features (CCR, Ultraplan, cost tracking, mdm settings)
- `CLAUDE_CODE_ENABLE_XAA` enables extended agent architecture (experimental reward modeling)
- These **ARE validated** against managed-settings.json

### 1.4 Plugin & Update Management

| Variable | Purpose | Type | Default | Scope |
|---|---|---|---|---|
| `FORCE_AUTOUPDATE_PLUGINS` | Force plugin auto-update even if disabled | boolean | `false` | System |
| `DISABLE_AUTOUPDATER` | Disable entire auto-update system | boolean | `false` | System |
| `CLAUDE_CODE_ATTRIBUTION_HEADER` | Custom HTTP attribution header for API calls | string | `"claude-code-2.1.88"` | Network |

**Critical Notes:**
- `DISABLE_AUTOUPDATER=true` prevents Claude Code from checking for updates
- `CLAUDE_CODE_ATTRIBUTION_HEADER` is sent to the Anthropic API; useful for tracking agent workload
- `FORCE_AUTOUPDATE_PLUGINS` bypasses user preference — could auto-restart plugins unexpectedly

### 1.5 Platform-Specific

| Variable | Purpose | Type | Default | Scope |
|---|---|---|---|---|
| `SHELL` | Shell interpreter (inferred by system) | path | Auto-detected | System |

---

## 2. FEATURE FLAGS (Bun `feature()` System)

These are **compile-time flags** set during build. They can be checked with `ENABLE_GROWTHBOOK_DEV=true` but cannot be changed at runtime.

### 2.1 Core Agent Capabilities

| Flag | Status | Impact | Agent-Relevant? |
|---|---|---|---|
| `AGENT_TRIGGERS` | ? | Enables `/loop` and scheduled task system | ⭐ **YES** |
| `AGENT_WORKFLOWS` | ? | Enables workflow scripting (advanced automation) | ⭐ **YES** |
| `VERIFICATION_AGENT` | ? | Enables autonomous verification sub-agent | ⭐ **YES** |

### 2.2 Memory & Context Management

| Flag | Status | Impact | Agent-Relevant? |
|---|---|---|---|
| `TEAMMEM` | ? | Enables shared team memory (multi-agent coordination) | ⭐ **YES** |
| `CACHED_MICROCOMPACT` | ? | Caches context summaries for faster retrieval | ✓ (Perf) |
| `TOKEN_BUDGET` | ? | Enables token budgeting across turns | ✓ (Limits) |

### 2.3 Audio & Voice

| Flag | Status | Impact | Agent-Relevant? |
|---|---|---|---|
| `VOICE_MODE` | ? | Enables voice input/output (`/voice` command) | ✓ (Input method) |
| `KAIROS` | ? | Multi-modal voice capabilities (experimental) | ✓ (Input method) |
| `KAIROS_BRIEF` | ? | Lite version of KAIROS | ✓ (Input method) |

### 2.4 Plugins & Integrations

| Flag | Status | Impact | Agent-Relevant? |
|---|---|---|---|
| `CONNECTOR_TEXT` | ? | Enables text-based MCP connectors | ✓ (MCP) |
| `EXPERIMENTAL_SKILL_SEARCH` | ? | Enables semantic search over skills (helps agent find tools) | ✓ (Tool discovery) |

### 2.5 Advanced/Internal

| Flag | Status | Impact | Agent-Relevant? |
|---|---|---|---|
| `CCR_AUTO_CONNECT` | Anthropic-only | Auto-connect to cloud Claude (remote inference) | ⚠️ Can override local-first |
| `NATIVE_CLIENT_ATTESTATION` | ? | Enable attestation for native client verification | ✓ (Security) |
| `WORKFLOW_SCRIPTS` | ? | Enable workflow scripting backend | ✓ (Automation) |
| `LODESTONE` | ? | Unknown (possibly internal routing) | ? |
| `PROACTIVE` | ? | Proactive completion suggestions | ? |
| `TRANSCRIPT_CLASSIFIER` | ? | ML-based conversation classification | ✓ (Analytics) |

**How to Check These at Runtime:**
```bash
# If ENABLE_GROWTHBOOK_DEV=true, open /config > Gates tab to see resolved flags
ENABLE_GROWTHBOOK_DEV=1 claude /config
```

---

## 3. GROWTHBOOK FEATURE FLAGS (Dynamic Runtime Flags)

GrowthBook flags are **not hardcoded** — they're served dynamically from Anthropic's backend. But several **hardcoded fallback values** exist in the source.

### 3.1 Known GrowthBook Flags (from source analysis)

| Flag Name | Fallback Value | Known Purpose | Agent Impact |
|---|---|---|---|
| `tengu_hive_evidence` | `false` | Hive protocol evidence collection | Unknown |
| `tengu_attribution_header` | `true` | Send attribution headers to API | Network tracking |
| `tengu_hawthorn_window` | `200_000` (chars) | Max tool results per message — **OVERRIDABLE** | ⭐ Context limit |
| `tengu_cicada_nap_ms` | Unknown | API call throttling interval | ⭐ Rate limiting |
| `tengu_config_*` | Various | Internal analytics events | Metrics |
| `tengu_run_hook` | Unknown | Hook execution tracking | Session behavior |
| `tengu_api_success` | Unknown | Tracks API success metrics | Metrics |
| `tengu_bridge_repl_v2_cse_shim_enabled` | Unknown | CSE bridge shimming for REPL | Bridge behavior |

**Critical Discovery:** `tengu_hawthorn_window` controls the **per-message tool result budget** and is **overridable via GrowthBook**. Default is 200,000 characters. If Anthropic wants to reduce agent context consumption, they can do this without a Claude Code update.

### 3.2 How to Override GrowthBook Flags (Ant-Only)

In `~/.claude/settings.json`:

```json
{
  "growthBookOverrides": {
    "tengu_hawthorn_window": 100000,
    "tengu_cicada_nap_ms": 500
  }
}
```

**Only works if:**
- `USER_TYPE=ant` is set
- Local GrowthBook dev mode is enabled (`ENABLE_GROWTHBOOK_DEV=true`)
- Settings file is writable

---

## 4. CONFIGURABLE LIMITS

All hardcoded limits that affect agent behavior and resource consumption:

### 4.1 API Limits (Anthropic-Enforced)

| Limit | Value | Type | Notes |
|---|---|---|---|
| `API_IMAGE_MAX_BASE64_SIZE` | 5 MB | Hard limit | Base64-encoded size; raw ≈ 3.75 MB |
| `IMAGE_MAX_WIDTH` | 2000 px | Client soft limit | API internal: 1568 px |
| `IMAGE_MAX_HEIGHT` | 2000 px | Client soft limit | API internal: 1568 px |
| `API_MAX_MEDIA_PER_REQUEST` | 100 | Hard limit | Images + PDFs combined |
| `API_PDF_MAX_PAGES` | 100 | Hard limit | PDF page limit |

### 4.2 PDF Processing Limits

| Limit | Value | Notes |
|---|---|---|
| `PDF_TARGET_RAW_SIZE` | 20 MB | Max raw PDF before API rejects |
| `PDF_EXTRACT_SIZE_THRESHOLD` | 3 MB | Above this, PDFs are converted to page images |
| `PDF_MAX_EXTRACT_SIZE` | 100 MB | Max file size for extraction (larger rejected) |
| `PDF_MAX_PAGES_PER_READ` | 20 | Max pages the `Read` tool will extract in one call |
| `PDF_AT_MENTION_INLINE_THRESHOLD` | 10 pages | Above this, PDFs get reference treatment instead of inline |

**Agent Impact:** Large PDFs (>20MB) will fail silently. PDFs >3MB get extracted as page images, which affects token counting.

### 4.3 Tool Result Limits

| Limit | Value | Type | Overridable? | Notes |
|---|---|---|---|---|
| `DEFAULT_MAX_RESULT_SIZE_CHARS` | 50,000 chars | Default | Per-tool | If tool result > 50K, saved to disk; model gets filepath instead |
| `MAX_TOOL_RESULT_TOKENS` | 100,000 tokens | Hard cap | No | ~400KB text equivalent |
| `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS` | 200,000 chars | Per-message | **YES** (GrowthBook) | Budget for all parallel tool results in one turn |
| `TOOL_SUMMARY_MAX_LENGTH` | 50 chars | Display only | No | For grouped agent rendering |
| `BYTES_PER_TOKEN` | 4 | Estimate | No | Used for token-to-byte conversion |

**Critical Agent Impact:**
- If agent runs 5 parallel tools that each return 50K output, total = 250K chars
- With default `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200K`, **3 tools will be preserved, 2 replaced with file paths**
- This is **invisible to the agent** — no error, just truncation
- `tengu_hawthorn_window` can reduce this to 100K (50% fewer results) on the fly

### 4.4 Parsing & Processing Limits

| Limit | Value | Notes |
|---|---|---|
| `BINARY_CHECK_SIZE` | 8192 bytes | Size of file header checked to detect binary files |
| `MAX_SLOW_OPERATIONS` | 10 | Max JSON parse/stringify ops before warning |
| `MAX_IN_MEMORY_ERRORS` | 100 | Max error log entries kept in memory |
| `MDM_SUBPROCESS_TIMEOUT_MS` | 5000 ms | Timeout for managed-settings subprocess |
| `MIN_BACKUP_INTERVAL_MS` | 60 ms | Minimum time between config backups |

### 4.5 Error Recovery & Timeouts

All these are **hardcoded** — cannot be changed at runtime:

| Limit | Value | Context |
|---|---|---|
| `Config file lock timeout` | (Check source) | How long to wait for `~/.claude/claude.json` lock |
| `API retry backoff` | (Check source) | Exponential backoff for failed API calls |
| `Session reuse timeout` | (Check source) | How long a session ID stays valid before re-auth |

---

## 5. HIDDEN SETTINGS IN settings.json

These are configurable in `~/.claude/settings.json` but not well-documented in the UI:

### 5.1 Agent Behavior Settings

```json
{
  "hooks": {
    "beforeTurn": [],       // Run arbitrary code before each agent turn
    "afterTurn": [],        // Run after each turn completes
    "onError": [],          // Error handler
    "onAPICall": [],        // Intercept API calls (spy on usage)
    "beforeFile": [],       // Before any file operation
    "afterFile": []         // After file changes
  },
  "env": {
    // Custom environment variables for spawned processes
    "MY_VAR": "value"
  },
  "memoryType": "user" | "project" | "local" | "managed" | "team",
  "allowedTools": [],       // Whitelist of allowed tools
  "disabledTools": [],      // Blacklist (if allowedTools is empty)
  "memoryExclusions": [],   // Glob patterns to exclude from /memory saves
  
  // DANGEROUS — controls what Claude Code can do
  "permissions": {
    "allowUnreviewedFileWrites": false,
    "allowDestructiveCommands": false,
    "requireExplicitApprovalFor": ["git:force-push", "rm -rf /"]
  }
}
```

### 5.2 Notification Settings

```json
{
  "preferredNotifChannel": "native" | "email" | "slack" | "custom",
  "customNotifyCommand": "notify-send 'Claude Code' '{message}'",
  
  // Push notifications (new in 2.1.88)
  "taskCompleteNotifEnabled": false,
  "inputNeededNotifEnabled": false,
  "agentPushNotifEnabled": false
}
```

### 5.3 Context & Token Management

```json
{
  "showStatusInTerminalTab": true,  // OSC 21337 terminal status
  "terminalProgressBarEnabled": true,  // OSC 9;4 progress
  "fileCheckpointingEnabled": true,  // Save file state for recovery
  
  // Display behavior
  "showTurnDuration": true,  // Show "Cooked for 1m 6s" messages
  "diffTool": "terminal" | "auto"  // How to show diffs
}
```

### 5.4 Caching & Performance

```json
{
  "autoCompactEnabled": true,  // Auto-compact context summaries
  "respectGitignore": true,     // File picker respects .gitignore
  "copyFullResponse": false     // /copy behavior
}
```

---

## 6. KILLSWITCHES & EMERGENCY OVERRIDES

Settings that can **immediately disable functionality**:

### 6.1 Agent Killswitches

| Switch | Effect | Who Controls |
|---|---|---|
| `DISABLE_AUTOUPDATER=true` | Prevents Claude Code updates | User env var |
| `/config` > Disable all plugins | Stops all MCP servers | User UI |
| Managed settings policy | Can disable entire feature classes | Enterprise MDM |
| `managedSettings.json` | Policy-enforced settings (read-only) | Organization |

### 6.2 Context Killswitches

```json
{
  // Force agent to not use memory
  "memoryType": "local",
  "memoryExclusions": ["**/*"],  // Exclude everything
  
  // Force small context window
  "growthBookOverrides": {
    "tengu_hawthorn_window": 10000  // 10K char max per turn
  }
}
```

### 6.3 Tool Killswitches

```json
{
  "allowedTools": [],  // Empty list = only no-op tools work
  "disabledTools": ["bash", "git", "edit"]  // Explicit blacklist
}
```

### 6.4 Network Killswitches

```json
{
  "env": {
    "DISABLE_AUTOUPDATER": "true",
    "USE_LOCAL_OAUTH": "true"  // Force local OAuth (stops cloud)
  }
}
```

---

## 7. SETTINGS HIERARCHY (What Overrides What)

**Order of precedence (lowest to highest):**

1. **Defaults** — Hardcoded in Claude Code source
2. **User settings** (`~/.claude/settings.json`)
3. **Project settings** (`.claude/settings.json` in project root)
4. **Local settings** (`.claude/.local.json` — gitignored)
5. **CLI flags** (`--setting key=value`)
6. **Managed/Policy** (`managed-settings.json` or MDM API)
7. **Environment variables** (`CLAUDE_CODE_*` env vars) — **HIGHEST PRIORITY**

**Critical:** Environment variables beat everything. An attacker with env var injection can bypass all settings-file restrictions.

---

## 8. AGENT-SPECIFIC CONFIGURATION PRESSURE POINTS

For Team SEAL (ADA, JARVIS, DUM) running 24/7:

### 8.1 Context Window Pressure Points

```bash
# These limit how much ADA/JARVIS can see before hitting context:
- tengu_hawthorn_window (tool results per turn)
- PDF_MAX_PAGES_PER_READ (20 page limit per Read call)
- MAX_TOOL_RESULT_TOKENS (100K token hard cap)
- API max request size (32 MB total)
```

### 8.2 Execution Frequency Pressure Points

```bash
# These control how fast agents can operate:
- MDM_SUBPROCESS_TIMEOUT_MS (5 seconds for managed settings subprocess)
- MIN_BACKUP_INTERVAL_MS (60ms between config saves)
- tengu_cicada_nap_ms (API call throttling)
- FORCE_AUTOUPDATE_PLUGINS (can trigger unexpected restarts)
```

### 8.3 Tool Availability Pressure Points

```bash
# These control what tools agents can use:
- allowedTools / disabledTools (whitelist/blacklist)
- Token budget for tool results (200K chars/turn default)
- API_MAX_MEDIA_PER_REQUEST (100 items max)
- BINARY_CHECK_SIZE (8KB — larger binary files auto-rejected)
```

### 8.4 Memory Persistence Pressure Points

```bash
# These control what agents can remember:
- memoryType (user|project|local|managed|team)
- memoryExclusions (glob patterns to ignore)
- TEAMMEM feature flag (required for multi-agent memory)
- growthBookOverrides (can reduce available memory storage)
```

---

## 9. MONITORING & ANALYTICS

Claude Code emits these telemetry events (can be disabled with privacy settings):

```typescript
// Internal events logged:
- tengu_config_cache_stats          // Config file lock/cache performance
- tengu_config_auth_loss_prevented  // Auth token refresh recovery
- tengu_config_lock_contention      // Concurrent access to config
- tengu_config_stale_write          // Stale write detection
- tengu_config_parse_error          // Config file corruption recovery
- tengu_run_hook                    // Hook execution tracking
- tengu_api_success                 // API call success/failure
- tengu_auto_mode_decision          // Auto mode decisions
- tengu_binary_feedback             // User corrections to agent output
```

**Agent Impact:** These events are sent to Anthropic and can be used to profile agent behavior patterns.

---

## 10. CRITICAL FINDINGS FOR TEAM SEAL

### 10.1 ⭐ Most Important Levers

1. **`tengu_hawthorn_window`** — Directly controls tool result budget. Anthropic can halve agent context without an update.
2. **`CLAUDE_CODE_ENABLE_XAA`** — Enables experimental agent architecture. Unclear what capabilities unlock.
3. **`AGENT_TRIGGERS` feature flag** — Your `/loop` system depends on this.
4. **`FORCE_AUTOUPDATE_PLUGINS`** — Can force restarts of your MCP servers.
5. **`CLAUDE_CODE_CUSTOM_OAUTH_URL`** — Can hijack all OAuth if set; no validation.

### 10.2 Recommendations for 24/7 Operation

```bash
# In ~/.claude/settings.json for ADA/JARVIS stability:
{
  "env": {
    "DISABLE_AUTOUPDATER": "true",  // Don't auto-restart during long tasks
    "ENABLE_GROWTHBOOK_DEV": "true"  // See all feature flags (dev-only)
  },
  
  "growthBookOverrides": {
    // Document what you're changing and why
    "tengu_hawthorn_window": 200000  // Ensure tool results aren't silently truncated
  },
  
  "hooks": {
    "afterTurn": [
      // Log turn metrics for monitoring
      {"type": "command", "command": "echo 'Turn complete' >> ~/turn-metrics.log"}
    ]
  },
  
  "memoryType": "team",  // Enable SEAL multi-agent coordination
  "fileCheckpointingEnabled": true,  // Save state for recovery
  "taskCompleteNotifEnabled": true,  // Know when tasks finish
  
  "permissions": {
    "allowDestructiveCommands": false,  // Safety
    "requireExplicitApprovalFor": ["git:force-push"]
  }
}
```

### 10.3 Monitoring Checklist

- [ ] Track `tengu_hawthorn_window` from GrowthBook (in /config > Gates)
- [ ] Monitor `MDM_SUBPROCESS_TIMEOUT_MS` for managed-settings delays
- [ ] Set `DISABLE_AUTOUPDATER=true` during critical operations
- [ ] Use hooks to instrument turn-by-turn metrics
- [ ] Alert if `AGENT_TRIGGERS` feature flag is disabled
- [ ] Verify `memoryType: 'team'` for multi-agent coordination

---

## 11. UNDOCUMENTED FEATURES & EASTER EGGS

From source inspection:

- **`/buddy` command** — Spawns a companion soul (AI-driven buddy). Configuration in `globalSettings.companion`.
- **`/config Gates tab`** — Exposes all feature flags and GrowthBook overrides (ant-only).
- **Skill usage tracking** — `skillUsage` field in globalConfig records which skills are used most.
- **GitHub repo path mapping** — Can map `owner/repo` to local clone paths for teleport support.
- **iTerm2 key binding auto-install** — Automatically sets up Shift+Enter in iTerm2.

---

## 12. COMPLIANCE & SECURITY

- **PII Handling:** See `getEssentialTrafficOnlyReason()` for privacy level checks
- **Managed Settings:** Requires `CLAUDE_CODE_MANAGED_SETTINGS_PATH` or MDM policy
- **Trust Dialog:** `hasTrustDialogAccepted` gates the initial trust prompt
- **File Permissions:** `bypassPermissionsModeAccepted` — uses Unix permissions if true

---

## Appendix: File Locations

```
~/.claude/settings.json              # User global settings
~/.claude/claude.json                # Global config cache (auto-generated)
~/.claude/cache/                     # Transient cache
~/.claude/memory/                    # User memory (when memoryType='user')
.claude/settings.json                # Project settings (checked in)
.claude/.local.json                  # Project local settings (gitignored)
managed-settings.json                # Enterprise managed settings (read-only)
```

---

## Summary Table: All 20 Environment Variables

| Variable | Default | Type | Gated by MDM? |
|---|---|---|---|
| `CLAUDE_CODE_ATTRIBUTION_HEADER` | See constant | string | No |
| `CLAUDE_CODE_CUSTOM_OAUTH_URL` | undefined | URL | No |
| `CLAUDE_CODE_ENABLE_XAA` | false | boolean | Yes |
| `CLAUDE_CODE_OVERRIDE_DATE` | undefined | ISO string | No |
| `CLAUDE_CODE_SIMPLE` | false | boolean | No |
| `CLAUDE_CODE_USE_COWORK_PLUGINS` | false | boolean | Yes |
| `CLAUDE_CODE_OAUTH_CLIENT_ID` | Anthropic default | string | No |
| `CLAUDE_LOCAL_OAUTH_API_BASE` | localhost | URL | No |
| `CLAUDE_LOCAL_OAUTH_APPS_BASE` | localhost | URL | No |
| `CLAUDE_LOCAL_OAUTH_CONSOLE_BASE` | localhost | URL | No |
| `DISABLE_AUTOUPDATER` | false | boolean | No |
| `ENABLE_GROWTHBOOK_DEV` | false | boolean | No |
| `FORCE_AUTOUPDATE_PLUGINS` | false | boolean | No |
| `NODE_ENV` | 'production' | string | No |
| `SHELL` | Auto-detected | path | System |
| `USER_TYPE` | default | 'ant' \| default | Yes |
| `USE_LOCAL_OAUTH` | false | boolean | No |
| `USE_STAGING_OAUTH` | false | boolean | No |
| `CLAUDE_CODE_MANAGED_SETTINGS_PATH` | (computed) | path | System |
| `CLAUDE_CODE_ENTRYPOINT` | (computed) | path | System |

---

## References & Source Files

Extracted from:
- `src/utils/config.ts` — Main config system (63K)
- `src/utils/settings/settings.ts` — Settings loader (32K)
- `src/utils/settings/types.ts` — Settings schema (43K)
- `src/constants/apiLimits.ts` — API limits
- `src/constants/toolLimits.ts` — Tool result limits
- `src/utils/settings/constants.ts` — Settings hierarchy
- `src/bootstrap/state.ts` — Initial bootstrap state (56K)

**Total analyzed:** 55 source files, ~300KB of TypeScript

---

*Generated: 2025-03-31 | Team SEAL Operation Manual | For: William Henry Tovar Urquia*
