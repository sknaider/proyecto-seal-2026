# Model Routing Findings — Claude Code v2.1.88

## 1. Model Selection Architecture

### Priority Order (Highest to Lowest)
Claude Code implements a clear 5-tier priority for model selection in `getMainLoopModel()`:

1. **Session Override** (`/model` command) — highest priority
2. **Startup Override** (`--model` flag at launch)
3. **Environment Variable** (`ANTHROPIC_MODEL` env var)
4. **Settings** (from `settings.json`)
5. **Default** (built-in fallback per subscription tier)

Code path: `/model → --model flag → ANTHROPIC_MODEL → settings.json → getDefaultMainLoopModel()`

The model is validated against `availableModels` allowlist in settings at each stage.

---

## 2. Multi-Tier Subscription Defaults

### Tier-Based Model Assignment
**File:** `model.ts` — `getDefaultMainLoopModelSetting()`

| Subscription Tier | Default Model | 1M Context Available |
|---|---|---|
| Ants (internal) | Configured via `tengu_ant_model_override` feature flag | Yes (Opus 1M) |
| Max subscribers | Opus 4.6 (+ 1M if merge enabled) | Yes |
| Team Premium | Opus 4.6 (+ 1M if merge enabled) | Yes |
| Pro/Team Standard/Enterprise | Sonnet 4.6 | Conditional access via `checkSonnet1mAccess()` |
| PAYG (1P & 3P) | Sonnet 4.6 (3P may use Sonnet 4.5) | Conditional access |

**Key constraint:** `isOpus1mMergeEnabled()` guards Opus[1m] availability:
- Returns false if: 1M disabled globally, user is Pro, or API provider is 3P
- Fails closed if subscription type unknown (prevents API rejection with misleading rate-limit error)

---

## 3. Provider-Specific Routing

### APIProvider Types & Routing Logic
**File:** `providers.ts`

```typescript
type APIProvider = 'firstParty' | 'bedrock' | 'vertex' | 'foundry'
```

**Selection order (via environment variables):**
1. `CLAUDE_CODE_USE_BEDROCK` → 'bedrock'
2. `CLAUDE_CODE_USE_VERTEX` → 'vertex'
3. `CLAUDE_CODE_USE_FOUNDRY` → 'foundry'
4. (default) → 'firstParty'

### Provider-Specific Model String Resolution
**File:** `modelStrings.ts`

Each model tier (Haiku, Sonnet, Opus) maps to **provider-specific model IDs** via `ALL_MODEL_CONFIGS`:

**Example (Opus 4.6):**
- **firstParty:** `claude-opus-4-6`
- **bedrock:** `us.anthropic.claude-opus-4-6-v1`
- **vertex:** `claude-opus-4-6`
- **foundry:** `claude-opus-4-6`

**Key optimization:** Bedrock inference profiles are fetched asynchronously:
- `getBedrockInferenceProfiles()` queries AWS ListInferenceProfilesCommand
- Profiles are matched against canonical first-party IDs (e.g., "claude-opus-4-6" substring match in "eu.anthropic.claude-opus-4-6-v1")
- Falls back to hardcoded Bedrock model IDs if profiles unavailable
- Uses memoization to avoid repeated AWS API calls

### Model Override Mechanism
Users can override provider-specific models via `settings.json`:
```json
{
  "modelOverrides": {
    "claude-opus-4-6": "arn:aws:bedrock:us-east-1:123:inference-profile/custom-opus"
  }
}
```

`resolveOverriddenModel()` reverses the mapping for canonical name lookup.

---

## 4. Bedrock Cross-Region Routing

### Bedrock Region Prefix Extraction & Application
**File:** `bedrock.ts`

**Region prefixes:** `us` | `eu` | `apac` | `global`

**Extraction function:** `getBedrockRegionPrefix(modelId)`
- Handles both plain IDs and full ARN format
- Example: `"eu.anthropic.claude-sonnet-4-5-20250929-v1:0"` → `"eu"`
- Returns `undefined` for foundation models without prefix

**Application function:** `applyBedrockRegionPrefix(modelId, prefix)`
- Replaces existing prefix or adds new one if absent
- Foundation models: `"anthropic.claude-x"` → `"eu.anthropic.claude-x"`
- Preserves original format for non-Bedrock models

### Sub-Agent Inheritance (Critical for IAM Scoping)
**File:** `agent.ts` — `getAgentModel()`

