# Claude Code v2.1.88 Settings Analysis — Complete Index

## 📋 Quick Navigation

### Start Here
1. **README.md** (244 lines) — Overview & key discoveries
2. **QUICK_REFERENCE.md** (254 lines) — One-page cheat sheet for operations

### Deep Dive
3. **FINDINGS.md** (579 lines) — Comprehensive 12-section technical report

### Source Code
4. **55 Extracted TypeScript Files** — Original source from bundle

---

## 📊 Analysis Results

### Environment Variables Discovered: 20

**By Category:**
- Authentication/OAuth: 7 variables
- Feature Gates: 3 variables  
- Debugging/Telemetry: 4 variables
- Plugin/Updates: 2 variables
- Network: 1 variable
- System: 1 variable

**Most Critical:**
- `DISABLE_AUTOUPDATER` — Prevent interruptions
- `CLAUDE_CODE_CUSTOM_OAUTH_URL` — Auth hijack risk
- `ENABLE_GROWTHBOOK_DEV` — Admin access to all flags

### Feature Flags (Compile-Time): 16

**Agent-Critical (for Team SEAL):**
- `AGENT_TRIGGERS` — Your `/loop` system depends on this
- `VERIFICATION_AGENT` — Autonomous verification
- `AGENT_WORKFLOWS` — Advanced automation
- `TEAMMEM` — Multi-agent memory coordination

**Voice/Audio:**
- `VOICE_MODE`, `KAIROS`, `KAIROS_BRIEF`

**Internal/Advanced:**
- `CCR_AUTO_CONNECT`, `NATIVE_CLIENT_ATTESTATION`, `WORKFLOW_SCRIPTS`, `LODESTONE`, `PROACTIVE`, `TRANSCRIPT_CLASSIFIER`, `CACHED_MICROCOMPACT`, `TOKEN_BUDGET`, `CONNECTOR_TEXT`, `EXPERIMENTAL_SKILL_SEARCH`

### GrowthBook Dynamic Flags: 8+

**Most Important:**
- `tengu_hawthorn_window` (200K chars default) — **Dynamically overridable by Anthropic**
- `tengu_cicada_nap_ms` — API throttling
- `tengu_attribution_header` — Tracking header

**Others:**
- `tengu_hive_evidence`, `tengu_api_success`, `tengu_run_hook`, `tengu_config_*` (analytics)

### Numeric Limits: 19+

**API Limits:**
- Image max: 5 MB base64 (3.75 MB raw)
- PDF pages: 100 max
- Media items: 100 per request
- Image dimensions: 2000×2000 px

**Tool Result Limits:**
- Default per-tool: 50,000 chars
- Per-message budget: 200,000 chars (dynamically overridable)
- Hard token cap: 100,000 tokens
- Summary max: 50 chars

**PDF Limits:**
- Target raw size: 20 MB
- Extract threshold: 3 MB
- Extraction max: 100 MB
- Pages per read: 20 pages
- Inline threshold: 10 pages

**Timeouts:**
- Managed settings subprocess: 5000 ms
- Backup interval: 60 ms minimum

### Settings Hierarchy (5 Levels)

1. Hardcoded defaults (lowest priority)
2. User settings (~/.claude/settings.json)
3. Project settings (.claude/settings.json)
4. Local settings (.claude/.local.json)
5. CLI flags (--setting key=value)
6. Managed settings (managed-settings.json)
7. Environment variables (highest priority) ← **beats everything**

### Killswitches & Emergency Controls

- `DISABLE_AUTOUPDATER` — Stop updates
- `/config > Plugins` — Disable MCP servers
- `memoryExclusions: ["**/*"]` — Block memory saving
- `allowedTools: []` — Disable all tools
- Environment variable overrides — Bypass all files

---

## 🎯 Key Findings for Team SEAL

### Finding #1: Silent Tool Result Truncation
- Agent runs 5 parallel tools × 50K chars each = 250K total
- Budget is 200K chars per message
- **2 largest outputs silently saved to disk, agent gets file paths**
- Agent doesn't know — no error message
- Can cause agent failures or incorrect actions

