/**
 * Código SEAL — Secret Scanner
 * Scans text for credentials before sending to AI providers.
 * 30+ regex patterns. Credentials NEVER leave the machine.
 * Based on: gitleaks patterns from OpenClaude teamMemorySync analysis.
 */

const PATTERNS = [
  // AWS
  { name: 'AWS Access Key', regex: /AKIA[0-9A-Z]{16}/g },
  { name: 'AWS Secret Key', regex: /(?:aws_secret_access_key|AWS_SECRET_ACCESS_KEY)\s*[=:]\s*['"]?([A-Za-z0-9/+=]{40})['"]?/g },
  { name: 'AWS Session Token', regex: /(?:aws_session_token|AWS_SESSION_TOKEN)\s*[=:]\s*['"]?([A-Za-z0-9/+=]{100,})['"]?/g },

  // Anthropic
  { name: 'Anthropic API Key', regex: /sk-ant-[a-zA-Z0-9_-]{20,}/g },

  // OpenAI
  { name: 'OpenAI API Key', regex: /sk-[a-zA-Z0-9]{20,}/g },

  // GitHub
  { name: 'GitHub PAT (classic)', regex: /ghp_[A-Za-z0-9]{36}/g },
  { name: 'GitHub PAT (fine-grained)', regex: /github_pat_[A-Za-z0-9_]{82}/g },
  { name: 'GitHub OAuth', regex: /gho_[A-Za-z0-9]{36}/g },
  { name: 'GitHub App Token', regex: /(?:ghu|ghs)_[A-Za-z0-9]{36}/g },

  // Google
  { name: 'Google API Key', regex: /AIza[0-9A-Za-z_-]{35}/g },
  { name: 'Google OAuth Secret', regex: /GOCSPX-[A-Za-z0-9_-]{28}/g },

  // Groq
  { name: 'Groq API Key', regex: /gsk_[a-zA-Z0-9]{20,}/g },

  // Stripe
  { name: 'Stripe Live Key', regex: /sk_live_[0-9a-zA-Z]{24,}/g },
  { name: 'Stripe Test Key', regex: /sk_test_[0-9a-zA-Z]{24,}/g },
  { name: 'Stripe Publishable', regex: /pk_(?:live|test)_[0-9a-zA-Z]{24,}/g },

  // Slack
  { name: 'Slack Token', regex: /xox[baprs]-[0-9a-zA-Z-]{10,}/g },
  { name: 'Slack Webhook', regex: /https:\/\/hooks\.slack\.com\/services\/T[0-9A-Z]{8,}\/B[0-9A-Z]{8,}\/[a-zA-Z0-9]{24}/g },

  // Private Keys
  { name: 'RSA Private Key', regex: /-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----/g },
  { name: 'PGP Private Key', regex: /-----BEGIN PGP PRIVATE KEY BLOCK-----/g },

  // Database
  { name: 'PostgreSQL Connection', regex: /postgresql:\/\/[^:]+:[^@]+@[^/]+\/\w+/g },
  { name: 'MySQL Connection', regex: /mysql:\/\/[^:]+:[^@]+@[^/]+\/\w+/g },
  { name: 'MongoDB Connection', regex: /mongodb(?:\+srv)?:\/\/[^:]+:[^@]+@[^/]+/g },

  // Generic
  { name: 'Bearer Token', regex: /Bearer\s+[a-zA-Z0-9_-]{20,}/g },
  { name: 'Basic Auth', regex: /Basic\s+[A-Za-z0-9+/=]{20,}/g },
  { name: 'JWT', regex: /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g },

  // Specific to William's setup
  { name: 'DGX Spark Password', regex: /42478340/g },
  { name: 'SEAL DB Password', regex: /seal_memory_2026/g },
  { name: 'Neo4j Password', regex: /seal2026soul/g },

  // SSH
  { name: 'SSH Private Key Path', regex: /~\/\.ssh\/id_[a-z]+/g },

  // Tokens genéricos
  { name: 'Generic API Key', regex: /(?:api[_-]?key|apikey|api[_-]?token)\s*[=:]\s*['"]?([a-zA-Z0-9_-]{20,})['"]?/gi },
  { name: 'Generic Secret', regex: /(?:secret|password|passwd|pwd)\s*[=:]\s*['"]?([^\s'"]{8,})['"]?/gi },
];

/**
 * Scan text for secrets. Returns array of findings.
 * @param {string} text — Text to scan
 * @returns {{ name: string, match: string, index: number }[]}
 */
function scan(text) {
  const findings = [];
  for (const { name, regex } of PATTERNS) {
    // Reset regex state
    regex.lastIndex = 0;
    let match;
    while ((match = regex.exec(text)) !== null) {
      findings.push({
        name,
        match: match[0].slice(0, 20) + '***',
        index: match.index,
      });
    }
  }
  return findings;
}

/**
 * Redact secrets from text. Replaces matches with [REDACTED].
 * @param {string} text
 * @returns {string}
 */
function redact(text) {
  let result = text;
  for (const { regex } of PATTERNS) {
    regex.lastIndex = 0;
    result = result.replace(regex, '[REDACTED]');
  }
  return result;
}

/**
 * Check if text contains any secrets.
 * @param {string} text
 * @returns {boolean}
 */
function hasSecrets(text) {
  for (const { regex } of PATTERNS) {
    regex.lastIndex = 0;
    if (regex.test(text)) return true;
  }
  return false;
}

module.exports = { scan, redact, hasSecrets, PATTERNS };
