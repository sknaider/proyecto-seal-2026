# API Layer Findings — Claude Code v2.1.88

## 1. Request Transport & Authentication

### Client Construction (`client.ts`)
Claude Code creates Anthropic SDK clients with extensive provider support:

- **Default provider**: First-party Anthropic API (ANTHROPIC_API_KEY or OAuth)
- **Alternative providers**:
  - AWS Bedrock (CLAUDE_CODE_USE_BEDROCK): Uses `AnthropicBedrock` SDK; refreshes credentials before each request if available
  - Azure Foundry (CLAUDE_CODE_USE_FOUNDRY): Uses `AnthropicFoundry` SDK; supports both API key and DefaultAzureCredential
  - Vertex AI (CLAUDE_CODE_USE_VERTEX): Uses `AnthropicVertex` SDK; creates fresh GoogleAuth instance per client call
  
### Authentication Strategy
- **OAuth-first for Claude.ai subscribers**: Uses `getClaudeAIOAuthTokens()` → `authToken` parameter
- **API key fallback**: `getAnthropicApiKey()` → `apiKey` parameter
- **Custom headers**: Supports ANTHROPIC_CUSTOM_HEADERS (newline-delimited, parsed on first `:`)
- **Client request ID injection**: Generates UUIDs for x-client-request-id header (first-party API only); survives timeouts for server-side correlation

### Session & Container Headers
- `X-Claude-Code-Session-Id`: Session tracking
- `x-claude-remote-container-id`: Container ID (CCR deployments)
- `x-claude-remote-session-id`: Remote session ID
- `x-client-app`: SDK consumer identification
- `x-anthropic-additional-protection`: Optional extra protection flag

### Timeouts
- **Default API timeout**: 600 seconds (configurable via API_TIMEOUT_MS env var)
- **Files API timeout**: 60s for downloads, 120s for uploads
- **Grove API timeout**: 3s (non-blocking notification feature; skips on timeout)
- **Bootstrap API timeout**: 5s

---

## 2. Retry Logic (`withRetry.ts`)

### Core Parameters
- **DEFAULT_MAX_RETRIES**: 10 (overridable via CLAUDE_CODE_MAX_RETRIES env var)
- **BASE_DELAY_MS**: 500ms initial backoff
- **MAX_RETRY_DELAY**: 32 seconds (before jitter)
- **Jitter formula**: baseDelay + (random 0-0.25 × baseDelay)

### Retry Conditions

#### Retryable Errors
1. **429 (Rate limit)**: Retried per user subscription level
   - Subscriber (Max/Pro): Only if x-should-retry header says true AND user is Enterprise
   - Non-subscriber: Always retry
   - Persistent mode (unattended): Always retry indefinitely
   
2. **529 (Overloaded)**: Context-dependent
   - **Foreground sources** (repl_main_thread, sdk, agent:*, hook_*, verification, auto_mode): Retried up to MAX_529_RETRIES=3
   - **Background sources** (speculation, session_memory, etc.): Dropped immediately (no amplification during cascades)
   - Consecutive 529s > 3 → model fallback (Opus to Sonnet) if fallbackModel specified

3. **408 (Request timeout)**: Always retried
4. **409 (Lock timeout)**: Always retried  
5. **5xx (Server errors)**: Always retried; Ants can override x-should-retry:false for 5xx only
6. **Connection errors** (APIConnectionError, ECONNRESET, EPIPE): Retried; stale connections trigger keep-alive disabling
7. **Auth errors** (401, 403): Retried with token refresh; 403 "OAuth token revoked" triggers OAuth refresh

#### Non-Retryable Errors
- 400 (Bad request) — except context overflow, prompt too long, media size errors
- 404 (Not found)
- 413 (Payload too large) — except with context overflow handling
- Invalid API key errors (returns immediately)
- Mock rate limit errors (testing only)

### Persistent Retry Mode (Unattended Sessions)

**Activation**: CLAUDE_CODE_UNATTENDED_RETRY env var + feature gate
- **Max backoff**: 5 minutes
- **Reset cap**: 6 hours (window-based limits wait until explicit reset timestamp)
- **Heartbeat interval**: 30 seconds (yields SystemAPIErrorMessage to prevent idle timeout)
- **Behavior**: Retries indefinitely; attempt counter grows unbounded but clamped at maxRetries for loop control

### Fast Mode & Overload Fallback