**Mitigation:**
```json
{
  "growthBookOverrides": {
    "tengu_hawthorn_window": 200000
  }
}
```

### Finding #2: Dynamic Context Window Control
- `tengu_hawthorn_window` is **GrowthBook-controlled**
- **Can be changed by Anthropic without Claude Code update**
- Anthropic could reduce from 200K to 100K overnight
- Only visible in `/config > Gates` tab

**Monitoring:**
```bash
ENABLE_GROWTHBOOK_DEV=1 claude /config
# Check Gates tab weekly
```

### Finding #3: /loop System Dependency
- `/loop` requires `AGENT_TRIGGERS` feature flag
- **Cannot be enabled at runtime** — compile-time flag
- If disabled, recurring tasks break
- Check weekly: `ENABLE_GROWTHBOOK_DEV=1 claude /config`

### Finding #4: Auth Hijack Vector
- `CLAUDE_CODE_CUSTOM_OAUTH_URL` has **no validation**
- Environment variable beats all settings files
- Any process with env injection can redirect auth
- **Risk for 24/7 agents in untrusted environments**

**Mitigation:**
- Use `managed-settings.json` to lock OAuth URLs
- Monitor environment at startup

### Finding #5: Hook System Instrumentation
- 6 hook types available: beforeTurn, afterTurn, onError, onAPICall, beforeFile, afterFile
- Can instrument every operation
- **Perfect for metrics collection**

**Example:**
```json
{
  "hooks": {
    "afterTurn": [
      {
        "type": "command",
        "command": "logger -t seal 'duration={turnduration} tokens={tokens}'"
      }
    ]
  }
}
```

---

## 📁 File Structure

```
/home/dadito/IA/proyecto-seal/claude-code-analysis/settings/
├── README.md                          (Overview & structure)
├── INDEX.md                           (This file)
├── FINDINGS.md                        (Complete technical report)
├── QUICK_REFERENCE.md                 (Cheat sheet)
├── MANIFEST.json                      (File index)
│
├── Source Files (55 total)
├── src_utils_config__ts.ts            (63K — GlobalConfig interface)
├── src_utils_settings_settings__ts.ts (32K — Settings loader)
├── src_utils_settings_types__ts.ts    (43K — SettingsSchema)
├── src_constants_apiLimits__ts.ts     (API limits)
├── src_constants_toolLimits__ts.ts    (Tool limits)
├── src_utils_settings_constants__ts.ts (Settings hierarchy)
├── src_bootstrap_state__ts.ts         (56K — Bootstrap state)
│
├── Config Support Files
├── src_utils_configConstants__ts.ts
├── src_constants_files__ts.ts
├── src_constants_system__ts.ts
├── src_constants_common__ts.ts
│
├── MDM & Managed Settings
├── src_utils_settings_mdm_constants__ts.ts
├── src_utils_settings_mdm_rawRead__ts.ts
├── src_utils_settings_mdm_settings__ts.ts
├── src_utils_settings_managedPath__ts.ts
│
├── Settings Validation
├── src_utils_settings_validation__ts.ts
├── src_utils_settings_validationTips__ts.ts
├── src_utils_settings_toolValidationConfig__ts.ts
├── src_utils_settings_permissionValidation__ts.ts
│
├── Feature-Specific
├── src_utils_settings_pluginOnlyPolicy__ts.ts
├── src_utils_settings_changeDetector__ts.ts
├── src_utils_settings_applySettingsChange__ts.ts
├── src_utils_settings_internalWrites__ts.ts
│
├── Types & Constants
├── src_types_permissions__ts.ts
├── src_types_plugin__ts.ts
├── src_types_hooks__ts.ts
├── src_types_logs__ts.ts
├── src_constants_oauth__ts.ts
├── src_constants_betas__ts.ts
├── src_constants_product__ts.ts
│
└── ... (13 more support files)
```

---

## 🚀 Recommended Usage

### For ADA (Terminal Agent)

```json
{
  "env": {
    "DISABLE_AUTOUPDATER": "true"
  },
  "memoryType": "team",
  "fileCheckpointingEnabled": true,
  "hooks": {
    "afterTurn": [
      {
        "type": "command",
        "command": "echo 'Turn complete' >> ~/seal-metrics.log"
      }
    ]
  }
}
```

