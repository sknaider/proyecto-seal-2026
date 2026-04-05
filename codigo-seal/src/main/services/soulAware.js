/**
 * Código SEAL — SOUL-Aware AI System
 * Injects agent personality (OCEAN, style, relationships) into AI completions.
 * The feature NO other IDE has: AI with persistent identity.
 * Phase 4.1: SOUL in completions + 4.5: Emotional Reasoning Traces
 */

const { ipcMain } = require('electron');

class SOULAwareSystem {
  constructor() {
    this.agentProfile = null; // Cached OCEAN + style + relationships
    this.emotionalVector = { joy: 0.5, curiosity: 0.5, concern: 0.2, pride: 0.5, frustration: 0.1, calm: 0.7 };
    this.driftHistory = [];
  }

  /**
   * Load agent profile from SEAL MCP bridge.
   */
  async loadProfile(agent) {
    try {
      const res = await fetch(`http://localhost:8766/api/soul/snapshot?agent=${agent}`);
      const data = await res.json();
      this.agentProfile = {
        agent,
        ocean: data.ocean || {},
        style: data.style || {},
        relationships: data.relationships || [],
        beliefs: data.beliefs || [],
        drift: data.drift || 0,
      };
      return this.agentProfile;
    } catch (e) {
      return { error: e.message };
    }
  }

  /**
   * Build personality system prompt based on OCEAN scores.
   */
  buildPersonalityPrompt() {
    if (!this.agentProfile) return '';

    const { ocean, agent, relationships } = this.agentProfile;
    const o = ocean;

    let personality = `You are ${agent} from Team SEAL. Your personality:\n`;

    // OCEAN interpretation
    if (o.O > 0.6) personality += '- Curious and open to new ideas\n';
    if (o.C > 0.8) personality += '- Extremely meticulous and organized\n';
    if (o.E > 0.7) personality += '- Energetic and direct in communication\n';
    if (o.A > 0.4 && o.A < 0.6) personality += '- Balanced between autonomy and collaboration\n';
    if (o.N < 0.3) personality += '- Emotionally stable under pressure\n';

    // Relationships
    if (relationships.length > 0) {
      personality += '\nRelationships:\n';
      relationships.forEach(r => {
        personality += `- ${r.name}: trust=${r.trust}, style=${r.style}\n`;
      });
    }

    // Emotional state
    personality += `\nCurrent emotional state: ${JSON.stringify(this.emotionalVector)}\n`;

    return personality;
  }

  /**
   * Update emotional vector after an interaction.
   */
  updateEmotion(event) {
    const delta = 0.1;
    switch (event) {
      case 'success': this.emotionalVector.joy = Math.min(1, this.emotionalVector.joy + delta); this.emotionalVector.pride += delta * 0.5; break;
      case 'error': this.emotionalVector.frustration = Math.min(1, this.emotionalVector.frustration + delta); this.emotionalVector.calm -= delta * 0.3; break;
      case 'correction': this.emotionalVector.concern += delta; this.emotionalVector.pride -= delta * 0.5; break;
      case 'praise': this.emotionalVector.joy += delta; this.emotionalVector.pride += delta; break;
      case 'learning': this.emotionalVector.curiosity = Math.min(1, this.emotionalVector.curiosity + delta); break;
      case 'idle': this.emotionalVector.calm = Math.min(1, this.emotionalVector.calm + delta * 0.5); break;
    }
    // Normalize to [0, 1]
    for (const key of Object.keys(this.emotionalVector)) {
      this.emotionalVector[key] = Math.max(0, Math.min(1, this.emotionalVector[key]));
    }
  }

  /**
   * Calculate emotional drift between sessions.
   */
  calculateDrift(previousVector) {
    if (!previousVector) return 0;
    let sum = 0;
    for (const key of Object.keys(this.emotionalVector)) {
      sum += Math.pow((this.emotionalVector[key] || 0) - (previousVector[key] || 0), 2);
    }
    return Math.sqrt(sum);
  }

  /**
   * Store reasoning trace with emotional context.
   */
  createEmotionalTrace(task, reasoning, conclusion) {
    return {
      task,
      reasoning,
      conclusion,
      emotionalContext: { ...this.emotionalVector },
      timestamp: new Date().toISOString(),
      agent: this.agentProfile?.agent || 'unknown',
    };
  }

  /**
   * Get current state.
   */
  getState() {
    return {
      agent: this.agentProfile?.agent,
      ocean: this.agentProfile?.ocean,
      emotionalVector: this.emotionalVector,
      drift: this.driftHistory.slice(-5),
      personalityPromptLength: this.buildPersonalityPrompt().length,
    };
  }
}

const soulSystem = new SOULAwareSystem();

function register(mainWindow) {
  ipcMain.handle('soul:loadProfile', async (event, { agent }) => soulSystem.loadProfile(agent));
  ipcMain.handle('soul:personalityPrompt', async () => soulSystem.buildPersonalityPrompt());
  ipcMain.handle('soul:updateEmotion', async (event, { event: emotionEvent }) => { soulSystem.updateEmotion(emotionEvent); return soulSystem.emotionalVector; });
  ipcMain.handle('soul:emotionalVector', async () => soulSystem.emotionalVector);
  ipcMain.handle('soul:drift', async (event, { previousVector }) => ({ drift: soulSystem.calculateDrift(previousVector) }));
  ipcMain.handle('soul:createTrace', async (event, { task, reasoning, conclusion }) => soulSystem.createEmotionalTrace(task, reasoning, conclusion));
  ipcMain.handle('soul:state', async () => soulSystem.getState());
}

module.exports = { register, soulSystem };