**Fast mode (claude-api speed)**:
- Short retry-after (< 20s): Waits and retries at fast speed (preserves prompt cache)
- Long retry-after (≥ 20s) or unknown: Triggers cooldown (20min default hold), switches to normal speed
- Overload reason header (`anthropic-ratelimit-unified-overage-disabled-reason`): Permanently disables fast mode
- Fast mode not enabled error (400, "Fast mode is not enabled"): Disables fast mode, retries at normal speed

### Max Tokens Context Overflow

When input + max_tokens exceeds limit:
- **Detection**: 400 error, message matches "input length and `max_tokens` exceed context limit"
- **Parsing**: Regex extracts inputTokens, maxTokens, contextLimit
- **Adjustment formula**: `max(FLOOR_OUTPUT_TOKENS, availableContext, minRequired)` where:
  - FLOOR_OUTPUT_TOKENS = 3000
  - minRequired = thinking budget + 1 token
  - availableContext = contextLimit - inputTokens - 1000 (safety buffer)
- **Retry**: Same request, next attempt uses adjusted max_tokens

---

## 3. Error Handling & Classification (`errors.ts`, `errorUtils.ts`)

### Connection Error Extraction
- Walks error.cause chain (max 5 hops) to find root cause code
- Maps 25+ SSL/TLS codes (CERT_HAS_EXPIRED, DEPTH_ZERO_SELF_SIGNED_CERT, etc.)
- Returns: `{ code, message, isSSLError }`
- **SSL hint for enterprises**: Suggests NODE_EXTRA_CA_CERTS for corporate proxies

### API Error Sanitization
- Strips HTML from error messages (CloudFlare/reverse proxy error pages)
- Extracts title tags from HTML responses
- Handles nested error formats from Bedrock/Vertex after JSON round-tripping

### Error Classification

**Rate limit errors** (429):
- **New unified headers** (v1 2025+):
  - `anthropic-ratelimit-unified-representative-claim`: five_hour | seven_day | seven_day_opus | seven_day_sonnet
  - `anthropic-ratelimit-unified-overage-status`: allowed | allowed_warning | rejected
  - `anthropic-ratelimit-unified-reset`: Unix timestamp (seconds)
  - `anthropic-ratelimit-unified-overage-reset`: Unix timestamp for overage reset
  - `anthropic-ratelimit-unified-overage-disabled-reason`: reason code
  
- **Legacy headers** (still handled):
  - five_hour, seven_day, seven_day_opus rate limits
  - Fallback to generic "Request rejected (429)" if no quota headers

**Specific errors with recovery**:
- **Prompt too long (400)**: User message, raw error in errorDetails; reactive compact parses token gap via regex
- **PDF too large**: Max pages + target size limits; suggests pdftotext conversion
- **Image exceeds size**: 400, message contains "image exceeds" + "maximum"
- **Many-image dimension limit**: 400, "image dimensions exceed" + "many-image" (2000px stricter limit)
- **Request too large (413)**: Typically large PDF + context > 32MB
- **Tool use/result concurrency (400)**: Missing tool_result blocks after tool_use; logs sequence mismatch to Statsig
- **Invalid model name (400)**: Opus-unavailable message for Pro users; custom org gating message for Ants
- **Credit balance too low**: Billing error

**Auth-specific**:
- **Organization disabled**: Detects env var ANTHROPIC_API_KEY as culprit vs actual OAuth
- **Invalid/expired OAuth**: TOKEN_REVOKED_ERROR_MESSAGE guides to /login
- **Enterprise org not allowed**: Suggests admin contact

---

## 4. Token & Cost Tracking (`logging.ts`)

### Usage Metrics Logged per Request
- **Input tokens**: `usage.input_tokens`
- **Output tokens**: `usage.output_tokens`
- **Cached read tokens**: `usage.cache_read_input_tokens` (prompt cache hits)
- **Cached creation tokens**: `usage.cache_creation_input_tokens` (new cache entries)
- **Cache deleted tokens**: `cache_deleted_input_tokens` (cached microcompact edits; Ant-only feature gate)

### Query Lifecycle Logging

**Phase 1 - Pre-request** (logAPIQuery):
- Model, message count, temperature
- Beta headers, permission mode, effort level
- Query source (repl_main_thread, sdk, agent:*, etc.)
- Query chain depth (for nested agent calls)
- Thinking type (adaptive/enabled/disabled)
- Fast mode enabled flag
- Previous request ID (for correlation chains)
- Build age in minutes (MACRO.BUILD_TIME)

