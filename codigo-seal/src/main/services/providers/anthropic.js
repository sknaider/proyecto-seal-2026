/**
 * Código SEAL — Anthropic Provider
 * Claude API via Messages endpoint. Streaming supported.
 */

const API_BASE = 'https://api.anthropic.com/v1';

function getApiKey() {
  return process.env.ANTHROPIC_API_KEY || '';
}

async function isAvailable() {
  if (!getApiKey()) return false;
  try {
    const res = await fetch(`${API_BASE}/messages`, {
      method: 'POST',
      headers: { 'x-api-key': getApiKey(), 'anthropic-version': '2023-06-01', 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: 'claude-haiku-4-5-20251001', max_tokens: 1, messages: [{ role: 'user', content: 'hi' }] }),
      signal: AbortSignal.timeout(10000),
    });
    return res.ok || res.status === 429; // 429 = rate limited but available
  } catch { return false; }
}

async function listModels() {
  return [
    { id: 'claude-opus-4-6', provider: 'anthropic', name: 'Claude Opus 4.6', contextWindow: 200000, supportsFIM: false, supportsStreaming: true },
    { id: 'claude-sonnet-4-6', provider: 'anthropic', name: 'Claude Sonnet 4.6', contextWindow: 200000, supportsFIM: false, supportsStreaming: true },
    { id: 'claude-haiku-4-5-20251001', provider: 'anthropic', name: 'Claude Haiku 4.5', contextWindow: 200000, supportsFIM: false, supportsStreaming: true },
  ];
}

async function* complete(request) {
  const { model, messages, maxTokens, temperature, system } = request;
  const apiKey = getApiKey();
  if (!apiKey) { yield { type: 'error', error: 'No ANTHROPIC_API_KEY' }; return; }

  const body = {
    model: model || 'claude-sonnet-4-6',
    max_tokens: maxTokens || 4096,
    temperature: temperature || 0.7,
    stream: true,
    messages: messages.filter(m => m.role !== 'system').map(m => ({
      role: m.role, content: typeof m.content === 'string' ? m.content : m.content,
    })),
  };
  const systemMsg = messages.find(m => m.role === 'system') || (system ? { content: system } : null);
  if (systemMsg) body.system = typeof systemMsg.content === 'string' ? systemMsg.content : systemMsg.content;

  try {
    const res = await fetch(`${API_BASE}/messages`, {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    });

    if (!res.ok) {
      const err = await res.text();
      yield { type: 'error', error: `API ${res.status}: ${err.slice(0, 200)}` };
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const data = line.slice(6);
        if (data === '[DONE]') { yield { type: 'done' }; continue; }
        try {
          const event = JSON.parse(data);
          if (event.type === 'content_block_delta' && event.delta?.text) {
            yield { type: 'text', text: event.delta.text };
          }
          if (event.type === 'message_delta' && event.usage) {
            yield { type: 'usage', usage: event.usage };
          }
          if (event.type === 'message_stop') {
            yield { type: 'done' };
          }
        } catch {}
      }
    }
  } catch (e) {
    yield { type: 'error', error: e.message };
  }
}

function estimateTokens(text) {
  return Math.ceil(text.length / 4);
}

module.exports = {
  id: 'anthropic',
  name: 'Anthropic Claude',
  isLocal: false,
  isAvailable,
  listModels,
  complete,
  estimateTokens,
};
