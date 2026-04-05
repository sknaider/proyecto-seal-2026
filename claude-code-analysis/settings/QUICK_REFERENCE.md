# Claude Code v2.1.88 — Quick Reference Card

**For Team SEAL 24/7 Operations**

---

## Kill/Control Switches (One-Liners)

```bash
# Disable auto-updates (prevent restarts during critical ops)
export DISABLE_AUTOUPDATER=true

# Enable all feature flags & GrowthBook overrides (admin view)
export ENABLE_GROWTHBOOK_DEV=true

# Override system date (test time-based logic)
export CLAUDE_CODE_OVERRIDE_DATE="2025-03-31T15:00:00Z"

# Force OAuth hijack (test isolation)
export CLAUDE_CODE_CUSTOM_OAUTH_URL="http://localhost:8080/oauth"

# Reduce tool context budget by 50% (stress test)
# Set in ~/.claude/settings.json:
# "growthBookOverrides": { "tengu_hawthorn_window": 100000 }
```

---

## Settings.json Configuration for ADA/JARVIS

```json
{
  "env": {
    "DISABLE_AUTOUPDATER": "true",
    "ENABLE_GROWTHBOOK_DEV": "true"
  },
  
  "memoryType": "team",
  
  "hooks": {
    "afterTurn": [
      {
        "type": "prompt",
        "prompt": "Log this turn's metrics: {turnduration}, {tokencount}, {toolcount}"
      }
    ]
  },
  
  "fileCheckpointingEnabled": true,
  "showTurnDuration": true,
  "autoCompactEnabled": true,
  
  "growthBookOverrides": {
    "tengu_hawthorn_window": 200000
  },
  
  "permissions": {
    "allowDestructiveCommands": false,
    "requireExplicitApprovalFor": ["git:force-push", "rm -rf /"]
  }
}
```

---

## Critical Limits (Agent Context)

| What | Value | Can Agent See? | Impact |
|---|---|---|---|
| Per-message tool result budget | 200K chars | ❌ No — silent truncation | 2-3 large outputs vanish per turn |
| PDF max size | 20 MB | ⚠️ API error | Larger PDFs fail silently |
| PDF page limit | 100 pages | ✓ Yes | Rejected with error |
| Max media items per request | 100 | ✓ Yes | Rejected with error |
| Tool result size default | 50K chars | ✓ Maybe | Large outputs saved to disk |
| Max tool result tokens | 100K | ❌ No | Hard cap, no error |
| Image base64 max | 5 MB | ✓ Yes | API error |

---

## Feature Flags (Compile-Time — Can't Change)

| Flag | Most Important For Team? | Impact |
|---|---|---|
| `AGENT_TRIGGERS` | ⭐⭐⭐ | Your `/loop` system depends on this |
| `VERIFICATION_AGENT` | ⭐⭐ | Autonomous verification sub-agent |
| `VOICE_MODE` | ⭐ | Voice input (if needed) |
| `TEAMMEM` | ⭐⭐⭐ | Multi-agent memory coordination |
| `CCR_AUTO_CONNECT` | ⚠️ Risk | Could force cloud inference |

**Check Status:**
```bash
ENABLE_GROWTHBOOK_DEV=1 claude /config
# Look at Gates tab for resolved flags
```

---

## GrowthBook Dynamic Flags (Runtime — Can Override)

| Flag | Default | What To Monitor |
|---|---|---|
| `tengu_hawthorn_window` | 200K chars | Controls tool result budget per turn |
| `tengu_cicada_nap_ms` | ??? | API throttling interval |
| `tengu_attribution_header` | true | Whether to send tracking header |

**Override Example:**
```json
{
  "growthBookOverrides": {
    "tengu_hawthorn_window": 100000,
    "tengu_cicada_nap_ms": 500
  }
}
```

---

## Emergency Disable Commands

```bash
# Disable all MCP servers
claude /config  # → Plugins → toggle each off

# Disable auto-updates
export DISABLE_AUTOUPDATER=true

# Force local-only mode (no cloud OAuth)
export USE_LOCAL_OAUTH=true

# Limit context window for testing
# In settings.json:
# "memoryExclusions": ["**/*"]  # Don't save memory
```

