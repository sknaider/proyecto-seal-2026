# Provider System — Architecture Design (Phase 1.2)
> Designed by JARVIS | 2026-04-05
> For implementation by ADA
> Based on: OpenClaude provider patterns (OPENCLAUDE_DELTA) + SEAL requirements

---

## Goal

A unified AI provider interface that abstracts multiple backends (Anthropic, Ollama, vLLM, OpenAI-compatible) with streaming, retry, and automatic fallback. Every AI feature in the IDE (ghost text, inline chat, agent mode, chat) goes through this single system.

---

## Files to Create

| File | Layer | Purpose |
|------|-------|---------|
| `src/main/services/provider.js` | Main process | ProviderRegistry + router + fallback |
| `src/main/services/providers/anthropic.js` | Main process | Anthropic Claude API provider |
| `src/main/services/providers/ollama.js` | Main process | Ollama local provider |
| `src/main/services/providers/vllm.js` | Main process | vLLM / OpenAI-compatible provider |
| `src/main/ipc/ai.js` | Main process | IPC handlers for renderer |
| `src/preload/ai-bridge.js` | Preload | contextBridge API surface |
| `src/renderer/hooks/useAIProvider.js` | Renderer | React hook for provider access |

---

## Core Types

```js
/**
 * @typedef {Object} Provider
 * @property {string} id - Unique identifier (e.g., 'anthropic', 'ollama', 'vllm')
 * @property {string} name - Display name
 * @property {() => Promise<boolean>} isAvailable - Health check
 * @property {() => Promise<Model[]>} listModels - Available models
 * @property {(request: CompletionRequest) => AsyncGenerator<StreamChunk>} complete - Streaming completion
 * @property {(text: string) => number} estimateTokens - Token count estimate
 */

/**
 * @typedef {Object} Model
 * @property {string} id - Model identifier (e.g., 'claude-opus-4-6', 'qwen2.5-coder:7b')
 * @property {string} provider - Parent provider id
 * @property {string} name - Display name
 * @property {number} contextWindow - Max context tokens
 * @property {boolean} supportsFIM - Fill-in-the-Middle support (for ghost text)
 * @property {boolean} supportsStreaming - Streaming support
 * @property {string} [fimFormat] - FIM token format: 'qwen' | 'deepseek' | 'starcoder'
 */

/**
 * @typedef {Object} CompletionRequest
 * @property {string} model - Model id to use
 * @property {Message[]} messages - Conversation messages
 * @property {number} [maxTokens=4096] - Max output tokens
 * @property {number} [temperature=0.7] - Temperature
 * @property {string} [stop] - Stop sequences
 * @property {boolean} [stream=true] - Enable streaming
 * @property {'chat' | 'fim'} [mode='chat'] - Completion mode
 * @property {string} [prefix] - FIM prefix (mode='fim')
 * @property {string} [suffix] - FIM suffix (mode='fim')
 */

/**
 * @typedef {Object} StreamChunk
 * @property {'text' | 'error' | 'done' | 'usage'} type
 * @property {string} [text] - Text delta
 * @property {string} [error] - Error message
 * @property {UsageInfo} [usage] - Token usage (on 'done')
 */
```

---

## ProviderRegistry (src/main/services/provider.js)

```js
class ProviderRegistry {
  constructor() {
    this.providers = new Map();      // id -> Provider
    this.defaultProvider = null;      // string id
    this.fallbackChain = [];          // ordered provider ids
    this.modelOverrides = new Map();  // feature -> { provider, model }
  }

  register(provider) { ... }
  unregister(id) { ... }

  // Get best provider for a request
  resolve(request) {
    // 1. Check modelOverrides for this feature (e.g., 'ghost-text' -> ollama/qwen2.5-coder)
    // 2. Check if requested model exists in any provider
    // 3. Fall back to defaultProvider
    // 4. Walk fallbackChain if default unavailable
  }

  // Route a completion through the right provider
  async *complete(request) {
    const provider = await this.resolve(request);
    yield* provider.complete(request);
  }

  // Health check all providers
  async healthCheck() {
    const results = {};
    for (const [id, provider] of this.providers) {
      results[id] = await provider.isAvailable().catch(() => false);
    }
    return results;
  }
}
```

### Feature-based Model Routing

Different features need different models:

| Feature | Preferred Model | Reason |
|---------|----------------|--------|
| Ghost Text | `ollama/qwen2.5-coder:7b` | Local, fast (<200ms), FIM support |
| Inline Chat | `anthropic/claude-sonnet-4-6` | Quality reasoning |
| Agent Mode | `anthropic/claude-opus-4-6` | Complex multi-step |
| Chat | `anthropic/claude-sonnet-4-6` | Balance quality/speed |
| Commit Message | `ollama/qwen2.5-coder:7b` | Local, fast, code-aware |

