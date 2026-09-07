/**
 * Código SEAL — Data Sovereignty Shield
 * Ensures sensitive data NEVER leaves the machine.
 * Integrates Secret Scanner + Provider routing to keep medical/sensitive data local.
 * Phase 4.8 from Master Plan.
 */

const { ipcMain } = require('electron');
const secretScanner = require('./secretScanner.js');
const settings = require('./settings.js');

class DataSovereigntyShield {
  constructor() {
    this.enabled = true;
    this.stats = { blocked: 0, redacted: 0, localRouted: 0 };
    this.sensitivePatterns = [
      /\b\d{3}-\d{2}-\d{4}\b/g,  // SSN
      /\b\d{8}\b/g,               // DNI Peru (8 digits)
      /\bpaciente\b/gi,           // Medical context
      /\bdiagnos(tico|is)\b/gi,   // Diagnosis
      /\bprescripci[oó]n\b/gi,    // Prescription
      /\bHIPAA\b/gi,              // HIPAA mention
      /\bhistoria\s+cl[ií]nica\b/gi, // Medical record
    ];
  }

  /**
   * Check if content contains sensitive data that should stay local.
   */
  isSensitive(text) {
    // Check for secrets (API keys, passwords)
    if (secretScanner.hasSecrets(text)) return { sensitive: true, reason: 'credentials' };

    // Check for medical/personal data
    for (const pattern of this.sensitivePatterns) {
      pattern.lastIndex = 0;
      if (pattern.test(text)) return { sensitive: true, reason: 'medical/personal data' };
    }

    // Check settings
    if (settings.get('localInferenceOnly')) return { sensitive: true, reason: 'localInferenceOnly setting' };

    return { sensitive: false };
  }

  /**
   * Pre-send hook: check content before sending to AI provider.
   * If sensitive, force local provider or redact.
   */
  preSendCheck(content, targetProvider) {
    if (!this.enabled) return { allow: true };

    const check = this.isSensitive(content);
    if (!check.sensitive) return { allow: true };

    // If target is local (Ollama, vLLM), allow
    if (targetProvider === 'ollama' || targetProvider === 'vllm') {
      this.stats.localRouted++;
      return { allow: true, note: 'Sensitive content routed to local provider' };
    }

    // If target is cloud (Anthropic, OpenAI), block or redact
    this.stats.blocked++;
    return {
      allow: false,
      reason: check.reason,
      suggestion: 'Route to local provider (Ollama) or redact sensitive content',
      redactedContent: secretScanner.redact(content),
    };
  }

  /**
   * Get shield status and stats.
   */
  getStatus() {
    return {
      enabled: this.enabled,
      stats: this.stats,
      localInferenceOnly: settings.get('localInferenceOnly'),
      secretScanEnabled: settings.get('secretScanEnabled'),
    };
  }

  /**
   * Toggle shield.
   */
  toggle(enabled) {
    this.enabled = enabled;
    return { enabled };
  }
}

const shield = new DataSovereigntyShield();

function register() {
  ipcMain.handle('sovereignty:check', async (event, { content, provider }) => shield.preSendCheck(content, provider));
  ipcMain.handle('sovereignty:status', async () => shield.getStatus());
  ipcMain.handle('sovereignty:toggle', async (event, { enabled }) => shield.toggle(enabled));
  ipcMain.handle('sovereignty:isSensitive', async (event, { text }) => shield.isSensitive(text));
}

module.exports = { register, shield };
