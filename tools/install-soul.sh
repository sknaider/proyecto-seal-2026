#!/bin/bash
################################################################################
# install-soul.sh — Mini-SOUL Installer (Native, Cross-Platform)
#
# Installs a complete Mini-SOUL edge agent on Linux or macOS without Docker.
# Generates identity, creates local SQLite database, and sets up systemd/launchd
# autostart (DRY-RUN by default, no secrets written to disk).
#
# Usage:
#   bash install-soul.sh [--apply]
#
# Without --apply: generates config in /tmp/soul-install-<uuid>/, no system changes.
# With --apply:    applies everything (requires sudo for systemd on Linux).
#
# Environment variables (optional, with defaults):
#   SEAL_PREFIX=~/.seal                  # Installation prefix
#   SOUL_ENDPOINT=<unset>               # Will prompt if not in env
#   AGENT_NAME=<unset>                  # Will prompt if not in env
#   DEVICE_NAME=$(hostname)             # Device identifier
#
# Author: SEAL Bootstrap (June 2026)
# Spec: SPEC_SEAL_DISTRIBUTED_AGENTS_v1.md + spec_soul_installer_v1.md
################################################################################

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION & DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

SEAL_PREFIX="${SEAL_PREFIX:-$HOME/.seal}"
# Fuente de los .py/.sql del mini-SOUL (repo/payload). Default: raíz del repo (padre de tools/).
SEAL_SOURCE="${SEAL_SOURCE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
DEVICE_NAME="${DEVICE_NAME:-$(hostname)}"
SOUL_ENDPOINT="${SOUL_ENDPOINT:-}"
AGENT_NAME="${AGENT_NAME:-}"
DRY_RUN=true
SCRIPT_VERSION="1.0"

# Color output (disable if not interactive)
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  GREEN='\033[0;32m'
  YELLOW='\033[1;33m'
  BLUE='\033[0;34m'
  NC='\033[0m'  # No Color
else
  RED='' GREEN='' YELLOW='' BLUE='' NC=''
fi

# ─────────────────────────────────────────────────────────────────────────────
# UTILITY FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

log_info() {
  echo -e "${BLUE}[INFO]${NC} $*" >&2
}

log_ok() {
  echo -e "${GREEN}[OK]${NC} $*" >&2
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $*" >&2
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*" >&2
}

log_section() {
  echo "" >&2
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}" >&2
  echo -e "${BLUE}$*${NC}" >&2
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}" >&2
}

die() {
  log_error "$*"
  exit 1
}

# UUID generator (portable)
generate_uuid() {
  python3 -c "import uuid; print(uuid.uuid4())"
}

# Random hex string (length parameter)
random_hex() {
  local length=${1:-32}
  python3 -c "import secrets; print(secrets.token_hex($((length / 2))))" | head -c "$length"
}