Configurable via settings cascade (Phase 1.3) — user can override any mapping.

---

## Provider Implementations

### AnthropicProvider

```js
// Uses @anthropic-ai/sdk (already in William's stack)
// Streaming via SDK's stream() method
// Supports: chat, tool_use, vision
// Does NOT support: FIM

class AnthropicProvider {
  constructor(apiKey) { ... }

  async *complete(request) {
    const stream = await this.client.messages.stream({
      model: request.model,
      messages: request.messages,
      max_tokens: request.maxTokens,
      temperature: request.temperature,
    });

    for await (const event of stream) {
      if (event.type === 'content_block_delta') {
        yield { type: 'text', text: event.delta.text };
      }
    }
    yield { type: 'done', usage: stream.finalMessage().usage };
  }
}
```

### OllamaProvider

```js
// Uses fetch to localhost:11434/api/generate (or /api/chat)
// Streaming via NDJSON response
// Supports: chat, FIM (model-dependent)
// FIM format varies by model:
//   qwen2.5-coder: <|fim_prefix|> ... <|fim_suffix|> ... <|fim_middle|>
//   deepseek-coder: <｜fim▁begin｜> ... <｜fim▁hole｜> ... <｜fim▁end｜>
//   starcoder: <fim_prefix> ... <fim_suffix> ... <fim_middle>

class OllamaProvider {
  constructor(baseUrl = 'http://localhost:11434') { ... }

  async *complete(request) {
    if (request.mode === 'fim') {
      yield* this._completeFIM(request);
      return;
    }

    const response = await fetch(`${this.baseUrl}/api/chat`, {
      method: 'POST',
      body: JSON.stringify({
        model: request.model.replace('ollama/', ''),
        messages: request.messages,
        stream: true,
        options: { temperature: request.temperature },
      }),
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const lines = decoder.decode(value).split('\n').filter(Boolean);
      for (const line of lines) {
        const data = JSON.parse(line);
        if (data.message?.content) {
          yield { type: 'text', text: data.message.content };
        }
        if (data.done) {
          yield { type: 'done', usage: { prompt_tokens: data.prompt_eval_count, completion_tokens: data.eval_count } };
        }
      }
    }
  }

  async *_completeFIM(request) {
    const fimPrompt = this._buildFIMPrompt(request);
    const response = await fetch(`${this.baseUrl}/api/generate`, {
      method: 'POST',
      body: JSON.stringify({
        model: request.model.replace('ollama/', ''),
        prompt: fimPrompt,
        stream: true,
        options: {
          temperature: request.temperature ?? 0.2,  // Lower for completions
          stop: ['\n\n', '<|endoftext|>'],           // Stop at blank line or EOT
          num_predict: 128,                          // Short completions
        },
      }),
    });
    // ... same NDJSON parsing
  }
}
```

### VLLMProvider

```js
// Uses OpenAI-compatible API at localhost:8000
// Streaming via SSE (text/event-stream)
// Supports: chat, FIM (model-dependent), tool_use

class VLLMProvider {
  constructor(baseUrl = 'http://localhost:8000') { ... }
  // Identical to OpenAI SDK streaming pattern
  // response.body is SSE stream: "data: {json}\n\n"
}
```

---

## IPC Layer (src/main/ipc/ai.js)

```js
// Main process IPC handlers
ipcMain.handle('ai:complete', async (event, request) => {
  // Returns a port for streaming (MessagePort pattern)
  const { port1, port2 } = new MessageChannelMain();
  event.sender.postMessage('ai:stream-port', null, [port1]);

  // Stream in background
  (async () => {
    try {
      for await (const chunk of registry.complete(request)) {
        port2.postMessage(chunk);
      }
      port2.postMessage({ type: 'done' });
    } catch (err) {
      port2.postMessage({ type: 'error', error: err.message });
    } finally {
      port2.close();
    }
  })();

  return { streaming: true };
});

ipcMain.handle('ai:models', async () => {
  const health = await registry.healthCheck();
  const models = [];
  for (const [id, provider] of registry.providers) {
    if (health[id]) {
      models.push(...(await provider.listModels()));
    }
  }
  return { models, health };
});

ipcMain.handle('ai:cancel', (event, requestId) => {
  // AbortController pattern for cancellation
});
```

### Why MessagePort instead of ipcRenderer.on?