**Phase 2a - Successful response** (logAPISuccessAndDuration):
- All Phase 1 data plus:
- Durations: durationMs (request only), durationMsIncludingRetries (with backoff)
- TTFT (time to first token) in ms or null
- Stop reason (end_turn, max_tokens, tool_use, model_context_window_exceeded, etc.)
- Cost in USD (calculated from token counts)
- Gateway detection (litellm, helicone, portkey, cloudflare-ai-gateway, kong, braintrust, databricks)
- Content analysis: text length, thinking length, tool use input lengths by name, connector text block count
- Fallback indicator: did request fall back from streaming to non-streaming?
- Session context: non-interactive, post-compaction, print flag, isTTY

**Phase 2b - Error response** (logAPIError):
- Error message, error type classification
- Status code
- Client request ID (survives timeouts)
- Server request ID (if available)
- Attempt number (pre-retry and with retry counts)
- Teleported session tracking (first error/success in CCR)

### Gateway Detection
Fingerprints response headers or baseURL to identify proxies:
- Header prefixes: x-litellm-, helicone-, x-portkey-, cf-aig-, x-kong-, x-bt-
- Host suffixes: .cloud.databricks.com, .azuredatabricks.net, .gcp.databricks.com

### Analytics Events Emitted
- `tengu_api_query`: Pre-request snapshot
- `tengu_api_success`: Post-request success (every token counted, cached, cost tracked)
- `tengu_api_error`: Post-request error
- `tengu_api_retry`: Each retry attempt (delay, status, error message)
- `tengu_api_persistent_retry_wait`: Unattended retries > 60s
- `tengu_api_opus_fallback_triggered`: 529-triggered model fallback
- `tengu_api_custom_529_overloaded_error`: External users hit 3+ consecutive 529s
- `tengu_api_429_background_dropped`: Non-foreground source dropped on 529 (no retry amplification)
- `tengu_max_tokens_context_overflow_adjustment`: Context overflow recovery attempt
- `tengu_tool_use_tool_result_mismatch_error`: Tool concurrency bugs
- `tengu_teleport_first_message_success/error`: CCR session reliability

---

## 5. Prompt Cache Optimization (`promptCacheBreakDetection.ts`)

### Pre-call State Capture (Phase 1)
Records hashes + per-tool schemas for:
- System prompt (with/without cache_control)
- Tool schemas
- Model, fast mode, global cache strategy
- Beta headers
- Auto mode, overage state, cached microcompact state
- Effort override
- Extra body params

**Tracking key**: Query source prefix (repl_main_thread, sdk, agent:*, etc.) + agentId for subagents
**Max tracked sources**: 10 (LRU eviction)

### Post-call Break Detection (Phase 2)
Triggered when cache_read_tokens drop > 5% AND absolute drop > 2000 tokens

**Root cause attribution**:
1. **Client-side changes detected**:
   - System prompt changed (char delta logged)
   - Tool schemas changed (added/removed/schema-modified)
   - Model switched
   - Fast mode toggled
   - Cache control scope/TTL flipped (global↔org, 1h↔5m)
   - Beta headers added/removed
   - Auto mode toggled
   - Overage state changed (TTL latched, not cache-breaking in theory)
   - Cached microcompact toggled
   - Effort changed
   - Extra body params changed

2. **TTL-based (no client changes)**:
   - Last assistant message > 1h ago → "possible 1h TTL expiry"
   - Last assistant message > 5m ago → "possible 5min TTL expiry"
   - Else → "likely server-side (prompt unchanged, <5min gap)"

3. **Excluded models**: Haiku (different caching behavior)

### Cache Deletion Handling
When cached microcompact sends cache_edits deletions:
- Marked via `notifyCacheDeletion()`
- Expected drop in cache read tokens on next call
- Baseline reset; no false-positive break detection

### Analytics Event
`tengu_prompt_cache_break`: Detailed attribute breakdown of what changed, token deltas, TTL analysis, diff file path

---

## 6. Session Persistence (`sessionIngress.ts`)

### Optimistic Concurrency Control
Uses Last-Uuid header to detect concurrent writers (race conditions from killed processes)

**Retry logic**:
1. **200/201**: Success, store entry UUID
2. **409 (Conflict)**:
   - Check if entry.uuid == server's x-last-uuid header: Already stored, recover from stale state
   - Else: Adopt server's last UUID from header and retry
   - Fallback: Re-fetch session, find last UUID, retry
3. **401**: Non-retryable (token expired)
4. **4xx/5xx**: Retryable with exponential backoff

**Sequential queueing**: Per-session wrapper enforces one-at-a-time appends (prevents thundering herd)

