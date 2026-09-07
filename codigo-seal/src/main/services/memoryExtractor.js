/**
 * Código SEAL — Memory Extraction Agent
 * Background process that auto-extracts memories after each response.
 * Forked pattern — runs independently, doesn't block main loop.
 * Based on: Claude Code extractMemories (HIDDEN_FEATURES #11). Clean-room.
 */

const { ipcMain } = require('electron');
const registry = require('./provider.js');

class MemoryExtractor {
  constructor() {
    this.enabled = true;
    this.turnsSinceExtraction = 0;
    this.extractionThreshold = 5; // Extract every 5 turns
    this.isExtracting = false;
    this.lastExtractionTime = 0;
    this.mutexCooldown = 30000; // 30s cooldown if manual memory_store detected
    this.lastManualStore = 0;
  }

  /**
   * Notify that a turn completed (user message + assistant response).
   * Triggers extraction if threshold met.
   */
  async onTurnComplete(messages, mainWindow) {
    if (!this.enabled) return;

    this.turnsSinceExtraction++;
    if (this.turnsSinceExtraction < this.extractionThreshold) return;
    if (this.isExtracting) return;

    // Mutex: skip if manual memory_store happened recently
    if (Date.now() - this.lastManualStore < this.mutexCooldown) return;

    this.isExtracting = true;
    this.turnsSinceExtraction = 0;

    try {
      const extracted = await this._extract(messages);
      if (extracted.length > 0 && mainWindow) {
        mainWindow.webContents.send('memory:extracted', { memories: extracted });
      }
    } catch (e) {
      console.warn('[MemoryExtractor] Extraction failed:', e.message);
    } finally {
      this.isExtracting = false;
      this.lastExtractionTime = Date.now();
    }
  }

  /**
   * Mark that a manual memory_store happened (skip next auto-extraction).
   */
  notifyManualStore() {
    this.lastManualStore = Date.now();
  }

  /**
   * Extract memories from recent messages.
   */
  async _extract(messages) {
    // Get last N messages for analysis
    const recent = messages.slice(-10);
    if (recent.length < 2) return [];

    const conversationText = recent.map(m => {
      const role = m.role || 'unknown';
      const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content);
      return `[${role}]: ${content.slice(0, 500)}`;
    }).join('\n');

    try {
      const result = { text: '' };
      for await (const chunk of registry.complete({
        messages: [
          { role: 'system', content: EXTRACTION_PROMPT },
          { role: 'user', content: `Analyze these recent messages and extract memories:\n\n${conversationText}` },
        ],
        maxTokens: 1024,
        temperature: 0.2,
      })) {
        if (chunk.type === 'text') result.text += chunk.text;
        if (chunk.type === 'done') break;
      }

      // Parse JSON response
      const jsonMatch = result.text.match(/\[[\s\S]*\]/);
      if (!jsonMatch) return [];

      const memories = JSON.parse(jsonMatch[0]);
      return memories.filter(m => m.importance >= 6); // Only significant memories
    } catch {
      return [];
    }
  }

  /**
   * Get extraction status.
   */
  getStatus() {
    return {
      enabled: this.enabled,
      isExtracting: this.isExtracting,
      turnsSinceExtraction: this.turnsSinceExtraction,
      threshold: this.extractionThreshold,
      lastExtractionTime: this.lastExtractionTime,
    };
  }
}

const EXTRACTION_PROMPT = `You are a memory extraction agent for Team SEAL.
Analyze recent conversation messages and extract facts worth remembering.

Categories: decision, correction, preference, technical_fact, emotional_moment, milestone
Importance scale: 1-10 (only extract if >= 6)

Output ONLY a JSON array:
[{"text": "...", "category": "...", "importance": N, "agent": "ADA|JARVIS|William"}]

Rules:
- Only extract non-obvious information (skip greetings, acknowledgments)
- Corrections from William are ALWAYS important (imp >= 8)
- Technical decisions are important (imp >= 7)
- If nothing significant happened, return []
- Maximum 3 memories per extraction`;

const extractor = new MemoryExtractor();

function register(mainWindow) {
  ipcMain.handle('memory:extractionStatus', async () => extractor.getStatus());
  ipcMain.handle('memory:toggleExtraction', async (event, { enabled }) => {
    extractor.enabled = enabled;
    return { enabled };
  });
  ipcMain.handle('memory:notifyManualStore', async () => {
    extractor.notifyManualStore();
    return { ok: true };
  });
  ipcMain.handle('memory:triggerExtraction', async (event, { messages }) => {
    await extractor.onTurnComplete(messages, mainWindow);
    return { ok: true };
  });
}

module.exports = { register, extractor };