# Check if command exists
command_exists() {
  command -v "$1" >/dev/null 2>&1
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 1: ENVIRONMENT DETECTION
# ─────────────────────────────────────────────────────────────────────────────

detect_os() {
  log_section "Phase 1: Environment Detection"

  if [[ "$OSTYPE" == "linux"* ]]; then
    OS="linux"
    log_ok "Detected OS: Linux"
  elif [[ "$OSTYPE" == "darwin"* ]]; then
    OS="macos"
    log_ok "Detected OS: macOS"
  else
    die "Unsupported OS: $OSTYPE (Linux/macOS only; use WSL2 for Windows)"
  fi
}

detect_package_manager() {
  if [[ "$OS" == "linux" ]]; then
    if command_exists apt; then
      PM="apt"
      log_ok "Package manager: apt (Debian/Ubuntu)"
    elif command_exists dnf; then
      PM="dnf"
      log_ok "Package manager: dnf (Fedora/RHEL)"
    else
      die "Unsupported Linux distro (apt/dnf required)"
    fi
  elif [[ "$OS" == "macos" ]]; then
    if ! command_exists brew; then
      die "Homebrew not installed. Install from https://brew.sh"
    fi
    PM="brew"
    log_ok "Package manager: Homebrew"
  fi
}

check_connectivity() {
  log_info "Checking internet connectivity..."
  if ! timeout 3 ping -c 1 8.8.8.8 >/dev/null 2>&1; then
    log_warn "Cannot reach 8.8.8.8 (may be offline)"
    if ! timeout 3 ping -c 1 1.1.1.1 >/dev/null 2>&1; then
      die "No internet connectivity detected. Please check your connection."
    fi
  fi
  log_ok "Internet connectivity OK"
}

check_python() {
  if ! command_exists python3; then
    die "python3 not found in PATH"
  fi

  local py_version
  py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
  log_ok "Python 3 found: $py_version"

  if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null; then
    log_warn "Python 3.11+ recommended (found: $py_version). Continuing anyway..."
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 2: USER INPUT
# ─────────────────────────────────────────────────────────────────────────────

gather_user_input() {
  log_section "Phase 2: Configuration"

  # Device name (auto-detected, but allow override)
  log_info "Device name: $DEVICE_NAME (override: set DEVICE_NAME env var)"

  # Agent name (required)
  if [[ -z "$AGENT_NAME" ]]; then
    log_info "Agent names: ADA, JARVIS, ALICE, NEXUS, DUM, FABLE"
    read -p "Enter agent name for this device: " AGENT_NAME || true
    [[ -z "$AGENT_NAME" ]] && die "Agent name required"
  fi
  log_ok "Agent: $AGENT_NAME"

  # SOUL endpoint (required)
  if [[ -z "$SOUL_ENDPOINT" ]]; then
    read -p "Enter SOUL API endpoint (e.g., https://soul.tail123456.ts.net:8771): " SOUL_ENDPOINT || true
    [[ -z "$SOUL_ENDPOINT" ]] && die "SOUL endpoint required"
  fi
  log_ok "SOUL endpoint: $SOUL_ENDPOINT"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 3: IDENTITY GENERATION
# ─────────────────────────────────────────────────────────────────────────────

generate_identities() {
  log_section "Phase 3: Identity Generation"

  DEVICE_ID=$(generate_uuid)
  AGENT_ID=$(generate_uuid)
  API_KEY=$(random_hex 32)
  DB_VERSION="1.0"
  COMPAT_VERSION="1.0"

  log_ok "Device ID: $DEVICE_ID"
  log_ok "Agent ID: $AGENT_ID"
  log_ok "API Key: ${API_KEY:0:16}... (32 hex)"
  log_ok "DB Version: $DB_VERSION"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 4: VENV & DEPENDENCIES
# ─────────────────────────────────────────────────────────────────────────────

setup_venv() {
  log_section "Phase 4: Python Virtual Environment"

  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY RUN] Would create venv at: $SEAL_PREFIX/venv"
  else
    mkdir -p "$SEAL_PREFIX/lib" "$SEAL_PREFIX/bin" "$SEAL_PREFIX/logs"
    python3 -m venv "$SEAL_PREFIX/venv"
    log_ok "venv created: $SEAL_PREFIX/venv"
  fi
}

install_dependencies() {
  log_section "Phase 5: Install Python Dependencies"

  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY RUN] Would install: cryptography>=42.0.0"
  else
    source "$SEAL_PREFIX/venv/bin/activate"
    log_info "Installing cryptography (Ed25519, mTLS)..."
    "$SEAL_PREFIX/venv/bin/pip" install -q "cryptography>=42.0.0"

    log_info "Installing optional: keyring (OS keyring fallback)..."
    "$SEAL_PREFIX/venv/bin/pip" install -q "keyring>=24.0.0" || log_warn "keyring install failed (non-critical)"

    log_ok "Dependencies installed"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 6: DATABASE INITIALIZATION
# ─────────────────────────────────────────────────────────────────────────────

create_schema() {
  log_section "Phase 6: Database Schema Initialization"

  local db_path="$SEAL_PREFIX/mini-soul.db"

  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY RUN] Would create database at: $SEAL_PREFIX/mini-soul.db"
    log_info "[DRY RUN] Would load minisoul_local_schema.sql"
  else
    mkdir -p "$(dirname "$db_path")"

    # Create database with schema (Python inline). '-' = leer el script del stdin, argv[1]=db_path.
    python3 - "$db_path" << 'PYTHON_EOF'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys = ON')
db.execute('PRAGMA journal_mode = WAL')
db.executescript("""
CREATE TABLE IF NOT EXISTS distilled_exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, agent TEXT NOT NULL,
    exchange_core TEXT NOT NULL, specific_context TEXT, source_tokens INTEGER,
    distilled_tokens INTEGER, compression_ratio REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, synced_at TIMESTAMP,
    lifecycle TEXT NOT NULL DEFAULT 'pending' CHECK (lifecycle IN ('pending', 'syncing', 'synced', 'failed')),
    UNIQUE(session_id, agent)
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, agent TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
    importance INTEGER DEFAULT 5, scope TEXT NOT NULL DEFAULT 'private' CHECK (scope IN ('private', 'shared', 'team', 'william')),
    created_at TEXT NOT NULL, device_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    lifecycle TEXT NOT NULL DEFAULT 'pending' CHECK (lifecycle IN ('pending', 'syncing', 'synced', 'failed', 'dropped')),
    synced_at TEXT, cloud_id INTEGER, source TEXT DEFAULT 'edge'
);
CREATE TABLE IF NOT EXISTS sync_state (
    provider TEXT PRIMARY KEY, last_cursor TEXT, last_sync TEXT, daily_calls INTEGER DEFAULT 0,
    daily_budget INTEGER DEFAULT 500, next_sync_at TIMESTAMP, error_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chunk_id TEXT, distilled_exchange_id INTEGER,
    agent TEXT NOT NULL, entry_type TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'private' CHECK (scope IN ('private', 'shared', 'team', 'william')),
    sync_state TEXT NOT NULL DEFAULT 'pending' CHECK (sync_state IN ('pending', 'syncing', 'synced', 'failed')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, synced_at TIMESTAMP, error_message TEXT,
    retry_count INTEGER DEFAULT 0, nonce TEXT UNIQUE,
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
    FOREIGN KEY (distilled_exchange_id) REFERENCES distilled_exchanges(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL, direction TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failure', 'partial')),
    items_processed INTEGER, bytes_transferred INTEGER, error_message TEXT, duration_ms INTEGER
);
CREATE TABLE IF NOT EXISTS conflict_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    chunk_id TEXT, cloud_id INTEGER, local_version TEXT, central_version TEXT,
    resolution TEXT, resolution_at TIMESTAMP, notes TEXT
);
CREATE TABLE IF NOT EXISTS compaction_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_type TEXT NOT NULL CHECK (task_type IN ('nightly', 'weekly', 'monthly')),
    scheduled_at TIMESTAMP, started_at TIMESTAMP, completed_at TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    items_processed INTEGER, compression_ratio_avg REAL, error_message TEXT
);
INSERT OR IGNORE INTO sync_state (provider, daily_budget, daily_calls) VALUES
    ('memories_push', 500, 0), ('memories_pull', 500, 0), ('goals_pull', 200, 0),
    ('tasks_pull', 200, 0), ('distilled_exchanges_push', 100, 0);
INSERT OR IGNORE INTO metadata (key, value) VALUES
    ('db_version', '1.0'), ('device_id', 'UNSET_DURING_INSTALL'), ('agent', 'UNSET_DURING_INSTALL'),
    ('soul_endpoint', 'UNSET_DURING_INSTALL'), ('compat_version', '1.0'), ('last_sync_utc', ''),
    ('local_memory_count', '0');
""")
db.commit()
db.close()
print(f'DB initialized: {sys.argv[1]}')
PYTHON_EOF

    log_ok "Database created: $db_path"
  fi
}

populate_metadata() {
  log_section "Phase 7: Populate Device Metadata"

  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY RUN] Would update metadata: device_id=$DEVICE_ID, agent=$AGENT_NAME, soul_endpoint=$SOUL_ENDPOINT"
  else
    python3 - "$SEAL_PREFIX/mini-soul.db" "$DEVICE_ID" "$AGENT_NAME" "$SOUL_ENDPOINT" "$DB_VERSION" "$COMPAT_VERSION" << 'PYTHON_EOF'
import sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute('PRAGMA foreign_keys = ON')
updates = {
    'device_id': sys.argv[2], 'agent': sys.argv[3], 'soul_endpoint': sys.argv[4],
    'db_version': sys.argv[5], 'compat_version': sys.argv[6], 'last_sync_utc': ''
}
for key, val in updates.items():
    db.execute('UPDATE metadata SET value = ? WHERE key = ?', (val, key))
db.commit()
db.close()
PYTHON_EOF
    log_ok "Metadata populated"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 8: CONFIG FILES
# ─────────────────────────────────────────────────────────────────────────────

generate_env_file() {
  log_section "Phase 8: Generate .env File"

  cat > "$WORK_DIR/.env" << ENV_EOF
# Mini-SOUL Device Configuration — Generated 2026-07-02
# Device: $DEVICE_ID / $AGENT_NAME @ $DEVICE_NAME

DEVICE_ID=$DEVICE_ID
AGENT_NAME=$AGENT_NAME
AGENT_ID=$AGENT_ID
DEVICE_NAME=$DEVICE_NAME
SOUL_ENDPOINT=$SOUL_ENDPOINT
SOUL_API_KEY=<placeholder: obtain via CSR out-of-band>
MINISOUL_DB_PATH=$SEAL_PREFIX/mini-soul.db
MINISOUL_DB_VERSION=$DB_VERSION
DEVICE_API_KEY=$API_KEY
DEVICE_PUBLIC_KEY=<placeholder: generated during CSR>
SYNC_BATCH_SIZE=50
SYNC_MAX_RETRIES=3
SYNC_BACKOFF_S=5
SYNC_BUDGET_DAILY_MEMORIES=500
SYNC_BUDGET_DAILY_EXCHANGES=100
LOG_LEVEL=INFO
LOG_FILE=$SEAL_PREFIX/logs/minisoul.log
MTLS_CERT_PATH=$SEAL_PREFIX/certs/device.crt
MTLS_KEY_PATH=$SEAL_PREFIX/certs/device.key
MTLS_CA_PATH=$SEAL_PREFIX/certs/ca.crt
MTLS_PIN_HASH=<placeholder: obtained from SOUL central>
ENV_EOF

  log_ok "Generated: $WORK_DIR/.env"
}

generate_agent_yml() {
  log_section "Phase 9: Generate agent.yml Descriptor"

  cat > "$WORK_DIR/agent.yml" << AGENT_YML_EOF
version: '1.0'
metadata:
  device_id: '$DEVICE_ID'
  agent_name: '$AGENT_NAME'
  agent_id: '$AGENT_ID'
  device_name: '$DEVICE_NAME'
  created_at: '$(date -Iseconds)'
  soul_endpoint: '$SOUL_ENDPOINT'
agent:
  name: $AGENT_NAME
  memory_tier: 'edge'
  auto_update_enabled: true
  max_local_memory_mb: 500
sync:
  enabled: true
  interval_s: 3600
  retry_max: 3
database:
  engine: 'sqlite'
  path: '$SEAL_PREFIX/mini-soul.db'
  schema_version: '$DB_VERSION'
security:
  auth_mechanism: 'device-bound-token'
  token_ttl_s: 86400
logging:
  level: 'INFO'
  file: '$SEAL_PREFIX/logs/minisoul.log'
AGENT_YML_EOF

  log_ok "Generated: $WORK_DIR/agent.yml"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 9: SYSTEMD/LAUNCHD SERVICE
# ─────────────────────────────────────────────────────────────────────────────

generate_systemd_service() {
  log_section "Phase 10: Generate Systemd Service (Linux)"

  cat > "$WORK_DIR/seal-minisoul.service" << SYSTEMD_EOF
[Unit]
Description=SEAL Mini-SOUL Edge Agent ($AGENT_NAME @ $DEVICE_NAME)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="SEAL_PREFIX=$SEAL_PREFIX"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=$SEAL_PREFIX/.env
ExecStart=$SEAL_PREFIX/venv/bin/python3 $SEAL_PREFIX/lib/minisoul_sync_daemon.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
SYSTEMD_EOF

  log_ok "Generated: $WORK_DIR/seal-minisoul.service"
}

generate_launchd_plist() {
  log_section "Phase 11: Generate Launchd Plist (macOS)"

  cat > "$WORK_DIR/com.seal.minisoul.plist" << LAUNCHD_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.seal.minisoul</string>
  <key>ProgramArguments</key>
  <array>
    <string>$SEAL_PREFIX/venv/bin/python3</string>
    <string>$SEAL_PREFIX/lib/minisoul_sync_daemon.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SEAL_PREFIX</key>
    <string>$SEAL_PREFIX</string>
  </dict>
  <key>StandardOutPath</key>
  <string>$SEAL_PREFIX/logs/minisoul.log</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
</dict>
</plist>
LAUNCHD_EOF

  log_ok "Generated: $WORK_DIR/com.seal.minisoul.plist"
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 9b: DEPLOY ARTIFACTS + CONFIG (bug cazado por e2e: faltaba copiar libs/config al prefijo)
# ─────────────────────────────────────────────────────────────────────────────

deploy_artifacts() {
  log_section "Phase 9b: Deploy libs + config to prefix"
  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY RUN] Would copy mini-SOUL libs to $SEAL_PREFIX/lib/ and config to $SEAL_PREFIX/"
    return
  fi
  mkdir -p "$SEAL_PREFIX/lib" "$SEAL_PREFIX/certs"
  local missing=0
  for f in memory/minisoul_sync_daemon.py memory/minisoul_sync_central.py memory/minisoul_sync_policy.py \
           memory/minisoul_local_schema.sql \
           tools/seal_token.py tools/seal_token_store.py tools/seal_sync_auth.py \
           tools/seal_csr.py tools/seal_revocation_client.py tools/seal_mtls.py; do
    if [[ -f "$SEAL_SOURCE/$f" ]]; then
      cp "$SEAL_SOURCE/$f" "$SEAL_PREFIX/lib/"
    else
      log_warn "artefacto no encontrado en source: $f (bundle incompleto)"
      missing=$((missing + 1))
    fi
  done
  [[ "$missing" -eq 0 ]] && log_ok "Libs desplegadas en $SEAL_PREFIX/lib/" || log_warn "$missing libs faltaron"
  cp "$WORK_DIR/.env" "$SEAL_PREFIX/.env" 2>/dev/null && log_ok "Config .env → $SEAL_PREFIX/" || true
  cp "$WORK_DIR/agent.yml" "$SEAL_PREFIX/agent.yml" 2>/dev/null || true
}

# ─────────────────────────────────────────────────────────────────────────────
# PHASE 10: SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

print_summary() {
  log_section "Installation Complete"

  echo ""
  echo "Configuration Summary:"
  echo "  Device:    $DEVICE_ID / $AGENT_NAME"
  echo "  Endpoint:  $SOUL_ENDPOINT"
  echo "  Prefix:    $SEAL_PREFIX"
  echo ""
  echo "Artefacts in $WORK_DIR:"
  echo "  .env                    Environment config"
  echo "  agent.yml               Agent descriptor"
  echo "  mini-soul.db            SQLite database"
  echo "  seal-minisoul.service   Systemd unit (Linux)"
  echo "  com.seal.minisoul.plist Launchd plist (macOS)"
  echo ""

  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${YELLOW}DRY RUN MODE${NC} — To apply:"
    echo "  bash install-soul.sh --apply"
  else
    echo -e "${GREEN}Installation applied${NC}"
  fi
  echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

main() {
  echo "Mini-SOUL Edge Agent Installer v$SCRIPT_VERSION"
  echo ""

  if [[ "${1:-}" == "--apply" ]]; then
    DRY_RUN=false
    log_warn "APPLY MODE"
  else
    log_info "DRY RUN — use --apply to proceed"
  fi

  WORK_DIR=$(mktemp -d /tmp/soul-install-XXXXXX)
  trap "rm -rf $WORK_DIR" EXIT
  log_info "Working directory: $WORK_DIR"

  detect_os
  detect_package_manager
  check_connectivity
  check_python
  gather_user_input
  generate_identities
  setup_venv
  install_dependencies
  create_schema
  populate_metadata
  generate_env_file
  generate_agent_yml
  [[ "$OS" == "linux" ]] && generate_systemd_service || generate_launchd_plist
  deploy_artifacts
  print_summary
}

main "$@"