1. Each stream gets its own channel — no multiplexing needed
2. Automatic cleanup when port closes
3. Backpressure via port buffer
4. Matches OpenClaude's bridge pattern (SPEC_11)

---

## Renderer Hook (src/renderer/hooks/useAIProvider.js)

```js
export function useAIProvider() {
  const [models, setModels] = useState([]);
  const [health, setHealth] = useState({});

  useEffect(() => {
    window.sealAI.listModels().then(({ models, health }) => {
      setModels(models);
      setHealth(health);
    });
  }, []);

  const complete = useCallback(async function* (request) {
    // Returns AsyncGenerator that yields StreamChunks
    const stream = await window.sealAI.complete(request);
    yield* stream;
  }, []);

  return { models, health, complete };
}
```

---

## Fallback & Retry

```
Fallback chain (configurable):
  1. Try requested provider
  2. If unavailable → try next in fallbackChain
  3. If all down → return clear error to user

Retry policy (per provider):
  - Max 2 retries
  - Exponential backoff: 1s, 3s
  - Retry on: network error, 429 (rate limit), 500-503
  - No retry on: 400, 401, 403
  - Timeout: 30s for chat, 5s for FIM completions
```

---

## Initialization

```js
// In main.js, after app.ready:
const { ProviderRegistry } = require('./services/provider');
const { AnthropicProvider } = require('./services/providers/anthropic');
const { OllamaProvider } = require('./services/providers/ollama');
const { VLLMProvider } = require('./services/providers/vllm');

const registry = new ProviderRegistry();

// Register available providers
if (process.env.ANTHROPIC_API_KEY) {
  registry.register(new AnthropicProvider(process.env.ANTHROPIC_API_KEY));
}
registry.register(new OllamaProvider());  // Always available locally
if (process.env.VLLM_URL) {
  registry.register(new VLLMProvider(process.env.VLLM_URL));
}

// Default: Anthropic for quality, Ollama for speed
registry.defaultProvider = 'anthropic';
registry.fallbackChain = ['anthropic', 'ollama', 'vllm'];

// Feature overrides
registry.modelOverrides.set('ghost-text', { provider: 'ollama', model: 'qwen2.5-coder:7b' });
registry.modelOverrides.set('commit-msg', { provider: 'ollama', model: 'qwen2.5-coder:7b' });
```

---

## Key Design Decisions

1. **AsyncGenerator for streaming** — not callbacks, not EventEmitter. Composable, cancellable via for-await-of + AbortController.
2. **MessagePort for IPC streaming** — avoids global ipcRenderer.on pollution, each stream is isolated.
3. **FIM as first-class mode** — not an afterthought. Ghost Text is THE killer feature and needs <200ms latency.
4. **Provider in main process** — network calls stay in main. Renderer never touches fetch/HTTP directly for AI.
5. **Feature-based routing** — different features need different models. Ghost Text needs fast/local, Agent Mode needs smart/cloud.

---

## Testing Strategy

1. **Unit**: Mock fetch, test each provider's stream parsing independently
2. **Integration**: Spin up Ollama, test full complete() cycle
3. **IPC**: Test MessagePort streaming from main to renderer
4. **Fallback**: Kill one provider, verify chain works
5. **FIM**: Test each FIM format (qwen, deepseek, starcoder) against Ollama

---

## OpenClaude Insights (post-research refinement)

OpenClaude uses only **2 real provider types**: `anthropic` (native SDK) and `openai-compatible` (everything else via `OpenAIShimStream` that translates SSE chunks to Anthropic format). Our OllamaProvider and VLLMProvider can share a single `OpenAICompatProvider` base since both speak the OpenAI `/v1/chat/completions` format.

Key patterns to adopt:
- **529 fallback**: After 3 consecutive 529 (overloaded) on Opus, auto-switch to fallback model (Sonnet). Critical for reliability.
- **Retry**: Exponential backoff 500ms * 2^n, max 32s, 25% jitter, 10 retries default. Configurable via env var.
- **Persistent mode**: For unattended sessions (agent mode), retry 429/529 indefinitely with 5-min max backoff + 30s heartbeat yields.
- **Provider profiles**: CRUD for named configs stored in global settings. Activation writes correct env vars. We should support this in Settings Cascade (Phase 1.3).

---

## Dependencies

- `@anthropic-ai/sdk` (npm) — for AnthropicProvider
- None for OllamaProvider/VLLMProvider (raw fetch, OpenAI-compatible)
- No dependency on Phase 1.1 (monolith refactor) — provider is self-contained in main process