---

## Settings Hierarchy (Precedence)

1. Hardcoded defaults (lowest)
2. `~/.claude/settings.json` (user global)
3. `.claude/settings.json` (project)
4. `.claude/.local.json` (project local, gitignored)
5. CLI flags (`--setting key=value`)
6. `managed-settings.json` (enterprise)
7. Environment variables (highest) ← **env vars beat everything**

**Exploit:** If `DISABLE_AUTOUPDATER=1` is set in env, it overrides all settings files.

---

## Sensitive Environment Variables (No Validation)

⚠️ These are **NOT validated** against managed-settings.json:

- `CLAUDE_CODE_CUSTOM_OAUTH_URL` — Can hijack auth
- `CLAUDE_CODE_OVERRIDE_DATE` — Can manipulate time-based logic
- `CLAUDE_CODE_SIMPLE` — UI only
- `DISABLE_AUTOUPDATER` — Can prevent security updates

---

## What Happens When Agent Uses Too Much Context

1. Agent requests read large file → returns 50K+ chars
2. Agent runs 5 parallel tools, each returns 50K
3. Total per-message = 250K chars (exceeds 200K budget)
4. Claude Code silently saves 2 outputs to disk
5. Agent sees file paths instead of content
6. **Agent doesn't know** — no error, no warning
7. Agent may fail or take wrong action based on missing data

**Prevention:**
```bash
# Reduce budget to test behavior
"growthBookOverrides": { "tengu_hawthorn_window": 100000 }
```

---

## Monitoring Checklist for 24/7 Ops

- [ ] Check `AGENT_TRIGGERS` feature flag weekly (in `/config > Gates`)
- [ ] Monitor for `tengu_hawthorn_window` changes (can be reduced by Anthropic)
- [ ] Log all `tengu_api_success` events (throughput tracking)
- [ ] Alert if `DISABLE_AUTOUPDATER` changes unexpectedly
- [ ] Verify `memoryType: "team"` for multi-agent coordination
- [ ] Set hook to log context window usage per turn
- [ ] Monitor `MDM_SUBPROCESS_TIMEOUT_MS` (5 sec) for managed settings delays

---

## Testing Commands

```bash
# Test GrowthBook feature access
ENABLE_GROWTHBOOK_DEV=1 claude /config

# Test auth hijacking
export CLAUDE_CODE_CUSTOM_OAUTH_URL="http://localhost:9999/oauth"
claude /login

# Test time manipulation
export CLAUDE_CODE_OVERRIDE_DATE="2020-01-01T00:00:00Z"
claude "what's the date?"

# Test auto-update suppression
DISABLE_AUTOUPDATER=1 claude

# Test PDF limits
claude "read /path/to/200-page-pdf.pdf"
# Watch for silent truncation at 100 pages
```

---

## Source Files Reference

- **Main Config:** `src/utils/config.ts` (63K) — GlobalConfig, ProjectConfig types
- **Settings Loader:** `src/utils/settings/settings.ts` (32K) — SettingsJson schema
- **API Limits:** `src/constants/apiLimits.ts` — Anthropic-enforced limits
- **Tool Limits:** `src/constants/toolLimits.ts` — Tool result budgets
- **Feature Gates:** `src/utils/settings/types.ts` (43K) — SettingsSchema with feature() gates

---

## Key Insight

**The most dangerous setting is `tengu_hawthorn_window`.** It controls tool result context budget and is:
- **Overridable** (GrowthBook)
- **Dynamic** (can change without Claude Code update)
- **Silent** (agent doesn't see truncation)
- **Per-turn** (affects every turn's output capacity)

If Anthropic needs to reduce agent throughput or context consumption, they can cut this in half without you knowing.

**Recommendation:** Add monitoring hook:
```json
{
  "hooks": {
    "afterTurn": [
      {
        "type": "command",
        "command": "echo 'Tool result budget: tengu_hawthorn_window={{ growthBookFeature tengu_hawthorn_window }}' >> ~/seal-monitoring.log"
      }
    ]
  }
}
```

---

*Last Updated: 2025-03-31*  
*Source: Claude Code v2.1.88 source map analysis*  
*Team SEAL — Proyecto SEAL*