### New Teleport API (CCR v2)
Replaces session-ingress pagination:
- **Endpoint**: `/v1/code/sessions/{id}/teleport-events`
- **Pagination**: Cursor-based (loop until next_cursor unset)
- **Per-page default**: 500, max 1000
- **Page cap**: 100 (100k event limit to prevent hangs)
- **Payload shape**: Entry (TranscriptMessage struct) stored opaque in payload field
- **Null payloads**: Skipped (threadstore non-generic events, encryption failures)

---

## 7. Files API (`filesApi.ts`)

### Authentication
- OAuth Bearer token + two beta headers: files-api-2025-04-14, oauth-2025-04-20
- Base URL: ANTHROPIC_BASE_URL or CLAUDE_CODE_API_BASE_URL or default https://api.anthropic.com

### Download Flow
- **Retry**: 3 attempts, exponential backoff (500ms × 2^(attempt-1))
- **Timeout**: 60s per file
- **Non-retriable codes**: 401 (auth), 403 (forbidden), 404 (not found)
- **Retriable**: Network errors, 5xx
- **Concurrency**: 5 parallel downloads default
- **Path validation**: Rejects .. traversal

### Upload Flow (BYOC mode)
- **Retry**: 3 attempts same backoff
- **Timeout**: 120s (2min)
- **Multipart form-data**: file + purpose="user_data" fields
- **Max file size**: 500MB
- **Non-retriable codes**: 401, 403, 413 (payload too large)
- **Non-retriable exceptions**: Abort signal, cancel operations
- **Analytics**: tengu_file_upload_failed events (error_type: file_read, file_too_large, auth, forbidden, size, network)

