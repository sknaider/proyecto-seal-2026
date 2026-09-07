/**
 * Código SEAL — Governance Event Capture
 * Auto-detect and log governance-relevant events for compliance.
 * Based on: ECC governance-capture.js (GEM #4). Clean-room for SEAL.
 *
 * Events: secret_detected, approval_requested, policy_violation,
 *         security_finding, medical_data_access, phi_exposure
 */

const { ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const os = require('os');

const LOG_DIR = path.join(os.homedir(), '.seal', 'governance');
const LOG_FILE = path.join(LOG_DIR, 'audit_trail.jsonl');

// Detection patterns
const PATTERNS = {
  secret_detected: [
    { name: 'AWS Key', regex: /AKIA[0-9A-Z]{16}/ },
    { name: 'JWT', regex: /eyJ[A-Za-z0-9_-]{10,}\.eyJ/ },
    { name: 'GitHub PAT', regex: /ghp_[A-Za-z0-9]{36}/ },
    { name: 'Private Key', regex: /-----BEGIN.*PRIVATE KEY-----/ },
    { name: 'Anthropic Key', regex: /sk-ant-[a-zA-Z0-9_-]{20,}/ },
    { name: 'Generic API Key', regex: /api[_-]?key\s*[=:]\s*['"]?[a-zA-Z0-9_-]{20,}/i },
  ],
  approval_required: [
    { name: 'Force push', regex: /git push --force|git push -f/ },
    { name: 'Destructive delete', regex: /rm -rf|DROP TABLE|TRUNCATE/ },
    { name: 'Production deploy', regex: /deploy.*prod|npm publish/ },
    { name: 'Schema migration', regex: /ALTER TABLE|CREATE INDEX.*ON/ },
    { name: 'Model merge', regex: /merge.*adapter|merge.*lora/i },
  ],
  policy_violation: [
    { name: 'Write to credentials', regex: /\.env|credentials\.json|\.pem|\.key/ },
    { name: 'Modify safety config', regex: /capabilities\.yaml|CLAUDE\.md|settings\.json/ },
    { name: 'Skip hooks', regex: /--no-verify|--no-gpg-sign/ },
  ],
  medical_data: [
    { name: 'Patient data access', regex: /patient|paciente|historia\s+cl[ií]nica/i },
    { name: 'PHI exposure', regex: /\b\d{3}-\d{2}-\d{4}\b|\b\d{8}\b/ }, // SSN, DNI
    { name: 'Clinical inference', regex: /diagnos|prescripci[oó]n|tratamiento/i },
    { name: 'Medical model', regex: /medgemma|medical.*model|clinical.*ai/i },
  ],
};

class GovernanceCapture {
  constructor() {
    fs.mkdirSync(LOG_DIR, { recursive: true });
    this.events = [];
    this.stats = { total: 0, secrets: 0, approvals: 0, violations: 0, medical: 0 };
  }

  /**
   * Scan content for governance events.
   */
  scan(content, context = {}) {
    const findings = [];
    const text = typeof content === 'string' ? content : JSON.stringify(content);

    for (const [category, patterns] of Object.entries(PATTERNS)) {
      for (const { name, regex } of patterns) {
        if (regex.test(text)) {
          findings.push(this._createEvent(category, name, context));
        }
      }
    }

    return findings;
  }

  /**
   * Record a governance event.
   */
  record(event) {
    this.events.push(event);
    this.stats.total++;
    if (event.category === 'secret_detected') this.stats.secrets++;
    if (event.category === 'approval_required') this.stats.approvals++;
    if (event.category === 'policy_violation') this.stats.violations++;
    if (event.category === 'medical_data') this.stats.medical++;

    // Append to audit trail
    try {
      fs.appendFileSync(LOG_FILE, JSON.stringify(event) + '\n');
    } catch {}

    return event;
  }

  /**
   * Scan and auto-record.
   */
  scanAndRecord(content, context = {}) {
    const findings = this.scan(content, context);
    findings.forEach(f => this.record(f));
    return findings;
  }

  _createEvent(category, name, context) {
    return {
      id: `gov_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      category,
      name,
      severity: category === 'secret_detected' || category === 'medical_data' ? 'critical' : 'high',
      agent: context.agent || 'unknown',
      tool: context.tool || 'unknown',
      timestamp: new Date().toISOString(),
      resolved: false,
      resolution: null,
    };
  }

  /**
   * Resolve an event (mark as reviewed).
   */
  resolve(eventId, { resolution, resolvedBy }) {
    const event = this.events.find(e => e.id === eventId);
    if (!event) return { error: 'Event not found' };
    event.resolved = true;
    event.resolution = resolution;
    event.resolvedBy = resolvedBy;
    event.resolvedAt = new Date().toISOString();
    return event;
  }

  /**
   * Get audit trail.
   */
  getTrail(limit = 50) {
    return this.events.slice(-limit);
  }

  /**
   * Get stats.
   */
  getStats() {
    return { ...this.stats, unresolved: this.events.filter(e => !e.resolved).length };
  }

  /**
   * Read full audit trail from file.
   */
  readAuditFile(limit = 100) {
    try {
      const lines = fs.readFileSync(LOG_FILE, 'utf-8').trim().split('\n');
      return lines.slice(-limit).map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
    } catch { return []; }
  }
}

const governance = new GovernanceCapture();

function register() {
  ipcMain.handle('governance:scan', async (event, { content, context }) => governance.scanAndRecord(content, context));
  ipcMain.handle('governance:resolve', async (event, { eventId, resolution, resolvedBy }) => governance.resolve(eventId, { resolution, resolvedBy }));
  ipcMain.handle('governance:trail', async (event, { limit }) => governance.getTrail(limit));
  ipcMain.handle('governance:stats', async () => governance.getStats());
  ipcMain.handle('governance:auditFile', async (event, { limit }) => governance.readAuditFile(limit));
}

module.exports = { register, governance };
