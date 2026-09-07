/**
 * Código SEAL — Settings Cascade (7 levels)
 * Resolves settings from multiple sources in priority order.
 * Based on: Claude Code settings system (SPEC_15, 1,015 lines in original).
 * Clean-room: ~130 lines.
 *
 * Priority (lowest → highest):
 * 1. defaults — Built-in defaults
 * 2. managed — /etc/seal/settings.json (system admin)
 * 3. user — ~/.seal/settings.json (user global)
 * 4. project — .seal/settings.json (project root)
 * 5. local — .seal/settings.local.json (gitignored)
 * 6. cli — Command-line flags
 * 7. runtime — In-memory overrides
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

const DEFAULTS = {
  provider: 'ollama',
  model: 'qwen2.5-coder:7b',
  theme: 'seal-dark',
  language: 'es',
  fontSize: 13,
  tabSize: 2,
  wordWrap: true,
  minimap: true,
  autoSave: false,
  terminalFontSize: 12,
  chatMaxMessages: 500,
  // Provider settings
  providers: {
    ollama: { baseUrl: 'http://localhost:11434', models: [] },
    anthropic: { apiKey: '', models: [] },
  },
  // Agent routing
  agentRouting: {
    ADA: 'ollama',
    JARVIS: 'anthropic',
    DUM: 'ollama',
    default: 'ollama',
  },
  // Permissions
  permissions: {
    allow: [],
    deny: [],
    defaultMode: 'ask',
  },
  // Hooks
  hooks: {},
  // Data sovereignty
  secretScanEnabled: true,
  localInferenceOnly: false,
};

const SOURCES = [
  { name: 'managed', path: '/etc/seal/settings.json' },
  { name: 'user', path: path.join(os.homedir(), '.seal', 'settings.json') },
  { name: 'project', path: null }, // resolved per-project
  { name: 'local', path: null },   // resolved per-project
];

let cliOverrides = {};
let runtimeOverrides = {};
let projectRoot = null;
let cachedSettings = null;

function loadJsonSafe(filePath) {
  try {
    if (!filePath || !fs.existsSync(filePath)) return {};
    return JSON.parse(fs.readFileSync(filePath, 'utf-8'));
  } catch (e) {
    console.warn(`[Settings] Failed to load ${filePath}: ${e.message}`);
    return {};
  }
}

function deepMerge(target, source) {
  const result = { ...target };
  for (const key of Object.keys(source)) {
    if (source[key] && typeof source[key] === 'object' && !Array.isArray(source[key]) &&
        result[key] && typeof result[key] === 'object' && !Array.isArray(result[key])) {
      result[key] = deepMerge(result[key], source[key]);
    } else {
      result[key] = source[key];
    }
  }
  return result;
}

function resolve() {
  let merged = { ...DEFAULTS };

  // 1. Managed (system admin)
  merged = deepMerge(merged, loadJsonSafe(SOURCES[0].path));

  // 2. User global
  merged = deepMerge(merged, loadJsonSafe(SOURCES[1].path));

  // 3. Project
  if (projectRoot) {
    merged = deepMerge(merged, loadJsonSafe(path.join(projectRoot, '.seal', 'settings.json')));
  }

  // 4. Local (gitignored)
  if (projectRoot) {
    merged = deepMerge(merged, loadJsonSafe(path.join(projectRoot, '.seal', 'settings.local.json')));
  }

  // 5. CLI overrides
  merged = deepMerge(merged, cliOverrides);

  // 6. Runtime overrides
  merged = deepMerge(merged, runtimeOverrides);

  cachedSettings = merged;
  return merged;
}

function get(key) {
  if (!cachedSettings) resolve();
  if (!key) return cachedSettings;
  return key.split('.').reduce((obj, k) => obj && obj[k], cachedSettings);
}

function set(key, value) {
  const keys = key.split('.');
  let obj = runtimeOverrides;
  for (let i = 0; i < keys.length - 1; i++) {
    if (!obj[keys[i]]) obj[keys[i]] = {};
    obj = obj[keys[i]];
  }
  obj[keys[keys.length - 1]] = value;
  cachedSettings = null; // Invalidate cache
}

function setProjectRoot(root) {
  projectRoot = root;
  cachedSettings = null;
}

function setCLIOverrides(overrides) {
  cliOverrides = overrides;
  cachedSettings = null;
}

function saveUser(settings) {
  const userPath = SOURCES[1].path;
  const dir = path.dirname(userPath);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(userPath, JSON.stringify(settings, null, 2));
  cachedSettings = null;
}

function reset() {
  cliOverrides = {};
  runtimeOverrides = {};
  cachedSettings = null;
}

module.exports = { get, set, resolve, setProjectRoot, setCLIOverrides, saveUser, reset, DEFAULTS };
