/**
 * Código SEAL — Provider Registry
 * Routes AI requests to the appropriate provider.
 * Implements fallback chain and health monitoring.
 * Design: JARVIS (PROVIDER_SYSTEM_ARCH.md)
 */

const ollamaProvider = require('./providers/ollama.js');
const anthropicProvider = require('./providers/anthropic.js');

class ProviderRegistry {
  constructor() {
    this.providers = new Map();
    this.activeProviderId = null;
    this.fallbackChain = [];
    this.healthCache = new Map(); // id → { available, checkedAt }
  }

  register(provider) {
    this.providers.set(provider.id, provider);
    if (!this.activeProviderId) this.activeProviderId = provider.id;
  }

  setActive(providerId) {
    if (!this.providers.has(providerId)) throw new Error(`Provider ${providerId} not registered`);
    this.activeProviderId = providerId;
  }

  setFallbackChain(providerIds) {
    this.fallbackChain = providerIds.filter(id => this.providers.has(id));
  }

  getActive() {
    return this.providers.get(this.activeProviderId);
  }

  getAll() {
    return [...this.providers.values()].map(p => ({
      id: p.id, name: p.name, isLocal: p.isLocal,
      isActive: p.id === this.activeProviderId,
      status: this.healthCache.get(p.id)?.available ? 'up' : 'unknown',
    }));
  }

  async checkHealth(providerId) {
    const provider = this.providers.get(providerId);
    if (!provider) return false;
    const available = await provider.isAvailable();
    this.healthCache.set(providerId, { available, checkedAt: Date.now() });
    return available;
  }

  async checkAllHealth() {
    const results = {};
    for (const [id] of this.providers) {
      results[id] = await this.checkHealth(id);
    }
    return results;
  }

  async listModels(providerId) {
    const provider = this.providers.get(providerId || this.activeProviderId);
    if (!provider) return [];
    return await provider.listModels();
  }

  async getBestFIMModel() {
    // Find best model that supports FIM across all providers
    for (const [, provider] of this.providers) {
      if (!await provider.isAvailable()) continue;
      const models = await provider.listModels();
      const fimModel = models.find(m => m.supportsFIM);
      if (fimModel) return { provider: provider.id, model: fimModel.id };
    }
    return null;
  }

  async* complete(request) {
    // Auto-select FIM model if mode is 'fim' and no model specified
    if (request.mode === 'fim' && !request.model) {
      const best = await this.getBestFIMModel();
      if (best) { request.model = best.model; request._provider = best.provider; }
    }
    // Try active provider first
    const chain = [this.activeProviderId, ...this.fallbackChain.filter(id => id !== this.activeProviderId)];

    for (const providerId of chain) {
      const provider = this.providers.get(providerId);
      if (!provider) continue;

      try {
        const available = await provider.isAvailable();
        if (!available) continue;

        for await (const chunk of provider.complete(request)) {
          if (chunk.type === 'error') {
            console.warn(`[Provider:${providerId}] Error: ${chunk.error}`);
            break; // Try next in chain
          }
          yield { ...chunk, provider: providerId };
        }
        return; // Success — don't try fallback
      } catch (e) {
        console.warn(`[Provider:${providerId}] Failed: ${e.message}`);
        continue; // Try next in chain
      }
    }

    yield { type: 'error', error: 'All providers failed' };
  }

  estimateTokens(text, providerId) {
    const provider = this.providers.get(providerId || this.activeProviderId);
    return provider ? provider.estimateTokens(text) : Math.ceil(text.length / 4);
  }
}

// Singleton
const registry = new ProviderRegistry();

// Register default providers
registry.register(ollamaProvider);
registry.register(anthropicProvider);

// Default: prefer Ollama local (free), fallback to Anthropic
registry.setActive('ollama');
registry.setFallbackChain(['ollama', 'anthropic']);

module.exports = registry;