When a parent uses Bedrock cross-region inference (e.g., `eu.anthropic.*`):
1. Extract parent's region prefix via `getBedrockRegionPrefix(parentModel)`
2. If subagent specifies an alias (`opus`, `sonnet`, `haiku`):
   - Resolve alias to full model ID
   - Apply parent's region prefix unless subagent's spec already carries a different prefix
3. This prevents silent data-residency violations when IAM only permits specific regions

Example:
```
Parent: eu.anthropic.claude-opus-4-6-v1
Subagent: model="opus"
Result: eu.anthropic.claude-opus-4-6-v1 (inherits parent's EU region)
```

---

## 5. Model Capabilities & Context Window

### Dynamic Capability Fetching
**File:** `modelCapabilities.ts`

Ants (internal users) only:
- `refreshModelCapabilities()` calls Anthropic models.list() API with OAUTH_BETA_HEADER
- Caches results in `~/.claude/cache/model-capabilities.json` (mode 0o600)
- Schema: `{ id: string, max_input_tokens?: number, max_tokens?: number }`
- Cache invalidation: only re-written if capabilities differ (`isEqual()` check)

**3P Capability Overrides:** `get3PModelCapabilityOverride()`
- Overrides keyed by tier: `ANTHROPIC_DEFAULT_*_MODEL` pins with `ANTHROPIC_DEFAULT_*_MODEL_SUPPORTED_CAPABILITIES`
- Example: `ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES="effort,thinking,adaptive_thinking"`
- Returns `boolean | undefined` per capability type

### Context Window Support Check
**File:** `context.ts` (imported)
- `modelSupports1M()` — checks if model supports 1M context window
- `has1mContext()` — checks for `[1m]` suffix in model string

---

## 6. Sub-Agent Model Selection (JARVIS/ADA Pattern)

### Agent Model Options
**File:** `agent.ts`

Available sub-agent model options:
```typescript
const AGENT_MODEL_OPTIONS = [...MODEL_ALIASES, 'inherit'] as const
// = 'sonnet' | 'opus' | 'haiku' | 'best' | 'sonnet[1m]' | 'opus[1m]' | 'opusplan' | 'inherit'
```

**Default:** `getDefaultSubagentModel()` returns `'inherit'`

### Routing Logic for Sub-Agents

1. **Environment Override:** `CLAUDE_CODE_SUBAGENT_MODEL`
   - Bypasses all other logic if set

2. **Tool-Specified Model:** (from skill frontmatter)
   - If alias matches parent tier, use parent's exact model
   - Otherwise resolve alias and apply parent's Bedrock region prefix

3. **Agent Config Model** (user-specified)
   - If `'inherit'`: apply `getRuntimeMainLoopModel()` to resolve opusplan→Opus in plan mode
   - If alias matches parent tier: use parent's exact model
   - Otherwise resolve and apply parent's region prefix

4. **Tier-Matching Function:** `aliasMatchesParentTier()`
   - Prevents surprising downgrades in 3P environments
   - Only bare aliases match (`opus`, `sonnet`, `haiku`)
   - `opus[1m]`, `best`, `opusplan` always resolve to defaults

