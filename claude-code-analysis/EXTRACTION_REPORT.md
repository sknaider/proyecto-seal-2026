# Source Map Extraction Report
**Date:** 2026-03-31  
**Source:** `/tmp/claude-code-2.1.88/package/cli.js.map`  
**Output:** `/home/dadito/IA/proyecto-seal/claude-code-analysis/`

## Summary
- **Total sources in map:** 4,756
- **Matching sources extracted:** 53
- **Total size extracted:** 556,052 bytes (~543 KB)

## Extraction Details

### API Services (20 files, 359,444 bytes)
Key API integration layer for Claude Code backend services.

| File | Size |
|------|------|
| claude.ts | 125,720 bytes |
| errors.ts | 41,707 bytes |
| withRetry.ts | 28,217 bytes |
| promptCacheBreakDetection.ts | 26,230 bytes |
| logging.ts | 24,183 bytes |
| filesApi.ts | 21,492 bytes |
| sessionIngress.ts | 17,030 bytes |
| client.ts | 16,160 bytes |
| grove.ts | 11,539 bytes |
| errorUtils.ts | 8,401 bytes |
| dumpPrompts.ts | 7,328 bytes |
| referral.ts | 7,982 bytes |
| metricsOptOut.ts | 5,337 bytes |
| overageCreditGrant.ts | 4,907 bytes |
| bootstrap.ts | 4,628 bytes |
| adminRequests.ts | 3,208 bytes |
| firstTokenDate.ts | 1,765 bytes |
| usage.ts | 1,685 bytes |
| ultrareviewQuota.ts | 1,219 bytes |
| emptyUsage.ts | 706 bytes |

**Purpose:** API client implementations, error handling, retry logic, prompt caching, file operations, session management, logging, and usage tracking.

### Model Configuration (16 files, 90,212 bytes)
Model selection, capability management, and provider configuration.

| File | Size |
|------|------|
| model.ts | 21,391 bytes |
| modelOptions.ts | 18,319 bytes |
| modelAllowlist.ts | 6,016 bytes |
| bedrock.ts | 9,180 bytes |
| modelStrings.ts | 5,227 bytes |
| validateModel.ts | 4,631 bytes |
| modelCapabilities.ts | 4,097 bytes |
| configs.ts | 4,280 bytes |
| agent.ts | 5,574 bytes |
| antModels.ts | 1,798 bytes |
| deprecation.ts | 2,532 bytes |
| modelSupportOverrides.ts | 1,537 bytes |
| providers.ts | 1,340 bytes |
| check1mAccess.ts | 2,213 bytes |
| contextWindowUpgradeCheck.ts | 1,284 bytes |
| aliases.ts | 793 bytes |

**Purpose:** Model provider integrations (Anthropic, Bedrock, etc.), capability definitions, deprecation handling, and model validation.

### Bundled Skills (17 files, 106,396 bytes)
Built-in skill plugins for Claude Code functionality.

| File | Size |
|------|------|
| scheduleRemoteAgents.ts | 18,968 bytes |
| updateConfig.ts | 17,411 bytes |
| keybindings.ts | 10,394 bytes |
| skillify.ts | 9,473 bytes |
| batch.ts | 7,135 bytes |
| claudeApi.ts | 6,299 bytes |
| claudeApiContent.ts | 4,278 bytes |
| loremIpsum.ts | 4,400 bytes |
| stuck.ts | 4,224 bytes |
| debug.ts | 4,215 bytes |
| remember.ts | 4,205 bytes |
| loop.ts | 4,627 bytes |
| index.ts | 3,250 bytes |
| claudeInChrome.ts | 1,760 bytes |
| verify.ts | 893 bytes |
| verifyContent.ts | 414 bytes |

**Purpose:** Extensible skill system for batch processing, remote agent scheduling, keybinding configuration, API interactions, and utility functions.

## Directory Structure
```
/home/dadito/IA/proyecto-seal/claude-code-analysis/
├── api/              (20 TypeScript files)
│   ├── claude.ts     (primary API handler)
│   ├── errors.ts     (error definitions)
│   ├── withRetry.ts  (retry logic)
│   └── ... 17 more
├── model/            (16 TypeScript files)
│   ├── model.ts      (core model selection)
│   ├── modelOptions.ts
│   └── ... 14 more
├── skills/           (17 TypeScript files)
│   ├── scheduleRemoteAgents.ts
│   ├── updateConfig.ts
│   └── ... 15 more
└── EXTRACTION_REPORT.md (this file)
```

## Key Modules for Analysis

### Primary (>20KB each)
- **claude.ts** — Main API orchestration (125.7 KB)
- **errors.ts** — Comprehensive error taxonomy (41.7 KB)
- **withRetry.ts** — Exponential backoff & retry strategies (28.2 KB)
- **promptCacheBreakDetection.ts** — Cache invalidation logic (26.2 KB)
- **logging.ts** — Observability & telemetry (24.2 KB)

### Critical Infrastructure
- **model.ts** — Model routing & selection engine
- **client.ts** — HTTP client & request handling
- **sessionIngress.ts** — Session initialization & ingress
- **filesApi.ts** — File upload/download operations

### Extensibility
- **scheduleRemoteAgents.ts** — Cron-based remote execution
- **updateConfig.ts** — Configuration management
- **skillify.ts** — Skill registration & lifecycle

## Analysis Recommendations
1. **API Layer:** Analyze `claude.ts` + `errors.ts` for request/response flow
2. **Resilience:** Study `withRetry.ts` + `promptCacheBreakDetection.ts` for reliability patterns
3. **Model Selection:** Review `model.ts` + `modelOptions.ts` for provider routing logic
4. **Skills System:** Examine skill index + bundled implementations for plugin architecture
5. **Observability:** Check `logging.ts` for instrumentation strategy

## Verification
All 53 files successfully extracted with source map sources:
- Original source paths preserved in naming
- Full TypeScript source code (not transpiled)
- Ready for static analysis, code review, or architectural documentation

