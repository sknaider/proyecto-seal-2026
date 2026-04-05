# SPEC 12 — OpenClaude Modifications to Claude Code Source Files

> Analysis of what OpenClaude changed in **existing** Claude Code files (not new files).
> Compared: `~/IA/proyecto-seal/claude-code-analysis/full_src/` (original) vs `~/IA/proyecto-seal/openclaude-ref/src/` (OpenClaude)

**Summary:** 740 modified files, 116 new files added. The core mission: make Claude Code work with any OpenAI-compatible API provider (OpenAI, Gemini, GitHub Models, Ollama, Codex, etc.) instead of only Anthropic's API.

---

## 1. services/api/client.ts — Provider Routing at Client Creation

**Purpose:** Route API calls to OpenAI-compatible endpoints instead of Anthropic.

**Changes:**
- **Added `providerOverride` parameter** to `createClient()` function signature — allows per-agent provider routing (model, baseURL, apiKey)
- **Added OpenAI shim injection block** (~30 lines): When `providerOverride` is set OR env vars `CLAUDE_CODE_USE_OPENAI/GITHUB/GEMINI` are truthy, imports `createOpenAIShimClient` from `./openaiShim.js` and returns it cast as `Anthropic` client
- **Security: strips auth headers** when routing to third-party endpoints — filters out `authorization`, `x-api-key`, `api-key` from defaultHeaders to prevent credential leaks (SSRF mitigation)
- **Replaced static imports** with `importRuntimeModule()` dynamic import wrapper (a `new Function('specifier', 'return import(specifier)')`) for `@anthropic-ai/foundry-sdk`, `@azure/identity`, `@anthropic-ai/vertex-sdk`, `google-auth-library` — avoids bundler issues with optional deps
- **Removed `GoogleAuth` type import** — replaced with inline structural type to avoid hard dependency

**Lines changed:** ~60 additions/modifications

---

## 2. services/api/claude.ts — Core Query Engine

**Purpose:** Thread provider overrides through the entire query pipeline.

**Changes:**
- **Added `providerOverride` to Options type** and threaded it through `ClientOptions`, `createClientOptions`, and into `createClient()` calls
- **Disabled prompt caching for non-Anthropic providers** (~8 lines): New guard checks `getAPIProvider()` — if not `firstParty`, `bedrock`, or `vertex`, returns `false` for cache eligibility. Third-party providers reject `cache_control` blocks.
- **Latched Sonnet 1M experiment** — moved experiment check before the retry closure to prevent mid-retry GrowthBook refreshes from changing the beta header and busting cache keys
- **Reordered JSON body fields** — moved `system` BEFORE `messages` in the request object literal. Critical: Bun attestation (`Attestation.zig`) overwrites the FIRST `cch=00000` sentinel in serialized body. If `messages` appears first and contains the literal, wrong occurrence gets replaced.
- **Propagated `providerOverride`** through `createOpenAIClient` call at line ~2552

**Lines changed:** ~40 additions/modifications

---

## 3. services/api/withRetry.ts — Retry Logic for Multi-Provider

**Purpose:** Handle rate limiting and errors from OpenAI/Codex/GitHub providers, not just Anthropic.

**Changes:**
- **Added `isQuotaExhausted()` function** (~8 lines): Detects 429 errors with "limit: 0" or "exceeded your current quota" — throws `CannotRetryError` with user-friendly message suggesting `/provider` switch
- **Added `parseOpenAIDuration()` exported function** (~15 lines): Parses OpenAI-style relative duration strings ("1s", "6m0s", "1h30m0s", "500ms") into milliseconds for retry-after headers
- **Rewrote `getRateLimitResetDelayMs()`** from 8-line Anthropic-only to ~45-line multi-provider:
  - `firstParty`: uses `anthropic-ratelimit-unified-reset` header (unix timestamp)
  - `openai/codex/github`: uses `x-ratelimit-reset-requests` and `x-ratelimit-reset-tokens` headers (duration strings), takes the larger delay
  - `bedrock/vertex/foundry/gemini`: returns null (no standard reset header)
- **Imported `getAPIProvider`** to determine which header parsing logic to use

**Lines changed:** ~70 additions/modifications

---

## 4. services/api/errors.ts — Error Messages for Multi-Provider

**Purpose:** Make error messages provider-aware instead of Anthropic-specific.

