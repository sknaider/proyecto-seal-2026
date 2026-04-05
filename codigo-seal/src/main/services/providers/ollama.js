/**
 * Código SEAL — Ollama Provider
 * Local inference via Ollama API. Zero cost. FIM supported.
 * Based on JARVIS architecture design (PROVIDER_SYSTEM_ARCH.md)
 */

const OLLAMA_BASE = process.env.OLLAMA_BASE_URL || 'http://localhost:11434';

const FIM_FORMATS = {
  qwen: { prefix: '<|fim_prefix|>', suffix: '<|fim_suffix|>', middle: '<|fim_middle|>' },
  deepseek: { prefix: '<｜fim▁begin｜>', suffix: '<｜fim▁hole｜>', middle: '<｜fim▁end｜>' },
  starcoder: { prefix: '<fim_prefix>', suffix: '<fim_suffix>', middle: '<fim_middle>' },
};

function detectFimFormat(modelName) {
  if (modelName.includes('qwen')) return 'qwen';
  if (modelName.includes('deepseek')) return 'deepseek';
  if (modelName.includes('starcoder') || modelName.includes('codellama')) return 'starcoder';
  return null;
}

async function isAvailable() {
  try {
    const res = await fetch(`${OLLAMA_BASE}/api/tags`, { signal: AbortSignal.timeout(3000) });
    return res.ok;
  } catch { return false; }
}

async function listModels() {
  try {
    const res = await fetch(`${OLLAMA_BASE}/api/tags`);
    const data = await res.json();
    return (data.models || []).map(m => ({
      id: m.name,
      provider: 'ollama',
      name: m.name,
      contextWindow: 32768,
      supportsFIM: !!detectFimFormat(m.name),
      supportsStreaming: true,
      fimFormat: detectFimFormat(m.name),
      size: m.size,
      family: m.details?.family,
      parameterSize: m.details?.parameter_size,
    }));
  } catch { return []; }
}

async function* complete(request) {
  const { model, messages, maxTokens, temperature, mode, prefix, suffix, stop } = request;

  if (mode === 'fim' && prefix !== undefined) {
    // FIM mode — use /api/generate with raw prompt
    const fmt = FIM_FORMATS[detectFimFormat(model)] || FIM_FORMATS.qwen;
    const prompt = `${fmt.prefix}${prefix}${fmt.suffix}${suffix || ''}${fmt.middle}`;

    const res = await fetch(`${OLLAMA_BASE}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model, prompt, stream: true,
        options: { num_predict: maxTokens || 256, temperature: temperature || 0.2, stop: stop ? [stop] : ['\n\n'] },
      }),
    });

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
        if (!line.trim()) continue;
        try {
          const chunk = JSON.parse(line);
          if (chunk.response) yield { type: 'text', text: chunk.response };
          if (chunk.done) yield { type: 'done', usage: { totalTokens: chunk.eval_count || 0 } };
        } catch {}
      }
    }
  } else {
    // Chat mode — use /api/chat
    const ollamaMessages = messages.map(m => ({
      role: m.role === 'assistant' ? 'assistant' : m.role === 'system' ? 'system' : 'user',
      content: typeof m.content === 'string' ? m.content : m.content.map(b => b.text || '').join(''),
    }));

    const res = await fetch(`${OLLAMA_BASE}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model, messages: ollamaMessages, stream: true,
        options: { num_predict: maxTokens || 4096, temperature: temperature || 0.7 },
      }),
    });

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
        if (!line.trim()) continue;
        try {
          const chunk = JSON.parse(line);
          if (chunk.message?.content) yield { type: 'text', text: chunk.message.content };
          if (chunk.done) yield { type: 'done', usage: { totalTokens: chunk.eval_count || 0 } };
        } catch {}
      }
    }
  }
}

function estimateTokens(text) {
  return Math.ceil(text.length / 3.5);
}

module.exports = {
  id: 'ollama',
  name: 'Ollama (Local)',
  isLocal: true,
  isAvailable,
  listModels,
  complete,
  estimateTokens,
};
