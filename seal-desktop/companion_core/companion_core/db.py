import aiosqlite
from companion_core.settings import db_path

_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    category TEXT,
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 5,
    embedding BLOB,
    created_at TEXT DEFAULT (datetime('now'))
)""",
    """CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    agent UNINDEXED,
    category UNINDEXED,
    content='memories',
    content_rowid='id'
)""",
    """CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content, agent, category)
    VALUES (new.id, new.content, new.agent, new.category);
END""",
    """CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, agent, category)
        VALUES('delete', old.id, old.content, old.agent, old.category);
    INSERT INTO memories_fts(rowid, content, agent, category)
        VALUES (new.id, new.content, new.agent, new.category);
END""",
    """CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content, agent, category)
        VALUES('delete', old.id, old.content, old.agent, old.category);
END""",
    """CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content TEXT NOT NULL,
    model TEXT,
    ts TEXT DEFAULT (datetime('now'))
)""",
    "CREATE INDEX IF NOT EXISTS conversations_thread_idx ON conversations(thread_id, ts)",
    """CREATE TABLE IF NOT EXISTS companion_settings (
    key TEXT PRIMARY KEY,
    value TEXT
)""",
    """CREATE TABLE IF NOT EXISTS integrations (
    id TEXT PRIMARY KEY,
    token_encrypted BLOB,
    nonce BLOB,
    connected_at TEXT DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    trigger_phrase TEXT,
    prompt_template TEXT NOT NULL,
    category TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','completed','archived')),
    priority INTEGER NOT NULL DEFAULT 5,
    due_date TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '[]',
    env TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    tools_cache TEXT,
    added_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS agent_profile (
    id INTEGER PRIMARY KEY DEFAULT 1,
    name TEXT NOT NULL DEFAULT 'Companion',
    persona_description TEXT,
    ocean_o REAL NOT NULL DEFAULT 0.7,
    ocean_c REAL NOT NULL DEFAULT 0.6,
    ocean_e REAL NOT NULL DEFAULT 0.5,
    ocean_a REAL NOT NULL DEFAULT 0.8,
    ocean_n REAL NOT NULL DEFAULT 0.2,
    valence REAL NOT NULL DEFAULT 0.6,
    arousal REAL NOT NULL DEFAULT 0.4,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    "INSERT OR IGNORE INTO agent_profile (id) VALUES (1)",
    """CREATE TABLE IF NOT EXISTS user_profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    # ── SEAL App Sprint 1 ported (PostgreSQL soul_v3 → SQLite local) ──
    """CREATE TABLE IF NOT EXISTS daily_dreams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    date TEXT NOT NULL,
    cycle TEXT NOT NULL CHECK (cycle IN ('morning','midday','evening','nocturnal')),
    dream_narrative TEXT NOT NULL,
    key_events TEXT DEFAULT '[]',
    emotional_arc TEXT DEFAULT '{}',
    learnings TEXT DEFAULT '[]',
    pending_threads TEXT DEFAULT '[]',
    model_used TEXT,
    source_memory_ids TEXT DEFAULT '[]',
    inject_to_prompt INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, date, cycle)
)""",
    "CREATE INDEX IF NOT EXISTS daily_dreams_inject_idx ON daily_dreams (agent, inject_to_prompt, date DESC)",

    """CREATE TABLE IF NOT EXISTS llm_routing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('reasoning','agentic','coding','summary')),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    fallback_provider TEXT,
    fallback_model TEXT,
    max_tokens INTEGER NOT NULL DEFAULT 4096,
    temperature REAL NOT NULL DEFAULT 0.7,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, role)
)""",
    # Seed DEFAULT routing — local Gemma 4 first, no cloud markup
    "INSERT OR IGNORE INTO llm_routing (agent, role, provider, model, fallback_provider, fallback_model) VALUES ('DEFAULT', 'reasoning', 'ollama', 'gemma3:12b', 'ollama', 'llama3.1:8b')",
    "INSERT OR IGNORE INTO llm_routing (agent, role, provider, model, fallback_provider, fallback_model) VALUES ('DEFAULT', 'agentic', 'ollama', 'gemma3:12b', 'ollama', 'llama3.1:8b')",
    "INSERT OR IGNORE INTO llm_routing (agent, role, provider, model, fallback_provider, fallback_model) VALUES ('DEFAULT', 'coding', 'ollama', 'qwen2.5-coder:7b', 'ollama', 'gemma3:12b')",
    "INSERT OR IGNORE INTO llm_routing (agent, role, provider, model, fallback_provider, fallback_model) VALUES ('DEFAULT', 'summary', 'ollama', 'gemma3:4b', 'ollama', 'llama3.2:3b')",

    """CREATE TABLE IF NOT EXISTS companion_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    channel TEXT,
    action TEXT NOT NULL,
    target_id TEXT,
    metadata TEXT,
    processed_locally INTEGER NOT NULL DEFAULT 1,
    provider_used TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    "CREATE INDEX IF NOT EXISTS audit_log_recent_idx ON companion_audit_log (created_at DESC)",

    """CREATE TABLE IF NOT EXISTS memory_tree (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('hour','day','month','year')),
    bucket_start TEXT NOT NULL,
    bucket_end TEXT NOT NULL,
    summary TEXT NOT NULL,
    child_ids TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (agent, level, bucket_start)
)""",
    "CREATE INDEX IF NOT EXISTS memory_tree_lookup_idx ON memory_tree (agent, level, bucket_start DESC)",

    """CREATE TABLE IF NOT EXISTS agent_capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL UNIQUE,
    cap_shell_commands INTEGER NOT NULL DEFAULT 0,
    cap_git INTEGER NOT NULL DEFAULT 0,
    cap_read_files INTEGER NOT NULL DEFAULT 1,
    cap_write_files INTEGER NOT NULL DEFAULT 0,
    cap_screen_capture INTEGER NOT NULL DEFAULT 0,
    cap_camera INTEGER NOT NULL DEFAULT 0,
    cap_web_search INTEGER NOT NULL DEFAULT 1,
    cap_browser_control INTEGER NOT NULL DEFAULT 0,
    cap_memory_read INTEGER NOT NULL DEFAULT 1,
    cap_memory_write INTEGER NOT NULL DEFAULT 1,
    cap_cron_jobs INTEGER NOT NULL DEFAULT 0,
    cap_notifications INTEGER NOT NULL DEFAULT 1,
    cap_channel_read INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    # Default capabilities for SEAL App user-product agent (TODO OFF except basics)
    "INSERT OR IGNORE INTO agent_capabilities (agent) VALUES ('SOUL')",

    """CREATE TABLE IF NOT EXISTS user_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('nerves_fire','governance_challenge','reflective_diagnosis','system_alert','integration_event','custom')),
    severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('critical','warning','info','success')),
    title TEXT NOT NULL,
    body TEXT,
    source_id INTEGER,
    source_table TEXT,
    read INTEGER NOT NULL DEFAULT 0,
    dismissed INTEGER NOT NULL DEFAULT 0,
    action_url TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT
)""",
    "CREATE INDEX IF NOT EXISTS notifs_unread_idx ON user_notifications (agent, read, created_at DESC) WHERE dismissed = 0",

    """CREATE TABLE IF NOT EXISTS cron_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    agent TEXT,
    cron_expression TEXT NOT NULL,
    handler TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS cron_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT CHECK (status IN ('running','success','failed')),
    error_message TEXT,
    output_summary TEXT,
    FOREIGN KEY (job_id) REFERENCES cron_jobs(id) ON DELETE CASCADE
)""",
    "CREATE INDEX IF NOT EXISTS cron_runs_job_idx ON cron_runs (job_id, started_at DESC)",
]

_db_conn: aiosqlite.Connection | None = None


async def init_db() -> None:
    global _db_conn
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_conn = await aiosqlite.connect(str(path))
    _db_conn.row_factory = aiosqlite.Row
    for stmt in _STATEMENTS:
        await _db_conn.execute(stmt)
    await _db_conn.commit()


async def close_db() -> None:
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None


def get_db() -> aiosqlite.Connection:
    if _db_conn is None:
        raise RuntimeError("DB not initialised — call init_db() first")
    return _db_conn