**Changes:**
- **Added `getCustomOffSwitchMessage()` function**: Returns Anthropic-specific "switch to Sonnet" message for firstParty, generic "try again shortly" for others
- **Made 429 error message provider-aware**: Removed hardcoded "check status.anthropic.com" — only shows for firstParty provider
- **Made x-api-key error check provider-aware**: Only triggers on `firstParty` provider (line 815)
- **Made usage policy URL provider-aware**: Shows `anthropic.com/legal/aup` for firstParty, generic "your provider's acceptable use policy" for others
- **Changed `[ANT-ONLY]` prefix to `[internal]`** for ungated model errors

**Lines changed:** ~25 additions/modifications

---

## 5. entrypoints/cli.tsx — CLI Entrypoint

**Purpose:** Add provider selection at startup, disable Anthropic-internal betas, rebrand.

**Changes:**
- **Added imports** for `providerProfile`, `providerValidation` utilities
- **Disabled experimental API betas by default** (~5 lines): Sets `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS ??= 'true'` — tool search (defer_loading), global cache scope, context management require Anthropic-internal API support
- **Rebranded version output**: `(Claude Code)` → `(Open Claude)`, uses `MACRO.DISPLAY_VERSION`
- **Added `--provider` flag early processing** (~15 lines): Parses `--provider` from args before anything else so env vars are set for profile resolution and startup banner
- **Added credential hydration block** (~15 lines): Calls `enableConfigs()`, `applySafeConfigEnvironmentVariables()`, `hydrateGeminiAccessTokenFromSecureStorage()`, `hydrateGithubModelsTokenFromSecureStorage()`
- **Added `buildStartupEnvFromProfile()`** call — loads saved provider profile env vars
- **Added `validateProviderEnvOrExit()`** — validates provider configuration at startup
- **Removed several Anthropic-internal blocks**: `logPermissionContextForAnts`, settings validation after trust, forced org login check, `showSetupScreens()` logging

**Lines changed:** ~71 additions/modifications

---

## 6. main.tsx — Main Application

**Purpose:** Add `--provider` flag, Ollama model prefetching, MCP doctor command, remove internal logging.

**Changes:**
- **Added import**: `prefetchOllamaModels` from `./utils/model/ollamaModels.js`
- **Added import**: `registerMcpDoctorCommand` from `src/commands/mcp/doctorCommand.js`
- **Removed import**: `logPermissionContextForAnts` from `src/services/internalLogging.js`
- **Removed preAction profiling** block (~7 lines of `profileCheckpoint` calls)
- **Added `--provider` CLI option**: `--provider <provider>` with description listing: anthropic, openai, gemini, github, bedrock, vertex, ollama
- **All `ant-only` comments** → `internal-only` (global rename across file)

**Lines changed:** ~15 substantive + many comment renames

---

## 7. context.ts — System Prompt

**Purpose:** Minor comment rebranding only.

**Changes:**
- `ant-only` → `internal-only` in two comments about cache breaking
- No functional changes

---

## 8. constants/system.ts — System Prompt Strings (Identity)

**Purpose:** Rebrand Claude Code identity to OpenClaude.

**Changes:**
- `DEFAULT_PREFIX`: "You are Claude Code, Anthropic's official CLI for Claude." → "You are OpenClaude, an open-source fork of Claude Code."
- `AGENT_SDK_CLAUDE_CODE_PRESET_PREFIX`: Same pattern
- `AGENT_SDK_PREFIX`: "You are a Claude agent, built on Anthropic's Claude Agent SDK." → "You are a Claude agent running in OpenClaude, built on the Claude Agent SDK."

**Lines changed:** 6

---

## 9. constants/prompts.ts — Prompt Templates

**Purpose:** Rebrand prompts and remove Anthropic-internal Slack recommendation.

**Changes:**
- **Removed Slack integration suggestion** from bug report prompt: cut "offer to post the link to #claude-code-feedback (channel ID C07VBSHV7EV)"
- **Rebranded system prompt**: "You are Claude Code, Anthropic's official CLI" → "You are OpenClaude, an open-source fork of Claude Code"
- **Rebranded agent prompt**: Same pattern in `DEFAULT_AGENT_PROMPT`
- **Comment rename**: `3P default: false — verification agent is ant-only A/B` → `internal-only`

**Lines changed:** ~8

---

## 10. constants/keys.ts — GrowthBook SDK Keys

**Purpose:** Remove hardcoded Anthropic GrowthBook keys, make configurable.

