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
