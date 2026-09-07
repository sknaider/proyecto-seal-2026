# Claude Code v2.1.88 Settings Analysis — Complete Documentation

## Contents

This directory contains a complete analysis of Claude Code's settings, configuration system, feature flags, and limits extracted from the v2.1.88 source map.

### Files in This Directory

1. **FINDINGS.md** — Comprehensive 12-section report
   - All 20 environment variables with descriptions
   - All 16 feature flags (Bun compile-time)
   - All GrowthBook dynamic flags
   - Complete limits reference (API, PDF, tool results)
   - Settings hierarchy & overrides
   - Killswitches & emergency controls
   - Agent-specific pressure points
   - Recommendations for Team SEAL 24/7 operations

2. **QUICK_REFERENCE.md** — One-page cheat sheet
   - Critical limits table
   - Settings.json template for ADA/JARVIS
   - Kill switches (one-liners)
   - Feature flag status
   - Monitoring checklist
   - Testing commands

3. **MANIFEST.json** — Index of extracted TypeScript files
   - 55 source files extracted
   - File paths and sizes
   - Coverage information

4. **Extracted TypeScript Files** (src_*.ts)
   - All settings-related source files from the compiled bundle
   - Includes:
     - `src_utils_config__ts.ts` — GlobalConfig interface (63K)
     - `src_utils_settings_settings__ts.ts` — Settings loader (32K)
     - `src_utils_settings_types__ts.ts` — SettingsSchema (43K)
     - `src_constants_apiLimits__ts.ts` — API limits
     - `src_constants_toolLimits__ts.ts` — Tool result limits
     - `src_utils_settings_constants__ts.ts` — Settings hierarchy
     - `src_bootstrap_state__ts.ts` — Bootstrap state (56K)
     - And 47 more support files

## Key Discoveries

### Critical Control Points for Team SEAL

1. **`tengu_hawthorn_window`** (GrowthBook)
   - Controls per-message tool result budget
   - Default: 200,000 chars
   - **Dynamically overridable by Anthropic**
   - Silent truncation — agent doesn't see it