**Changes:**
- **Removed entire conditional logic** (ant vs 3P key selection with dev override)
- **Replaced with**: `return process.env.GROWTHBOOK_CLIENT_KEY ?? ''`
- **Removed imports**: `isEnvTruthy`, `envUtils`

This effectively disables GrowthBook feature flags unless user provides their own key.

**Lines changed:** 10 (mostly deletions)

---

## 11. constants/common.ts — Common Constants

**Purpose:** Trivial comment rename.

**Changes:**
- `ant-only` → `internal-only` in one date override comment

---

## 12. utils/config.ts — Configuration System

**Purpose:** Add provider profiles, flicker-free mode, and additional model caching.

**Changes:**
- **Added `ProviderProfile` type** (~8 lines): `{ id, name, provider, baseUrl, model, apiKey? }`
- **Added config fields** to `GlobalConfig`:
  - `flickerFreeMode?: boolean` — alt-screen + virtualized scroll
  - `openaiAdditionalModelOptionsCache?: ModelOption[]`
  - `providerProfiles?: ProviderProfile[]`
  - `activeProviderProfileId?: string`
  - `openaiAdditionalModelOptionsCacheByProfile?: Record<string, ModelOption[]>`
- **Added defaults**: `providerProfiles: []`, `openaiAdditionalModelOptionsCacheByProfile: {}`
- **Added `flickerFreeMode`** to safe global config keys list
- **Added EACCES/EPERM/EROFS error handling** for global config writes — silently skips instead of crashing on read-only filesystems
- **Comment renames**: `ant-only` → `internal-only` (5 instances)

**Lines changed:** ~40 additions/modifications

---

## 13. bootstrap/state.ts — Application State

**Purpose:** Add REPL bridge stubs for open build.

**Changes:**
- **Added `isReplBridgeActive()`**: stub returning `false`
- **Added `getReplBridgeHandle()`**: stub returning `null`
- Both marked as "not available in open build"
- **Comment renames**: `ant-only` → `internal-only`

**Lines changed:** ~10

---

## 14. utils/model/providers.ts — Provider Detection (CRITICAL)

**Purpose:** Expand `APIProvider` type from 4 to 9 variants.

**Changes:**
- **Expanded `APIProvider` type**: Added `'openai' | 'gemini' | 'github' | 'codex'` to existing `'firstParty' | 'bedrock' | 'vertex' | 'foundry'`
- **Rewrote `getAPIProvider()`**: New priority chain:
  1. `CLAUDE_CODE_USE_GEMINI` → `'gemini'`
  2. `CLAUDE_CODE_USE_GITHUB` → `'github'`
  3. `CLAUDE_CODE_USE_OPENAI` → check if Codex model → `'codex'` or `'openai'`
  4. `CLAUDE_CODE_USE_BEDROCK` → `'bedrock'`
  5. `CLAUDE_CODE_USE_VERTEX` → `'vertex'`
  6. `CLAUDE_CODE_USE_FOUNDRY` → `'foundry'`
  7. Default → `'firstParty'`
- **Added `usesAnthropicAccountFlow()`**: Returns true only for `firstParty`
- **Added `isCodexModel()`**: Checks `OPENAI_MODEL` env var against Codex alias table

**Lines changed:** ~35

---

## 15. utils/model/model.ts — Model Resolution (CRITICAL)

**Purpose:** Return appropriate models per provider instead of always returning Claude models.

**Changes:**
- **`getSmallFastModel()`**: Returns `gemini-2.0-flash-lite` for Gemini, `gpt-4o-mini` for OpenAI, Haiku for firstParty
- **`getUserSpecifiedModelSetting()`**: Reads provider-specific env vars: `GEMINI_MODEL` for gemini, `OPENAI_MODEL` for openai/gemini, `ANTHROPIC_MODEL` for firstParty — prevents cross-provider leaks
- **`getDefaultMainLoopModelSetting()`**: Returns `gemini-2.5-pro-preview-03-25` / `gpt-4o` / `gpt-5.4` / Claude based on provider
- **`getDefaultPlanModeModel()`**: Same pattern
- **`getDefaultHaikuModel()`**: Returns `gemini-2.0-flash-lite` / `gpt-4o-mini` / `gpt-5.4` / Haiku
- **`getModelForPermissionChecks()`**: Returns provider-specific model
- **`getModelDisplayString()`**: Handles `codexplan`/`codexspark` aliases, returns null for OpenAI/Gemini/Codex (shows raw model name)
- **`normalizeModelString()`**: Maps `codexplan` → `gpt-5.4`, `codexspark` → `gpt-5.3-codex-spark`