**Example (TEAM SEAL architecture):**
- **JARVIS** (parent on Opus 4.6): spawns ADA sub-agent with `model: 'inherit'`
  - ADA receives Opus 4.6 (inherits parent's exact version)
- **ADA** with `model: 'sonnet'`: spawns DUM sub-agent with `model: 'haiku'`
  - DUM receives Haiku 4.5 (not forced to inherit ADA's Sonnet)

---

## 7. Model Aliases & Resolution

### Core Aliases
**File:** `aliases.ts`

```typescript
const MODEL_ALIASES = [
  'sonnet',      // Resolves to current Sonnet default (4.6 for 1P, 4.5 for 3P)
  'opus',        // Resolves to current Opus default (4.6)
  'haiku',       // Resolves to current Haiku default (4.5)
  'best',        // Resolves to best available (Opus 4.6)
  'sonnet[1m]',  // Sonnet with 1M context
  'opus[1m]',    // Opus with 1M context
  'opusplan',    // Special: Opus in plan mode, Sonnet otherwise
]
```

### Alias Resolution Flow
**File:** `model.ts` — `parseUserSpecifiedModel()`

1. Trim and lowercase input
2. Extract `[1m]` suffix if present
3. If bare alias (without [1m]): resolve to provider default + reapply suffix
4. If Ant model: lookup in `getAntModels()` config
5. If legacy Opus (4.0/4.1) on 1P: remap to current default
6. Preserve case for custom model names (Azure Foundry deployment IDs)

**Special case: opusplan**
- Default to Sonnet in normal mode
- Upgrade to Opus in plan mode (only if `exceeds200kTokens` is false)
- Checked via `getRuntimeMainLoopModel()` at runtime

---

## 8. Deprecation Handling

### Deprecated Models Registry
**File:** `deprecation.ts`

```typescript
const DEPRECATED_MODELS: Record<string, DeprecationEntry> = {
  'claude-3-opus': {
    retirementDates: {
      firstParty: 'January 5, 2026',
      bedrock: 'January 15, 2026',
      vertex: 'January 5, 2026',
      foundry: 'January 5, 2026',
    }
  },
  'claude-3-7-sonnet': {
    retirementDates: {
      firstParty: 'February 19, 2026',
      bedrock: 'April 28, 2026',
      vertex: 'May 11, 2026',
      foundry: 'February 19, 2026',
    }
  },
  'claude-3-5-haiku': {
    retirementDates: {
      firstParty: 'February 19, 2026',
      bedrock: null,       // Not deprecated on Bedrock
      vertex: null,
      foundry: null,
    }
  }
}
```

### Warning Message
`getModelDeprecationWarning(modelId)` returns:
```
⚠ [Model Name] will be retired on [Date]. Consider switching to a newer model.
```

Provider-aware: only warns if the provider has a retirement date.

---

## 9. Model Allowlist & Access Control

### Allowlist Matching Strategy
**File:** `modelAllowlist.ts` — `isModelAllowed()`

Five matching tiers (evaluated in order):

1. **Direct Match** (exact string after normalization)
   - Handles alias-to-alias (`'sonnet'` → `'sonnet'`)
   - Skips if family alias is narrowed by specific entries

2. **Family Aliases** (wildcard for entire tier)
   - `["opus"]` allows all Opus models
   - `["opus", "opus-4-5"]` restricts to only Opus 4.5 (narrowing rule)

3. **Alias Resolution** (resolve aliases before comparison)
   - Input alias `'sonnet'` resolves to full ID
   - Checks if resolved ID is in allowlist

4. **Reverse Alias Resolution**
   - Allowlist entry `'opus'` resolves to full ID
   - Checks if full ID matches input

5. **Version-Prefix Matching** (segment-boundary matching)
   - `"opus-4-5"` or `"claude-opus-4-5"` matches `"claude-opus-4-5-20251101"`
   - Boundary check: match must end with `-` or be exact length

### Narrowing Rule (Critical for Admin Control)
When allowlist has both `"opus"` and `"opus-4-5"`:
- Bare `"opus"` is treated as narrowed to specific versions only
- Only models matching version prefixes are allowed
- Prevents unintended wildcard from overriding restrictions

---

## 10. Model Picker Options & Display

### Context-Aware Option Generation
**File:** `modelOptions.ts` — `getModelOptions()`

Options differ by **user tier + API provider**:

#### Max/Team Premium Users
- Default (recommended)
- Opus 4.6 [1M] (if `checkOpus1mAccess()`)
- Sonnet 4.6
- Sonnet 4.6 [1M] (if `checkSonnet1mAccess()`)
- Haiku 4.5

#### Pro/Team Standard/Enterprise
- Default (recommended, Sonnet 4.6)
- Sonnet 4.6 [1M] (if eligible)
- Opus 4.6 (or Opus [1M] if `isOpus1mMergeEnabled()`)
- Haiku 4.5

#### PAYG 1P (firstParty API)
- Default
- Sonnet 4.6 [1M] (if eligible)
- Opus 4.6 or Opus [1M]
- Haiku 4.5

#### PAYG 3P (bedrock/vertex/foundry)
- Default
- Custom Sonnet (if `ANTHROPIC_DEFAULT_SONNET_MODEL` set) OR Sonnet 4.6 + 1M variant
- Custom Opus (if set) OR Opus 4.1 + Opus 4.6 + 1M variant
- Custom Haiku (if set) OR Haiku (4.5 or 3.5)

### 3P Fallback Chain
When user selects unavailable model on 3P provider:
```
Opus 4.6 (not found) → suggest Opus 4.1
Sonnet 4.6 (not found) → suggest Sonnet 4.5
Sonnet 4.5 (not found) → suggest Sonnet 4.0
```

---

## 11. Ant-Only (Internal) Model Configuration

### Feature Flag Driven
**File:** `antModels.ts`

Internal models sourced from GrowthBook feature flag: `tengu_ant_model_override`

**AntModel schema:**
```typescript
type AntModel = {
  alias: string              // E.g., 'cherub' (codename masked in display)
  model: string              // Full model ID
  label: string
  description?: string
  defaultEffortValue?: number
  defaultMaxTokens?: number
  upperMaxTokensLimit?: number
  alwaysOnThinking?: boolean // Model defaults to adaptive thinking
  contextWindow?: number
}
```

**Codename Masking:** Masked to `cap*****-v2-fast` format (preserve suffix)

**Override Config:** `AntModelOverrideConfig`
- `defaultModel` — custom Ant default
- `defaultModelEffortLevel` — thinking effort tier
- `defaultSystemPromptSuffix` — appended to system prompt
- `switchCallout` — UI notification for model changes

---

## 12. Skill Model Resolution

### Carrying [1m] Context Window Across Models

**File:** `model.ts` — `resolveSkillModelOverride()`

Problem: Skill author writes `model: opus` expecting "use Opus-class reasoning", not "downgrade to 200K context".

Solution: If current model is opus[1m] and skill specifies `opus` (bare alias):
- Check if skill's target supports 1M via `modelSupports1M()`
- If yes, append `[1m]` suffix
- If no (e.g., Haiku has no 1M), downgrade is intentional

Logic prevents autocompact false positives at 23% token usage on 1M sessions.

---

## 13. Model Validation

### Validation Strategy
**File:** `validateModel.ts` — `validateModel()`

1. **Allowlist Check** — return false if not in `availableModels`
2. **Alias Check** — aliases always valid (no API call)
3. **Custom Model Check** — if matches `ANTHROPIC_CUSTOM_MODEL_OPTION`, valid
4. **Cache Check** — return cached validation if available
5. **API Call** — make minimal request (`max_tokens: 1`, ephemeral cache)

**Error Handling:**
- `NotFoundError` (404) → suggest 3P fallback model if available
- `AuthenticationError` → credentials issue
- `APIConnectionError` → network issue
- Generic `APIError` → check error body for "model not found" type

**Cache:** `validModelCache` Map (in-memory per session)

---

## 14. Model Configuration Constants

### Provider-Aware Model Configs
**File:** `configs.ts`

All models registered in `ALL_MODEL_CONFIGS`:
```typescript
export const ALL_MODEL_CONFIGS = {
  haiku35: CLAUDE_3_5_HAIKU_CONFIG,
  haiku45: CLAUDE_HAIKU_4_5_CONFIG,
  sonnet35: CLAUDE_3_5_V2_SONNET_CONFIG,
  sonnet37: CLAUDE_3_7_SONNET_CONFIG,
  sonnet40: CLAUDE_SONNET_4_CONFIG,
  sonnet45: CLAUDE_SONNET_4_5_CONFIG,
  sonnet46: CLAUDE_SONNET_4_6_CONFIG,
  opus40: CLAUDE_OPUS_4_CONFIG,
  opus41: CLAUDE_OPUS_4_1_CONFIG,
  opus45: CLAUDE_OPUS_4_5_CONFIG,
  opus46: CLAUDE_OPUS_4_6_CONFIG,
}
```

**Canonical ID Mapping:** `CANONICAL_ID_TO_KEY` (e.g., `'claude-opus-4-6'` → `'opus46'`)

Each config maps canonical name to provider-specific strings:
```typescript
CLAUDE_OPUS_4_6_CONFIG = {
  firstParty: 'claude-opus-4-6',
  bedrock: 'us.anthropic.claude-opus-4-6-v1',
  vertex: 'claude-opus-4-6',
  foundry: 'claude-opus-4-6',
}
```

---

## 15. Runtime Model Resolution Path (Complete Flow)

### For Main Thread
1. **Priority check:** Session override → startup flag → env var → settings → default
2. **Allowlist validation** via `isModelAllowed()`
3. **Alias resolution** via `parseUserSpecifiedModel()` (if alias, resolve to provider default + [1m] if applicable)
4. **Provider resolution** via `getModelStrings()` (map internal key to provider-specific ID)
5. **Bedrock profile lookup** (if Bedrock, try to match inference profiles async)
6. **Override resolution** via `resolveOverriddenModel()` (check settings.modelOverrides)
7. **Context window check** — append [1m] if eligible

### For Sub-Agents
1. **Env override check:** `CLAUDE_CODE_SUBAGENT_MODEL`
2. **Tool model override** (if skill specifies `model:`)
3. **Agent config model** (user-specified in agent definition)
4. **Alias tier matching** (if bare alias matches parent tier, inherit parent's exact model)
5. **Parent region prefix inheritance** (Bedrock only, unless subagent spec has its own prefix)
6. **Default fallback** (`'inherit'`, which resolves to parent at runtime)

---

## 16. Key Optimizations for Multi-Agent Teams

### For JARVIS/ADA/DUM Architecture

1. **Model Inheritance:** Sub-agents with `model: 'inherit'` get parent's exact version
   - Prevents version downgrade surprises
   - Works with 3P provider region prefixes

2. **Tier Matching:** `aliasMatchesParentTier()`
   - `opus` alias on parent Opus → child gets parent's exact Opus
   - Avoids unintended cross-model spawning

3. **Bedrock Region Continuity:** Cross-region inference prefix automatically inherited
   - Parent on `eu.anthropic.*` → subagents on `eu.anthropic.*`
   - Prevents data-residency violations in IAM-scoped deployments

4. **Capability Overrides:** 3P teams can pin Sonnet/Opus with specific capabilities
   - `ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES="thinking,adaptive_thinking,effort"`
   - Ensures all agents in team have consistent feature access

5. **Context Window Consistency:** 1M eligibility checked per-tier, not per-agent
   - Max users always have 1M if merge enabled
   - Pro users need explicit extra usage credits
   - PAYG always has 1M access (no billing gate)

---

## 17. Deprecation & Legacy Handling

### Legacy Opus Remap
First-party users on old Opus (4.0/4.1) silently remapped to current default (4.6):
```typescript
const LEGACY_OPUS_FIRSTPARTY = [
  'claude-opus-4-20250514',
  'claude-opus-4-1-20250805',
  'claude-opus-4-0',
  'claude-opus-4-1',
]
```

Opt-out: `CLAUDE_CODE_DISABLE_LEGACY_MODEL_REMAP=true`

**3P providers:** Models passed through unchanged (they may lag on latest versions)

---

## 18. Marketing Name Mapping

**File:** `model.ts` — `getMarketingNameForModel()`

Maps full model IDs to display names:
- `claude-opus-4-6` → `"Opus 4.6"`
- `claude-opus-4-6 + [1m]` → `"Opus 4.6 (with 1M context)"`
- `claude-sonnet-4-6` → `"Sonnet 4.6"`
- `claude-haiku-4-5` → `"Haiku 4.5"`

Used in version-upgrade hints on model picker.

---

## 19. Display & Rendering Functions

### Model Name Rendering
- `renderModelName()` — returns human-readable name (Ant users see masked codename)
- `renderModelSetting()` — renders alias setting (Opus Plan, Sonnet, etc.)
- `modelDisplayString()` — full string with resolution chain for UI
- `getPublicModelName()` — for git commit trailers (e.g., "Claude Opus 4.6")

---

## Summary: Multi-Agent Team Routing Strategy

For optimal TEAM SEAL deployment:

1. **JARVIS (main, Opus 4.6):**
   - Keep `model: null` (defaults to Opus for Max users)
   - Sub-agents with `inherit` get Opus 4.6

2. **ADA (terminal, Sonnet 4.6):**
   - Spawn with `model: 'sonnet'` (inherits 4.6 from 1P default)
   - If 3P Bedrock, set `ANTHROPIC_DEFAULT_SONNET_MODEL=us.anthropic.claude-sonnet-4-6`

3. **DUM (local inference, Haiku 4.5):**
   - Spawn with `model: 'haiku'` (gets 4.5 default)
   - Or directly specify `model: 'claude-haiku-4-5-20251001'` for pinned version

4. **Bedrock Teams (data sovereignty):**
   - Set `CLAUDE_CODE_USE_BEDROCK=true`
   - Set `ANTHROPIC_DEFAULT_*_MODEL=eu.anthropic.claude-*-v1` for region
   - All sub-agents auto-inherit `eu.` prefix via `getBedrockRegionPrefix()` logic
   - Pin IAM to specific inference profiles matching the prefix

5. **Capability Gating:**
   - Use `checkOpus1mAccess()` / `checkSonnet1mAccess()` before offering 1M options
   - Extra usage enabled check prevents misleading "rate limit" errors

---

**Generated:** March 2026  
**Context:** Claude Code v2.1.88 source analysis