### List Files (1P/Cloud mode)
- **Endpoint**: GET /v1/files with after_created_at + optional after_id cursor
- **Pagination**: Loops until has_more false (last file's ID as next cursor)
- **Returns**: FileMetadata array (filename, fileId, size)

---

## 8. Grove Privacy Notifications (`grove.ts`)

### Dual-mode Fetch Strategy
**Non-blocking cache-first**:
- Cold start (no cache): Returns false, fetches in background
- Stale cache (> 24h): Returns cached value, refreshes background
- Fresh cache (< 24h): Returns immediately

### Notification Logic
- **User hasn't chosen**: Show dialog
- **Grace period active**: Show informational message + continue
- **Grace period ended**: Show error message + exit(1) (for non-interactive sessions)
- **Reminder frequency**: Show dialog if days since viewed ≥ reminder_frequency

### API Calls
1. `getGroveSettings()`: GET /api/oauth/account/settings
   - Cached per session (memoized); cleared on toggle
   
2. `getGroveNoticeConfig()`: GET /api/claude_code_grove
   - Memoized; returns grove_enabled, domain_excluded, notice_is_grace_period, notice_reminder_frequency
   
3. `updateGroveSettings(groveEnabled)`: PATCH /api/oauth/account/settings
   - Invalidates cache after update
   
4. `markGroveNoticeViewed()`: POST /api/oauth/account/grove_notice_viewed
   - No body

---

## 9. Bootstrap & Configuration (`bootstrap.ts`)

### Fetch Strategy
- **Endpoint**: /api/claude_cli/bootstrap
- **Auth**: OAuth (requires user:profile scope) or API key fallback
- **Retry**: withOAuth401Retry (refreshes token on 401, retries)
- **Timeout**: 5s
- **Condition**: Only first-party API, essential traffic only, oauth or api key available

### Response Schema
```typescript
{
  client_data?: Record<string, unknown> | null,
  additional_model_options?: [
    {
      value: string,      // model ID
      label: string,      // display name
      description: string // help text
    }
  ] | null
}
```

### Cache
- **Key**: isEqual() comparison (only persist if changed)
- **Stored in**: globalConfig.clientDataCache, additionalModelOptionsCache
- **Validation**: Zod schema with transform

---

## 10. Usage & Limits (`usage.ts`)

### Utilization Endpoint
**GET** `/api/oauth/usage` (Claude.ai OAuth only)

**Response shape**:
```typescript
{
  five_hour?: { utilization: 0-100, resets_at: ISO8601 } | null,
  seven_day?: { ... },
  seven_day_oauth_apps?: { ... },
  seven_day_opus?: { ... },
  seven_day_sonnet?: { ... },
  extra_usage?: {
    is_enabled: boolean,
    monthly_limit: number | null,
    used_credits: number | null,
    utilization: 0-100 | null
  } | null
}
```

**Guard**: Returns {} if not Claude.ai subscriber or missing profile scope
**Stale token protection**: Checks isOAuthTokenExpired before API call (avoids 401 errors)
**Timeout**: 5s

---

## 11. Non-Obvious Behaviors & Gotchas

### 1. Recursive 529 Handling
- Streaming 429/529 error can be pre-seeded via initialConsecutive529Errors parameter
- This syncs the 529 count between streaming and non-streaming fallback paths
- Ensures total 529 attempts before fallback is consistent

### 2. OAuth Token Revocation
- 403 with message "OAuth token has been revoked" → triggers handleOAuth401Error (same flow as 401)
- Stale connection errors (ECONNRESET, EPIPE) → disables keep-alive pooling for retry

### 3. Bedrock Region Overrides
- ANTHROPIC_SMALL_FAST_MODEL_AWS_REGION bypasses AWS_REGION just for Haiku

### 4. Vertex Project ID Fallback
- Avoids 12-second metadata server timeout by:
  1. Checking env vars (GCLOUD_PROJECT, GOOGLE_CLOUD_PROJECT, etc.)
  2. Checking credential file for project_id
  3. Only if both missing: uses ANTHROPIC_VERTEX_PROJECT_ID
- Risk: If auth project ≠ API target project, could cause billing/audit issues

### 5. Email-like Custom Headers
- ANTHROPIC_CUSTOM_HEADERS can be multiline (split on \n or \r\n)
- Parsed on first `:` only (avoids regex backtracking on malformed headers)

### 6. Tool Use Concurrency Bugs
- API requires tool_result blocks immediately after tool_use
- Mismatch → 400, logs tool_use ID sequence pre/post normalization to Statsig

### 7. Model Fallback Gate
- FALLBACK_FOR_ALL_PRIMARY_MODELS env var forces fallback on any model's 3+ 529s
- Otherwise: only non-custom Opus models (Claude Code's historical default)

### 8. Fast Mode Cache Preservation
- Short retry-after: Retries at same speed (preserves model name for cache key)
- Long retry-after: Cooldown to standard speed (switches model, breaks cache intentionally to avoid thrashing)

### 9. Cache Break Detection Excludes Haiku
- Haiku has different caching behavior; excluded from cache break analysis

### 10. Content Overflow > Context Limits
- Client-side adjustment: doesn't increase context limit, reduces max_tokens to fit
- Safety buffer: 1000 tokens reserved between inputTokens and contextLimit

### 11. Teleport Pagination Edge Cases
- 404 on page 0: Ambiguous (doesn't exist vs session-ingress not deployed); returns null to fall back
- 404 mid-pagination: Returns partial data (better than nothing)
- Page cap hit: Logs warning but returns data collected so far

### 12. Files API Beta Headers Required
- Both `files-api-2025-04-14` and `oauth-2025-04-20` needed for Bearer OAuth
- Without oauth-2025-04-20 header: Gets 404 on public-api routes

### 13. Session Ingress UUID Chain
- Each entry has a UUID; optimistic concurrency enforces unique append order
- Concurrent writers detected via 409; server's x-last-uuid header allows adoption
- If header missing, session must be re-fetched to discover current chain

---

## 12. Monitoring & Observability for 24/7 Agents

### Critical Thresholds to Monitor
1. **Retry exhaustion**: tengu_api_retry events with attempt = maxRetries + 1
2. **Consecutive 529s**: tengu_api_opus_fallback_triggered (indicates cascade impact)
3. **Auth failures**: 401/403 events without immediate recovery
4. **Persistent retry waits**: tengu_api_persistent_retry_wait with delayMs > 60_000 (indicate queue backlog)
5. **Teleport pagination caps**: teleport_events_page_cap (indicates session truncation)
6. **Session persistence 409s**: session_persist_409_adopt_server_uuid (concurrent writer detected)

### Cache Efficiency
- Log tengu_prompt_cache_break ratio (breaks per 1000 requests)
- Trend cache read vs creation tokens; anomalies suggest stale TTLs or unstable configs
- Fast mode fallback frequency: should be rare; high frequency = service overload

### Token Cost Tracking
- Sum usage.input_tokens, output_tokens, cache_read_input_tokens per agent per day
- Flag cache_creation_input_tokens spikes (cache not warming up)
- Monitor cost_usd per query source; outliers indicate context bloat

### Auth Resilience
- Track OAuth token refresh frequency (should cluster at expiry, not mid-stream)
- Measure time-to-recovery for 401 → refresh → retry cycles
- Flag CCR auth errors in production (different message path than 1P failures)

### Network Quality
- Monitor connection error codes (ECONNRESET, ETIMEDOUT, SSL_* codes)
- Flag corporate proxy patterns (SSL_* + UNABLE_TO_VERIFY_LEAF_SIGNATURE)
- Track TTFT (time to first token) for streaming; > 3s = degradation