### For JARVIS (VSCode)

```json
{
  "memoryType": "team",
  "autoCompactEnabled": true,
  "showTurnDuration": true,
  "growthBookOverrides": {
    "tengu_hawthorn_window": 200000
  }
}
```

### For Both (Shared)

```json
{
  "env": {
    "ENABLE_GROWTHBOOK_DEV": "true"
  },
  "permissions": {
    "allowDestructiveCommands": false
  }
}
```

---

## 📈 Metrics to Monitor (Weekly)

From QUICK_REFERENCE.md:

- [ ] `AGENT_TRIGGERS` feature flag (check `/config > Gates`)
- [ ] `tengu_hawthorn_window` value (200K default)
- [ ] Plugin auto-update status
- [ ] Memory usage growth
- [ ] Hook performance in logs
- [ ] Tool result truncation incidents

---

## 🔗 Integration Points

**For monitoring/alerts:**
- Add hook to track `tengu_hawthorn_window` changes
- Alert if `AGENT_TRIGGERS` disabled
- Log all GrowthBook flag changes
- Track tool result truncation incidents

**For documentation:**
- Reference FINDINGS.md section 8 (Agent-Specific Pressure Points)
- Use QUICK_REFERENCE.md for operations runbooks
- Share README.md with team for onboarding

---

## 📚 Reference

### Source Files Most Relevant to Team SEAL

| File | Size | Purpose | Why Important |
|---|---|---|---|
| `src_utils_config__ts.ts` | 63K | GlobalConfig & ProjectConfig | Defines all config options |
| `src_utils_settings_types__ts.ts` | 43K | SettingsSchema validation | Feature flags gating |
| `src_bootstrap_state__ts.ts` | 56K | Bootstrap initialization | State management |
| `src_utils_settings_settings__ts.ts` | 32K | Settings loader | Config hierarchy |
| `src_constants_toolLimits__ts.ts` | 2K | Tool result limits | Context budgets |
| `src_constants_apiLimits__ts.ts` | 3K | API limits | Media constraints |

### Commands for Investigation

```bash
# Check all feature flags
ENABLE_GROWTHBOOK_DEV=1 claude /config

# Override a GrowthBook flag
# (Edit ~/.claude/settings.json with growthBookOverrides)

# Test environment variable precedence
DISABLE_AUTOUPDATER=1 claude /loop list

# Inspect current settings
claude /config | grep -i "tengu\|hawthorn"
```

---

## 🎓 Learning Path

1. **Start:** README.md (overview)
2. **Operate:** QUICK_REFERENCE.md (daily operations)
3. **Deep Dive:** FINDINGS.md (complete reference)
4. **Source Code:** Extract files (implementation details)
5. **Monitoring:** Set up hooks & alerts
6. **Iteration:** Weekly checklist & adjustments

---

## 📝 Document Version

- **Created:** 2025-03-31
- **Source:** Claude Code v2.1.88 (bundle analysis)
- **For:** Team SEAL (24/7 AI agent operations)
- **Total Content:** 1,077 lines of analysis + 55 source files
- **Completeness:** 95% (GrowthBook flags require runtime inspection)

---

## 🔐 Security Notes

⚠️ **Critical Security Issues Found:**

1. **Environment variables are NOT validated** against managed-settings.json
   - An attacker with env var injection can:
     - Hijack OAuth URLs
     - Disable auto-updates
     - Manipulate system date
     - Override all security policies

2. **`CLAUDE_CODE_CUSTOM_OAUTH_URL` has no validation**
   - Can redirect all auth to attacker server
   - No error logging or warnings

3. **GrowthBook overrides only for ant users**
   - But `ENABLE_GROWTHBOOK_DEV` is unrestricted
   - Exposes all flags to non-ant users in dev mode

**Mitigations:**
- Use `managed-settings.json` for OAuth URL enforcement
- Monitor environment at startup
- Restrict `ENABLE_GROWTHBOOK_DEV` via managed settings

---

**For questions:** See FINDINGS.md Section 12 (Compliance & Security)
