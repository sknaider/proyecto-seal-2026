/**
 * Código SEAL — 4-Axis Risk Scoring
 * Scores every tool call on 4 dimensions before execution.
 * Based on: ECC2 observability/mod.rs (GEM #1). Clean-room for SEAL.
 *
 * Axes: base_risk + file_sensitivity + blast_radius + irreversibility
 * Score: 0.0 - 1.0 → Allow / Review / RequireConfirmation / Block
 */

const { ipcMain } = require('electron');

// Axis 1: Base tool risk
const BASE_RISK = {
  bash: 0.20, terminal: 0.20,
  write: 0.15, fileWrite: 0.15,
  edit: 0.10, fileEdit: 0.10,
  read: 0.02, fileRead: 0.02,
  grep: 0.01, glob: 0.01,
  git: 0.15,
  ai_complete: 0.05,
  browser: 0.10,
  default: 0.05,
};

// Axis 2: File sensitivity patterns
const FILE_SENSITIVITY = [
  { pattern: /\.env|credentials|secrets|\.pem|\.key|id_rsa/, score: 0.30 },
  { pattern: /patient|medical|clinical|hipaa|phi/i, score: 0.30 },
  { pattern: /Dockerfile|docker-compose|\.yml$/i, score: 0.15 },
  { pattern: /migration|schema|\.sql/i, score: 0.20 },
  { pattern: /capabilities\.yaml|CLAUDE\.md|settings\.json/i, score: 0.25 },
  { pattern: /package\.json|package-lock|yarn\.lock/i, score: 0.10 },
  { pattern: /\.gitignore|\.eslintrc|\.prettierrc/i, score: 0.10 },
];

// Axis 3: Blast radius patterns
const BLAST_RADIUS = [
  { pattern: /git push --force.*main|git push -f.*main/i, score: 0.35 },
  { pattern: /rm -rf\s+[/~*]/, score: 0.35 },
  { pattern: /DROP\s+TABLE|TRUNCATE|DELETE\s+FROM/i, score: 0.30 },
  { pattern: /\*\*\/|\.\*/, score: 0.15 }, // Wildcards
  { pattern: /npm publish|docker push/i, score: 0.25 },
  { pattern: /chmod\s+-R|chown\s+-R/i, score: 0.20 },
  { pattern: /systemctl\s+(stop|restart|disable)/i, score: 0.20 },
];

// Axis 4: Irreversibility patterns
const IRREVERSIBILITY = [
  { pattern: /rm -rf|rm -r\s+[/~]/, score: 0.45 },
  { pattern: /DROP\s+TABLE|DROP\s+DATABASE/i, score: 0.45 },
  { pattern: /git push --force/i, score: 0.40 },
  { pattern: /mkfs|dd\s+.*of=\/dev/i, score: 0.45 },
  { pattern: /git reset --hard/i, score: 0.35 },
  { pattern: /npm unpublish/i, score: 0.35 },
  { pattern: /DELETE\s+FROM.*WHERE\s+1/i, score: 0.40 },
];

// Action thresholds
const THRESHOLDS = {
  allow: 0.25,        // 0.0 - 0.25
  review: 0.50,       // 0.25 - 0.50
  confirm: 0.75,      // 0.50 - 0.75
  block: 1.0,         // 0.75 - 1.0
};

/**
 * Score a tool call on all 4 axes.
 */
function scoreToolCall({ tool, input, filePath, command }) {
  const content = [input, filePath, command].filter(Boolean).join(' ');

  // Axis 1
  const base = BASE_RISK[tool] || BASE_RISK.default;

  // Axis 2
  let sensitivity = 0;
  for (const { pattern, score } of FILE_SENSITIVITY) {
    if (pattern.test(content)) sensitivity = Math.max(sensitivity, score);
  }

  // Axis 3
  let blast = 0;
  for (const { pattern, score } of BLAST_RADIUS) {
    if (pattern.test(content)) blast = Math.max(blast, score);
  }

  // Axis 4
  let irreversibility = 0;
  for (const { pattern, score } of IRREVERSIBILITY) {
    if (pattern.test(content)) irreversibility = Math.max(irreversibility, score);
  }

  // Combined score (clamped to 1.0)
  const total = Math.min(1.0, base + sensitivity + blast + irreversibility);

  // Determine action
  let action;
  if (total <= THRESHOLDS.allow) action = 'allow';
  else if (total <= THRESHOLDS.review) action = 'review';
  else if (total <= THRESHOLDS.confirm) action = 'confirm';
  else action = 'block';

  return {
    score: parseFloat(total.toFixed(3)),
    action,
    axes: {
      base: parseFloat(base.toFixed(3)),
      sensitivity: parseFloat(sensitivity.toFixed(3)),
      blast: parseFloat(blast.toFixed(3)),
      irreversibility: parseFloat(irreversibility.toFixed(3)),
    },
    tool,
    timestamp: new Date().toISOString(),
  };
}

function register() {
  ipcMain.handle('risk:score', async (event, opts) => scoreToolCall(opts));
  ipcMain.handle('risk:thresholds', async () => THRESHOLDS);
}

module.exports = { register, scoreToolCall, THRESHOLDS };