**Lines changed:** ~100+

---

## 16. utils/model/configs.ts — Model Configuration Maps

**Purpose:** Add OpenAI and Gemini model tier mappings.

**Changes:**
- **Added `OPENAI_MODEL_DEFAULTS`**: opus→gpt-4o, sonnet→gpt-4o-mini, haiku→gpt-4o-mini
- **Added `GEMINI_MODEL_DEFAULTS`**: opus→gemini-2.5-pro, sonnet→gemini-2.0-flash, haiku→gemini-2.0-flash-lite
- **Added `openai` and `gemini` keys** to every model config tier (11 tiers total): classifyBash, verifyEditSlow, verifyEditFast, compact, codeComplete, toolSearch, speculativeQuery, speculativePlan, speculativeReasoning, speculativeMax, subAgentDefault

**Lines changed:** ~50

---

## 17. utils/model/modelOptions.ts — Model Picker Options

**Purpose:** Show provider-appropriate models in /model picker.

**Changes:**
- **Added Codex model option functions**: `getCodexPlanOption()`, `getCodexSparkOption()`, `getCodexModelOptions()` — returns 8 Codex models (gpt-5.4, gpt-5.3-codex, gpt-5.3-codex-spark, gpt-5.2-codex, gpt-5.1-codex-max, gpt-5.1-codex-mini, gpt-5.4-mini, codexspark alias)
- **Added Ollama model support** (~20 lines): When provider is `openai` and `isOllamaProvider()`, shows models from `getCachedOllamaModelOptions()` instead of Claude models
- **Added Codex models to PAYG options**: Appends Codex models for openai/codex providers
- **Changed additional model options source**: Per-profile cache via `getActiveOpenAIModelOptionsCache()` for OpenAI, global cache otherwise
- **Added deduplication logic** (~10 lines): Filters duplicate model options by value to prevent navigation/focus bugs in Select component
- **Changed `[ANT-ONLY]`** → `[internal]` in model descriptions

**Lines changed:** ~100

---

## 18. utils/model/validateModel.ts — Model Validation

**Purpose:** Validate Ollama models against cached model list.

**Changes:**
- **Added Ollama validation path** (~20 lines): When provider is openai and `isOllamaProvider()`, validates against `getCachedOllamaModelOptions()` cache instead of API call. Shows available model names on validation failure.

**Lines changed:** ~20

---

## 19. services/mcp/client.ts — MCP Client

**Purpose:** Mostly formatting/refactoring, plus cleanup helper and rebranding.

**Changes:**
- **Added `cleanupFailedConnection()` exported function** (~10 lines): Properly closes transport and in-process server on connection failure
- **Extracted `InProcessMcpServer` type** for cleaner type annotations
- **Replaced inline cleanup** in catch blocks with `cleanupFailedConnection()`
- **Rebranded**: `title: 'Claude Code'` → `title: 'Open Claude'` (2 instances)
- **Significant whitespace/indentation reformatting** throughout (~200 diff lines that are purely formatting)

**Lines changed:** ~15 substantive + ~200 formatting

---

## 20. tools/AgentTool/runAgent.ts — Sub-Agent Execution

**Purpose:** Route sub-agents to different providers based on settings.

**Changes:**
- **Added imports**: `resolveAgentProvider` from `agentRouting.js`, `getInitialSettings` from settings
- **Added `agentName` parameter** to function signature
- **Added provider resolution** (~7 lines): Calls `resolveAgentProvider(agentName, agentType, settings)` to get per-agent provider override
- **Computes `effectiveModel`**: Uses override model if present, falls back to `resolvedAgentModel`
- **Passes `providerOverride`** to the query options so `createClient()` uses it
- **Uses `effectiveModel`** instead of `resolvedAgentModel` for `mainLoopModel`

**Lines changed:** ~15

---

## Global Pattern: "ant-only" → "internal-only" Rename

Across ~740 modified files, the most common change is renaming the comment marker `ant-only` (referring to Anthropic employees) to `internal-only`. This appears in hundreds of files. It is cosmetic but reveals which features were originally gated to Anthropic employees.

---

## New Files Summary (Key Ones, Not Exhaustive)

These are NEW files, not modifications, listed for context:

