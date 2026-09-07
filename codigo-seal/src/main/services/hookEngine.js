/**
 * Código SEAL — Hook Lifecycle Engine
 * Event system for IDE lifecycle events.
 * Plugins and features register handlers without coupling.
 * Based on: Claude Code hook system (SPEC_15, 5,022 lines in original).
 * Our clean-room implementation: ~120 lines.
 */

const HOOK_EVENTS = [
  'PreToolUse',       // Before a tool executes (can block)
  'PostToolUse',      // After a tool completes
  'PreSend',          // Before sending message to AI provider
  'PostSend',         // After receiving AI response
  'SessionStart',     // Session begins
  'SessionEnd',       // Session closes
  'PreCompact',       // Before context compaction
  'PostCompact',      // After context compaction
  'FileChanged',      // A file was modified
  'FileOpened',       // A file was opened in editor
  'TerminalOutput',   // Terminal produced output
  'ChatMessage',      // Chat message received
  'AgentSpawn',       // Sub-agent spawned
  'AgentComplete',    // Sub-agent finished
  'Error',            // Error occurred
];

class HookEngine {
  constructor() {
    this.handlers = new Map();
    HOOK_EVENTS.forEach(event => this.handlers.set(event, []));
  }

  /**
   * Register a handler for an event.
   * @param {string} event — One of HOOK_EVENTS
   * @param {Function} handler — async (context) => { result? }
   * @param {Object} [opts] — { priority: number, name: string, timeout: number }
   * @returns {Function} unregister function
   */
  on(event, handler, opts = {}) {
    if (!this.handlers.has(event)) {
      console.warn(`[Hooks] Unknown event: ${event}`);
      return () => {};
    }
    const entry = {
      handler,
      name: opts.name || handler.name || 'anonymous',
      priority: opts.priority || 0,
      timeout: opts.timeout || 10000,
    };
    const list = this.handlers.get(event);
    list.push(entry);
    list.sort((a, b) => b.priority - a.priority); // Higher priority first

    // Return unregister function
    return () => {
      const idx = list.indexOf(entry);
      if (idx >= 0) list.splice(idx, 1);
    };
  }

  /**
   * Emit an event. Runs all handlers in priority order.
   * Pre* hooks can return { block: true, reason: string } to prevent action.
   * @param {string} event
   * @param {Object} context — Event-specific data
   * @returns {Promise<{ blocked: boolean, reason?: string, results: any[] }>}
   */
  async emit(event, context = {}) {
    const list = this.handlers.get(event) || [];
    const results = [];

    for (const entry of list) {
      try {
        const result = await Promise.race([
          entry.handler({ ...context, event }),
          new Promise((_, reject) => setTimeout(() => reject(new Error('Hook timeout')), entry.timeout)),
        ]);
        results.push({ name: entry.name, result });

        // Pre* hooks can block
        if (event.startsWith('Pre') && result && result.block) {
          return { blocked: true, reason: result.reason || entry.name, results };
        }
      } catch (e) {
        console.warn(`[Hooks] ${entry.name} failed on ${event}: ${e.message}`);
        results.push({ name: entry.name, error: e.message });
      }
    }

    return { blocked: false, results };
  }

  /**
   * List registered handlers for an event (or all events).
   */
  list(event) {
    if (event) {
      return (this.handlers.get(event) || []).map(h => ({ name: h.name, priority: h.priority }));
    }
    const all = {};
    for (const [evt, handlers] of this.handlers) {
      if (handlers.length > 0) all[evt] = handlers.map(h => ({ name: h.name, priority: h.priority }));
    }
    return all;
  }

  /**
   * Remove all handlers (for cleanup/testing).
   */
  clear() {
    for (const [, list] of this.handlers) list.length = 0;
  }
}

// Singleton
const hooks = new HookEngine();

module.exports = { hooks, HOOK_EVENTS };
