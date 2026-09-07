-- Mini-SOUL Local Schema — FIX aplicado (verificación adversarial JARVIS-orquestador):
-- El loader DEBE abrir la conexión con estos PRAGMA (FK OFF por defecto en SQLite = bug #2/#3).
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- Mini-SOUL Local Database Schema (SQLite)
-- Path: ~/.seal/mini-soul.db
-- Version: 1.0 (June 2026)
-- Status: Implementation-ready DDL for SPEC_SEAL_DISTRIBUTED_AGENTS_v1

-- ============================================================================
-- CORE DISTILLED EXCHANGES TABLE
-- Compressed session summaries after nightly compaction (11:1 ratio target)
-- Syncs to central soul_v3.distilled_exchanges on importance ≥ 7
-- ============================================================================
CREATE TABLE IF NOT EXISTS distilled_exchanges (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL,                    -- unique session identifier
    agent               TEXT NOT NULL,                    -- ADA, JARVIS, ALICE, NEXUS, DUM, FABLE
    exchange_core       TEXT NOT NULL,                    -- JSON: messages + decisions
    specific_context    TEXT,                             -- JSON: room_assignments + technical tags
    source_tokens       INTEGER,                          -- token count before compression
    distilled_tokens    INTEGER,                          -- token count after compression
    compression_ratio   REAL,                             -- source_tokens / distilled_tokens (target ~11)
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- when distilled locally
    synced_at           TIMESTAMP,                        -- NULL until successfully synced to central
    lifecycle           TEXT NOT NULL DEFAULT 'pending'  -- pending, syncing, synced, failed
                        CHECK (lifecycle IN ('pending', 'syncing', 'synced', 'failed')),
    UNIQUE(session_id, agent)
);

CREATE INDEX IF NOT EXISTS idx_distilled_exchanges_lifecycle
    ON distilled_exchanges(lifecycle);
CREATE INDEX IF NOT EXISTS idx_distilled_exchanges_created_at
    ON distilled_exchanges(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_distilled_exchanges_agent
    ON distilled_exchanges(agent);

-- ============================================================================
-- CHUNKS TABLE (extended from edge_layer.py)
-- Core memory units with scope enforcement and device lifecycle
-- Content-addressed IDs: SHA256(agent+content)[:32]
-- ============================================================================
CREATE TABLE IF NOT EXISTS chunks (
    id              TEXT PRIMARY KEY,                    -- content-addressed: SHA256(agent+content)[:32]
    agent           TEXT NOT NULL,                       -- ADA, JARVIS, ALICE, NEXUS, DUM, FABLE
    category        TEXT NOT NULL,                       -- memory type (milestone, decision, correction, etc.)
    content         TEXT NOT NULL,                       -- actual memory text
    importance      INTEGER DEFAULT 5,                   -- 1-10 scale; ≥7 triggers sync
    scope           TEXT NOT NULL DEFAULT 'private'  -- private, shared, team, william
                    CHECK (scope IN ('private', 'shared', 'team', 'william')),
    created_at      TEXT NOT NULL,                       -- ISO8601 timestamp (local)
    device_created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- device-level created_at
    lifecycle       TEXT NOT NULL DEFAULT 'pending'  -- pending, syncing, synced, failed, dropped
                    CHECK (lifecycle IN ('pending', 'syncing', 'synced', 'failed', 'dropped')),
    synced_at       TEXT,                                -- ISO8601 timestamp when synced to central
    cloud_id        INTEGER,                             -- soul_v3.memories.id after sync
    source          TEXT DEFAULT 'edge'                  -- 'edge' (local), 'central' (pulled from cloud)
);

CREATE INDEX IF NOT EXISTS idx_chunks_agent
    ON chunks(agent);
CREATE INDEX IF NOT EXISTS idx_chunks_lifecycle
    ON chunks(lifecycle);
CREATE INDEX IF NOT EXISTS idx_chunks_importance
    ON chunks(importance DESC);
CREATE INDEX IF NOT EXISTS idx_chunks_scope
    ON chunks(scope);
CREATE INDEX IF NOT EXISTS idx_chunks_created_at
    ON chunks(created_at DESC);

-- ============================================================================
-- SYNC STATE TABLE
-- Tracks last sync cursor and budget per provider (memories, goals, tasks, etc.)
-- Prevents budget exhaustion and enables resume on reconnect
-- ============================================================================
CREATE TABLE IF NOT EXISTS sync_state (
    provider        TEXT PRIMARY KEY,                    -- 'memories', 'goals', 'tasks', 'distilled_exchanges'
    last_cursor     TEXT,                                -- last synced ID / timestamp
    last_sync       TEXT,                                -- ISO8601 timestamp of last successful sync
    daily_calls     INTEGER DEFAULT 0,                   -- API calls made today (resets at midnight)
    daily_budget    INTEGER DEFAULT 500,                 -- max SOUL API calls per provider per day
    next_sync_at    TIMESTAMP,                           -- when next sync should occur (for scheduling)
    error_count     INTEGER DEFAULT 0                    -- consecutive sync failures (reset on success)
);

-- ============================================================================
-- METADATA TABLE
-- Device identity, versioning, and sync coordination
-- ============================================================================
CREATE TABLE IF NOT EXISTS metadata (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Metadata keys that MUST be set during installer:
-- - device_id:          UUID unique per device (set during install)
-- - db_version:         Schema version (currently 1.0)
-- - last_sync_utc:      ISO8601 timestamp of last successful sync to central
-- - local_memory_count: COUNT(*) FROM chunks (informational)
-- - compat_version:     SOUL API protocol version (for negotiation)
-- - device_public_key:  Ed25519 public key for token verification (optional, for offline token validation)
-- - agent:              Agent name running on this device (ADA, JARVIS, etc.)
-- - soul_endpoint:      URL of central SOUL API (e.g., https://soul.tail123456.ts.net:8771)

-- ============================================================================
-- OFFLINE QUEUE TABLE
-- Outbox for chunks pending sync (importance ≥ 7, categories, or full distilled_exchanges)
-- Push to central when online; tracks state for retry logic
-- ============================================================================
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id        TEXT,                                -- references chunks.id (NULL if distilled_exchanges)
    distilled_exchange_id INTEGER,                       -- references distilled_exchanges.id (NULL if chunk)
    agent           TEXT NOT NULL,                       -- agent that created this entry
    entry_type      TEXT NOT NULL,                       -- 'chunk' or 'distilled_exchange'
    scope           TEXT NOT NULL DEFAULT 'private'  -- private, shared, team, william
                    CHECK (scope IN ('private', 'shared', 'team', 'william')),
    sync_state      TEXT NOT NULL DEFAULT 'pending'  -- pending, syncing, synced, failed
                    CHECK (sync_state IN ('pending', 'syncing', 'synced', 'failed')),
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    synced_at       TIMESTAMP,
    error_message   TEXT,                                -- last error if sync_state='failed'
    retry_count     INTEGER DEFAULT 0,                   -- incremented on each retry
    nonce           TEXT UNIQUE,                         -- UUID for dedup and idempotency
    FOREIGN KEY (chunk_id) REFERENCES chunks(id) ON DELETE CASCADE,
    FOREIGN KEY (distilled_exchange_id) REFERENCES distilled_exchanges(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_outbox_sync_state
    ON outbox(sync_state);
CREATE INDEX IF NOT EXISTS idx_outbox_agent
    ON outbox(agent);
CREATE INDEX IF NOT EXISTS idx_outbox_created_at
    ON outbox(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_outbox_scope
    ON outbox(scope);

-- ============================================================================
-- SYNC LOG TABLE
-- Audit trail of all sync operations (success/failure, bytes transferred, etc.)
-- Useful for debugging and verifying sync completeness
-- ============================================================================
CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider        TEXT NOT NULL,                       -- 'memories_push', 'memories_pull', etc.
    direction       TEXT NOT NULL,                       -- 'push' or 'pull'
    status          TEXT NOT NULL  -- 'success', 'failure', 'partial'
                    CHECK (status IN ('success', 'failure', 'partial')),
    items_processed INTEGER,                             -- number of chunks/exchanges processed
    bytes_transferred INTEGER,                           -- bytes up/down
    error_message   TEXT,                                -- error text if status != 'success'
    duration_ms     INTEGER                              -- milliseconds taken
);

CREATE INDEX IF NOT EXISTS idx_sync_log_timestamp
    ON sync_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sync_log_provider
    ON sync_log(provider);
CREATE INDEX IF NOT EXISTS idx_sync_log_status
    ON sync_log(status);

-- ============================================================================
-- CONFLICT LOG TABLE
-- Records divergences when offline writes conflict with central updates
-- Used by merge arbitrator (LLM or FAIL-CLOSED strategy)
-- ============================================================================
CREATE TABLE IF NOT EXISTS conflict_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    chunk_id        TEXT,                                -- local chunk that conflicted
    cloud_id        INTEGER,                             -- central memory.id that conflicts
    local_version   TEXT,                                -- JSON: {importance, modified_at, content_hash}
    central_version TEXT,                                -- JSON: {importance, modified_at, content_hash}
    resolution      TEXT,                                -- 'central_won', 'local_kept', 'merged', 'awaiting_review'
    resolution_at   TIMESTAMP,                           -- when conflict was resolved
    notes           TEXT                                 -- LLM explanation or manual note
);

CREATE INDEX IF NOT EXISTS idx_conflict_log_timestamp
    ON conflict_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_conflict_log_resolution
    ON conflict_log(resolution);

-- ============================================================================
-- COMPACTION SCHEDULE TABLE
-- Tracks when compaction tasks run (nightly, weekly, monthly)
-- Prevents double-runs and allows resumption after interruptions
-- ============================================================================
CREATE TABLE IF NOT EXISTS compaction_schedule (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type       TEXT NOT NULL  -- 'nightly', 'weekly', 'monthly'
                    CHECK (task_type IN ('nightly', 'weekly', 'monthly')),
    scheduled_at    TIMESTAMP,                           -- when task was supposed to run
    started_at      TIMESTAMP,                           -- when task actually started
    completed_at    TIMESTAMP,                           -- when task finished (NULL if in-progress or failed)
    status          TEXT NOT NULL DEFAULT 'pending'  -- pending, running, completed, failed
                    CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    items_processed INTEGER,                             -- chunks/exchanges processed
    compression_ratio_avg REAL,                          -- average compression achieved
    error_message   TEXT                                 -- error if status='failed'
);

CREATE INDEX IF NOT EXISTS idx_compaction_schedule_task_type
    ON compaction_schedule(task_type);
CREATE INDEX IF NOT EXISTS idx_compaction_schedule_status
    ON compaction_schedule(status);
CREATE INDEX IF NOT EXISTS idx_compaction_schedule_completed_at
    ON compaction_schedule(completed_at DESC);

-- ============================================================================
-- INITIALIZATION VIEWS
-- ============================================================================

-- View: pending_outbox
-- Shows all outbox entries ready to sync
CREATE VIEW IF NOT EXISTS v_pending_outbox AS
    SELECT id, chunk_id, distilled_exchange_id, agent, entry_type, scope,
           created_at, retry_count, nonce
    FROM outbox
    WHERE sync_state = 'pending'
    ORDER BY created_at ASC;

-- View: local_memory_stats
-- Summary stats: total chunks, pending count, compression ratio
CREATE VIEW IF NOT EXISTS v_local_memory_stats AS
    SELECT
        (SELECT COUNT(*) FROM chunks) as total_chunks,
        (SELECT COUNT(*) FROM chunks WHERE lifecycle='pending') as pending_chunks,
        (SELECT COUNT(*) FROM distilled_exchanges) as total_digests,
        (SELECT COUNT(*) FROM distilled_exchanges WHERE lifecycle='pending') as pending_digests,
        (SELECT AVG(compression_ratio) FROM distilled_exchanges WHERE compression_ratio IS NOT NULL) as avg_compression_ratio,
        (SELECT COUNT(*) FROM outbox WHERE sync_state='failed') as failed_syncs
    ;

-- View: sync_health
-- Shows last successful sync and time since
CREATE VIEW IF NOT EXISTS v_sync_health AS
    SELECT
        provider,
        last_sync,
        MAX(CAST((julianday('now') - julianday(last_sync)) * 24 * 60 AS INTEGER)) as minutes_since_sync,
        daily_calls,
        daily_budget
    FROM sync_state
    GROUP BY provider
    ;

-- ============================================================================
-- CONSTRAINTS & DEFAULTS
-- ============================================================================

-- Prevent chunks with importance < 1 or > 10
CREATE TRIGGER IF NOT EXISTS trg_validate_chunk_importance
BEFORE INSERT ON chunks
BEGIN
    SELECT CASE
        WHEN NEW.importance < 1 OR NEW.importance > 10 THEN
            RAISE(ABORT, 'importance must be between 1 and 10')
    END;
END;

-- Auto-update device_created_at on chunk insert (if not set)
CREATE TRIGGER IF NOT EXISTS trg_chunk_device_created_at
BEFORE INSERT ON chunks
WHEN NEW.device_created_at IS NULL
BEGIN
    UPDATE chunks SET device_created_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ============================================================================
-- INITIALIZATION SCRIPT
-- Run once during installer setup
-- ============================================================================

-- Insert default sync state entries
INSERT OR IGNORE INTO sync_state (provider, daily_budget, daily_calls)
VALUES
    ('memories_push', 500, 0),
    ('memories_pull', 500, 0),
    ('goals_pull', 200, 0),
    ('tasks_pull', 200, 0),
    ('distilled_exchanges_push', 100, 0);

-- Metadata skeleton (installer must fill in device-specific values)
INSERT OR IGNORE INTO metadata (key, value)
VALUES
    ('db_version', '1.0'),
    ('device_id', 'UNSET_DURING_INSTALL'),
    ('agent', 'UNSET_DURING_INSTALL'),
    ('soul_endpoint', 'UNSET_DURING_INSTALL'),
    ('compat_version', '1.0'),
    ('last_sync_utc', ''),
    ('local_memory_count', '0');

-- ============================================================================
-- END OF SCHEMA
-- ============================================================================