| File | Lines | Purpose |
|---|---|---|
| `services/api/openaiShim.ts` | 1152 | Translates Anthropic SDK calls to OpenAI chat completions API format |
| `services/api/codexShim.ts` | 896 | Codex-specific API translation (responses API, not chat completions) |
| `services/api/providerConfig.ts` | 452 | Provider detection, Codex alias resolution, local provider URL detection |
| `services/api/agentRouting.ts` | 75 | Per-agent provider routing from settings (name→model→baseURL+apiKey) |
| `services/api/openaiSchemaSanitizer.ts` | ~100 | Sanitizes tool schemas for OpenAI compatibility |
| `components/ProviderManager.tsx` | ~300 | TUI for managing provider profiles (add/edit/delete/select) |
| `commands/provider/` | ~200 | `/provider` slash command |
| `utils/providerProfile.ts` | ~200 | Provider profile env var application |
| `utils/providerProfiles.ts` | ~200 | CRUD for provider profiles in global config |
| `utils/providerValidation.ts` | ~150 | Validates provider env at startup |
| `utils/providerFlag.ts` | ~100 | Parses `--provider` CLI flag |
| `utils/model/ollamaModels.ts` | ~100 | Ollama model discovery and caching |
| `utils/model/openaiModelDiscovery.ts` | ~100 | Discovers models from OpenAI-compatible endpoints |
| `utils/model/openaiContextWindows.ts` | ~50 | Context window sizes for OpenAI models |
| `utils/geminiAuth.ts` | ~100 | Gemini API key resolution |
| `utils/geminiCredentials.ts` | ~80 | Secure storage for Gemini tokens |
| `utils/githubModelsCredentials.ts` | ~80 | Secure storage for GitHub Models tokens |
| `utils/schemaSanitizer.ts` | ~100 | Generic schema sanitization for non-Anthropic APIs |
| `services/mcp/doctor.ts` | ~200 | MCP diagnostic tool |
| `tools/WorkflowTool/` | new | Workflow automation tool |
| `tools/TungstenTool/` | new | Unknown purpose (possibly code analysis) |
| `tools/VerifyPlanExecutionTool/` | new | Plan execution verification |
| `tools/SuggestBackgroundPRTool/` | new | Background PR suggestion |

---

## Architecture of the Provider System

```
CLI --provider flag
        │
        ▼
  providerFlag.ts → sets env vars (CLAUDE_CODE_USE_OPENAI=1, etc.)
        │
        ▼
  providerProfile.ts → loads saved profile → applies env vars
        │
        ▼
  providerValidation.ts → validates keys exist
        │
        ▼
  providers.ts getAPIProvider() → returns 'openai' | 'gemini' | 'github' | 'codex' | ...
        │
        ▼
  client.ts createClient() ──┬── providerOverride set? → openaiShim → third-party API
                              ├── USE_OPENAI/GITHUB/GEMINI? → openaiShim → third-party API
                              └── else → native Anthropic SDK → Anthropic API
                                         │
  openaiShim.ts ◄──────────────────────────┘
        │
        ├── Translates Anthropic messages.create → OpenAI chat/completions
        ├── Converts tool schemas (Anthropic → OpenAI function calling)
        ├── Streams: converts OpenAI SSE → Anthropic SSE events
        └── For Codex models → delegates to codexShim.ts (responses API)
```

**Per-Agent Routing:**
```
runAgent.ts
    │
    ▼
agentRouting.ts resolveAgentProvider(name, type, settings)
    │
    ├── Looks up settings.agentRouting[name] → model name
    ├── Falls back to settings.agentRouting[subagentType] → model name
    ├── Falls back to settings.agentRouting["default"] → model name
    │
    ▼
    settings.agentModels[modelName] → { base_url, api_key }
    │
    ▼
    providerOverride → passed to createClient() → openaiShim
```

---

## Key Security Decisions

1. **Auth header stripping** in client.ts — prevents Anthropic API keys from leaking to third-party endpoints
2. **Experimental betas disabled by default** — tool search, global cache scope, context management use internal API features that cause 500s on external accounts
3. **GrowthBook keys removed** — no hardcoded Anthropic feature flag keys shipped
4. **Prompt caching disabled for non-Anthropic** — prevents rejected requests from providers that don't understand `cache_control`
5. **Provider-specific error messages** — no "check status.anthropic.com" when using OpenAI

---

*Generated: 2026-04-04 by JARVIS analysis pipeline*
*Sources: diff of full_src/ (Claude Code original) vs openclaude-ref/src/ (OpenClaude fork)*
