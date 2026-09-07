/**
 * Código SEAL — Plugin System
 * Load plugins from .seal/plugins/ with manifests.
 * Plugins can add: commands, tools, hooks, skills.
 * Based on: Claude Code plugin system (SPEC_15). Clean-room: ~130 lines.
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');

const PLUGINS_DIR = path.join(os.homedir(), '.seal', 'plugins');

class PluginManager {
  constructor() {
    this.plugins = new Map(); // id → { manifest, enabled, path, commands, hooks }
  }

  /**
   * Discover and load all plugins.
   */
  loadAll() {
    fs.mkdirSync(PLUGINS_DIR, { recursive: true });
    const dirs = fs.readdirSync(PLUGINS_DIR, { withFileTypes: true })
      .filter(d => d.isDirectory());

    for (const dir of dirs) {
      try {
        this.loadPlugin(path.join(PLUGINS_DIR, dir.name));
      } catch (e) {
        console.warn(`[Plugins] Failed to load ${dir.name}: ${e.message}`);
      }
    }
    return this.list();
  }

  /**
   * Load a single plugin from directory.
   */
  loadPlugin(pluginPath) {
    const manifestPath = path.join(pluginPath, 'plugin.json');
    if (!fs.existsSync(manifestPath)) return null;

    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
    const id = manifest.id || path.basename(pluginPath);

    // Validate manifest
    if (!manifest.name) throw new Error('Plugin missing name');

    const plugin = {
      id,
      manifest,
      path: pluginPath,
      enabled: manifest.enabled !== false,
      commands: [],
      hooks: [],
      skills: [],
    };

    // Load commands (.md files in commands/)
    const cmdDir = path.join(pluginPath, 'commands');
    if (fs.existsSync(cmdDir)) {
      plugin.commands = fs.readdirSync(cmdDir)
        .filter(f => f.endsWith('.md'))
        .map(f => ({ name: f.replace('.md', ''), file: path.join(cmdDir, f) }));
    }

    // Load hooks (hooks.json)
    const hooksPath = path.join(pluginPath, 'hooks.json');
    if (fs.existsSync(hooksPath)) {
      try { plugin.hooks = JSON.parse(fs.readFileSync(hooksPath, 'utf-8')); } catch {}
    }

    // Load skills (.md files in skills/)
    const skillDir = path.join(pluginPath, 'skills');
    if (fs.existsSync(skillDir)) {
      plugin.skills = fs.readdirSync(skillDir)
        .filter(f => f.endsWith('.md'))
        .map(f => ({ name: f.replace('.md', ''), file: path.join(skillDir, f) }));
    }

    this.plugins.set(id, plugin);
    return plugin;
  }

  /**
   * Enable/disable a plugin.
   */
  toggle(id, enabled) {
    const plugin = this.plugins.get(id);
    if (!plugin) return { error: 'Plugin not found' };
    plugin.enabled = enabled;
    // Update manifest
    plugin.manifest.enabled = enabled;
    fs.writeFileSync(path.join(plugin.path, 'plugin.json'), JSON.stringify(plugin.manifest, null, 2));
    return { id, enabled };
  }

  /**
   * List all plugins.
   */
  list() {
    return [...this.plugins.values()].map(p => ({
      id: p.id,
      name: p.manifest.name,
      version: p.manifest.version || '0.0.0',
      description: p.manifest.description || '',
      enabled: p.enabled,
      commands: p.commands.length,
      hooks: p.hooks.length || 0,
      skills: p.skills.length,
    }));
  }

  /**
   * Get all commands from enabled plugins.
   */
  getCommands() {
    const commands = [];
    for (const plugin of this.plugins.values()) {
      if (!plugin.enabled) continue;
      for (const cmd of plugin.commands) {
        commands.push({ ...cmd, plugin: plugin.id });
      }
    }
    return commands;
  }

  /**
   * Install plugin from path/URL (copies to plugins dir).
   */
  install(sourcePath) {
    const name = path.basename(sourcePath);
    const destPath = path.join(PLUGINS_DIR, name);
    if (fs.existsSync(destPath)) return { error: 'Plugin already exists' };
    // Copy directory
    fs.cpSync(sourcePath, destPath, { recursive: true });
    return this.loadPlugin(destPath);
  }

  /**
   * Remove plugin.
   */
  remove(id) {
    const plugin = this.plugins.get(id);
    if (!plugin) return { error: 'Plugin not found' };
    fs.rmSync(plugin.path, { recursive: true, force: true });
    this.plugins.delete(id);
    return { removed: id };
  }
}

const pluginManager = new PluginManager();

function register() {
  ipcMain.handle('plugins:loadAll', async () => pluginManager.loadAll());
  ipcMain.handle('plugins:list', async () => pluginManager.list());
  ipcMain.handle('plugins:toggle', async (event, { id, enabled }) => pluginManager.toggle(id, enabled));
  ipcMain.handle('plugins:commands', async () => pluginManager.getCommands());
  ipcMain.handle('plugins:install', async (event, { sourcePath }) => pluginManager.install(sourcePath));
  ipcMain.handle('plugins:remove', async (event, { id }) => pluginManager.remove(id));
}

module.exports = { register, pluginManager };
