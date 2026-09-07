/**
 * Código SEAL — Bash Command Safety Analyzer
 * Analyzes bash commands for dangerous patterns before execution.
 * Based on: Claude Code bash parser (4,436 lines). Clean-room: ~120 lines.
 */

// Dangerous commands that should ALWAYS be blocked or warned
const BLOCKED_PATTERNS = [
  { pattern: /rm\s+(-rf?|--recursive)\s+[/~]/, level: 'block', reason: 'Recursive delete on root/home' },
  { pattern: /rm\s+-rf?\s+\*/, level: 'block', reason: 'Recursive delete wildcard' },
  { pattern: /mkfs/, level: 'block', reason: 'Filesystem format' },
  { pattern: /dd\s+.*of=\/dev\//, level: 'block', reason: 'Direct disk write' },
  { pattern: /:(){ :\|:& };:/, level: 'block', reason: 'Fork bomb' },
  { pattern: />\s*\/dev\/sd[a-z]/, level: 'block', reason: 'Direct device write' },
  { pattern: /chmod\s+(-R\s+)?777\s+\//, level: 'block', reason: 'Chmod 777 on system path' },
  { pattern: /chown\s+-R\s+.*\s+\//, level: 'block', reason: 'Recursive chown on root' },
];

const WARN_PATTERNS = [
  { pattern: /rm\s+-/, level: 'warn', reason: 'File deletion' },
  { pattern: /git\s+(reset|clean|push\s+--force|checkout\s+--)/, level: 'warn', reason: 'Destructive git operation' },
  { pattern: /kill\s+-9/, level: 'warn', reason: 'Force kill process' },
  { pattern: /sudo\s+/, level: 'warn', reason: 'Elevated privileges' },
  { pattern: /curl\s+.*\|\s*(bash|sh)/, level: 'warn', reason: 'Pipe to shell (remote code execution)' },
  { pattern: /wget\s+.*\|\s*(bash|sh)/, level: 'warn', reason: 'Pipe to shell (remote code execution)' },
  { pattern: /eval\s+/, level: 'warn', reason: 'Dynamic code execution' },
  { pattern: />\s*\/etc\//, level: 'warn', reason: 'Write to system config' },
  { pattern: /systemctl\s+(stop|disable|mask)/, level: 'warn', reason: 'Service control' },
  { pattern: /iptables|ufw/, level: 'warn', reason: 'Firewall modification' },
  { pattern: /DROP\s+TABLE|TRUNCATE|DELETE\s+FROM.*WHERE\s+1/i, level: 'warn', reason: 'Destructive SQL' },
  { pattern: /npm\s+publish/, level: 'warn', reason: 'Package publish' },
  { pattern: /docker\s+(rm|rmi|system\s+prune)/, level: 'warn', reason: 'Docker cleanup' },
];

const SAFE_PATTERNS = [
  /^(ls|cat|head|tail|grep|find|wc|echo|pwd|date|whoami|hostname|uname)\b/,
  /^git\s+(status|log|diff|branch|show|stash\s+list)\b/,
  /^(node|python3?|npm\s+run|npm\s+test|npm\s+install)\b/,
  /^(cd|mkdir|cp|mv|touch)\b/,
  /^nvidia-smi/,
  /^(curl|wget)\s+-s/,
  /^(ps|top|htop|df|du|free)\b/,
];

/**
 * Analyze a bash command for safety.
 * @param {string} command
 * @returns {{ level: 'safe'|'warn'|'block', reason?: string, patterns: string[] }}
 */
function analyze(command) {
  const cmd = command.trim();
  const findings = [];

  // Check blocked first
  for (const { pattern, level, reason } of BLOCKED_PATTERNS) {
    if (pattern.test(cmd)) {
      findings.push({ level, reason });
    }
  }
  if (findings.some(f => f.level === 'block')) {
    return { level: 'block', reason: findings[0].reason, patterns: findings.map(f => f.reason) };
  }

  // Check warnings
  for (const { pattern, level, reason } of WARN_PATTERNS) {
    if (pattern.test(cmd)) {
      findings.push({ level, reason });
    }
  }
  if (findings.length > 0) {
    return { level: 'warn', reason: findings[0].reason, patterns: findings.map(f => f.reason) };
  }

  // Check if explicitly safe
  for (const pattern of SAFE_PATTERNS) {
    if (pattern.test(cmd)) {
      return { level: 'safe', patterns: ['known safe command'] };
    }
  }

  // Unknown — default to warn
  return { level: 'warn', reason: 'Unknown command — review recommended', patterns: [] };
}

/**
 * Check if a command is read-only (safe for auto-approval).
 */
function isReadOnly(command) {
  const result = analyze(command);
  return result.level === 'safe';
}

module.exports = { analyze, isReadOnly, BLOCKED_PATTERNS, WARN_PATTERNS, SAFE_PATTERNS };