2. **`AGENT_TRIGGERS` feature flag**
   - Required for `/loop` system
   - Compile-time flag (can't change)
   - Check status with: `ENABLE_GROWTHBOOK_DEV=1 claude /config`

3. **`DISABLE_AUTOUPDATER` environment variable**
   - Prevents Claude Code updates
   - No validation — env vars beat all settings files
   - Use for stable 24/7 operations

4. **`USER_TYPE=ant`**
   - Gates Anthropic-internal features (CCR, Ultraplan, etc.)
   - Not accessible to most users
   - Controls experimental agent architecture

### Hidden Settings (Not Documented in UI)

- **Hooks system** — `beforeTurn`, `afterTurn`, `onError`, `onAPICall`, `beforeFile`, `afterFile`
- **Managed settings** — `managed-settings.json` (enterprise/MDM)
- **GrowthBook overrides** — Local flag overrides in settings.json (ant-only)
- **Skill usage tracking** — Ranked by frequency
- **Companion soul** — `/buddy` command with AI-driven companion

### Numeric Limits (Can't Be Changed at Runtime)

| Category | Key Limits |
|---|---|
| **API** | 5 MB base64 image max, 100 PDF pages max, 100 media items max |
| **PDF** | 20 MB raw size, 3 MB extraction threshold, 100 MB extraction max |
| **Tools** | 50K char default per tool, 200K chars per message, 100K token hard cap |
| **Timeouts** | 5 sec for managed settings subprocess |

## Settings Hierarchy

**Lowest to Highest Priority:**

1. Hardcoded defaults
2. `~/.claude/settings.json` (user global)
3. `.claude/settings.json` (project checked-in)
4. `.claude/.local.json` (project local, gitignored)
5. CLI flags (`--setting key=value`)
6. `managed-settings.json` (enterprise)
7. **Environment variables** (HIGHEST — beats everything)

## 20 Environment Variables

All unique `process.env.*` variables recognized by Claude Code:

**Authentication & OAuth (7):**
- `CLAUDE_CODE_CUSTOM_OAUTH_URL`
- `CLAUDE_CODE_OAUTH_CLIENT_ID`
- `CLAUDE_LOCAL_OAUTH_API_BASE`
- `CLAUDE_LOCAL_OAUTH_APPS_BASE`
- `CLAUDE_LOCAL_OAUTH_CONSOLE_BASE`
- `USE_LOCAL_OAUTH`
- `USE_STAGING_OAUTH`

**Feature Gates (3):**
- `CLAUDE_CODE_ENABLE_XAA`
- `CLAUDE_CODE_USE_COWORK_PLUGINS`
- `USER_TYPE`

**Debugging & Telemetry (4):**
- `ENABLE_GROWTHBOOK_DEV`
- `NODE_ENV`
- `CLAUDE_CODE_OVERRIDE_DATE`
- `CLAUDE_CODE_SIMPLE`

**Plugin & Updates (2):**
- `FORCE_AUTOUPDATE_PLUGINS`
- `DISABLE_AUTOUPDATER`

**Network (1):**
- `CLAUDE_CODE_ATTRIBUTION_HEADER`

**System (1):**
- `SHELL`

**Note:** Environment variables are **NOT validated** against managed-settings.json. An attacker with env var injection can bypass all security settings.

## 16 Feature Flags (Compile-Time)

**Agent-Critical:**
- `AGENT_TRIGGERS` — `/loop` system
- `VERIFICATION_AGENT` — Autonomous verification
- `AGENT_WORKFLOWS` — Workflow scripting

**Memory & Context:**
- `TEAMMEM` — Multi-agent memory
- `CACHED_MICROCOMPACT` — Context caching
- `TOKEN_BUDGET` — Token budgeting

**Voice/Audio:**
- `VOICE_MODE`, `KAIROS`, `KAIROS_BRIEF`

**Plugins & Tools:**
- `CONNECTOR_TEXT`, `EXPERIMENTAL_SKILL_SEARCH`

**Internal/Advanced:**
- `CCR_AUTO_CONNECT`, `NATIVE_CLIENT_ATTESTATION`, `WORKFLOW_SCRIPTS`, `LODESTONE`, `PROACTIVE`, `TRANSCRIPT_CLASSIFIER`

## Recommendations for Claude Code Agent Teams

### For 24/7 Stable Operations

```json
{
  "env": {
    "DISABLE_AUTOUPDATER": "true",
    "ENABLE_GROWTHBOOK_DEV": "true"
  },
  "memoryType": "team",
  "fileCheckpointingEnabled": true,
  "autoCompactEnabled": true,
  "growthBookOverrides": {
    "tengu_hawthorn_window": 200000
  }
}
```

### Monitoring Hooks

```json
{
  "hooks": {
    "afterTurn": [
      {
        "type": "command",
        "command": "logger -t seal-agent 'Turn complete: duration={turnduration} tokens={tokens} tools={toolcount}'"
      }
    ],
    "onAPICall": [
      {
        "type": "command", 
        "command": "echo '{timestamp} API Call' >> ~/seal-api-calls.log"
      }
    ]
  }
}
```

### Weekly Checklist

- [ ] Verify `AGENT_TRIGGERS` flag still enabled (`claude /config > Gates`)
- [ ] Check `tengu_hawthorn_window` value hasn't changed
- [ ] Audit hook performance in logs
- [ ] Review memory usage growth
- [ ] Test PDF and image handling limits
- [ ] Verify plugin auto-updates disabled

## Analysis Methodology

1. **Source Map Parsing** — Extracted JSON from `cli.js.map`
2. **TypeScript Extraction** — Retrieved 55 source files from `sourcesContent`
3. **Pattern Matching** — Found all `process.env.*`, `feature()` calls, and constants
4. **Code Analysis** — Read interfaces, types, and configuration code
5. **Documentation Review** — Cross-referenced with available docs

## Completeness

- **Environment Variables:** 20/20 identified
- **Feature Flags:** 16/16 identified
- **GrowthBook Flags:** Partial (hardcoded fallbacks found)
- **Numeric Limits:** 19+ identified
- **Settings Hierarchy:** Complete (5 levels)
- **Killswitches:** 6+ identified
- **Undocumented Features:** 5+ found

## Limitations

- GrowthBook flags are dynamic — fallback values extracted but current values require runtime inspection
- Some feature flag purposes inferred from naming conventions
- Managed settings schema not fully analyzed (requires MDM server)
- Hooks system documented but execution semantics not fully traced

## Further Research

If running Team SEAL 24/7, consider:

1. **Monitoring tool** to track `tengu_hawthorn_window` changes
2. **Hook system deep dive** — use for turn-by-turn metrics
3. **GrowthBook API integration** — pull live flag values
4. **Managed settings schema** — understand enterprise policies
5. **Plugin system analysis** — MCP server management

---

**Extracted:** 2025-03-31  
**Source:** Claude Code v2.1.88 bundle  
**For:** Team SEAL (William Henry Tovar Urquia, Proyecto SEAL)  
**Scope:** Complete configuration analysis for 24/7 AI agent operations
