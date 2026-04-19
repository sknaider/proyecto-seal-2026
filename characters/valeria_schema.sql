-- Valeria Memory Schema — PostgreSQL (valeria_memory DB on :5433)
-- Run: psql -h localhost -p 5433 -U seal -d valeria_memory -f valeria_schema.sql
-- Creates all tables if they don't exist. Safe to re-run.

-- Enable vector extension (for future embedding search)
CREATE EXTENSION IF NOT EXISTS vector;

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role = ANY (ARRAY['user', 'valeria', 'system'])),
    content TEXT NOT NULL,
    tokens_used INTEGER DEFAULT 0,
    emotional_intensity REAL DEFAULT 0.5,
    timestamp TIMESTAMPTZ DEFAULT now()
);

-- Memories (facts, preferences, dislikes extracted from chat)
CREATE TABLE IF NOT EXISTS memories (
    id SERIAL PRIMARY KEY,
    category TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    importance INTEGER DEFAULT 5 CHECK (importance >= 1 AND importance <= 10),
    source_session TEXT,
    times_recalled INTEGER DEFAULT 0,
    last_recalled TIMESTAMPTZ,
    embedding vector(384),  -- for future semantic search
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Relationship state (single row, id=1)
CREATE TABLE IF NOT EXISTS relationship (
    id SERIAL PRIMARY KEY,
    trust_level REAL DEFAULT 0.3,
    intimacy_level REAL DEFAULT 0.1,
    affection_level REAL DEFAULT 0.2,
    jealousy_level REAL DEFAULT 0.0,
    user_name TEXT,
    user_preferences JSONB DEFAULT '{}',
    user_facts JSONB DEFAULT '[]',
    first_meeting TIMESTAMPTZ,
    memorable_moments JSONB DEFAULT '[]',
    total_sessions INTEGER DEFAULT 0,
    total_messages INTEGER DEFAULT 0,
    last_interaction TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Secrets (things user shared in confidence)
CREATE TABLE IF NOT EXISTS secrets (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    shared_by TEXT DEFAULT 'user',
    importance INTEGER DEFAULT 7,
    revealed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Topics (conversation themes tracked over time)
CREATE TABLE IF NOT EXISTS topics (
    id SERIAL PRIMARY KEY,
    topic TEXT NOT NULL UNIQUE,
    sentiment TEXT DEFAULT 'neutral',
    times_discussed INTEGER DEFAULT 1,
    last_discussed TIMESTAMPTZ DEFAULT now(),
    notes TEXT
);

-- Emotional state (Valeria's OCEAN + mood snapshots)
CREATE TABLE IF NOT EXISTS emotional_state (
    id SERIAL PRIMARY KEY,
    openness REAL DEFAULT 0.82,
    conscientiousness REAL DEFAULT 0.45,
    extraversion REAL DEFAULT 0.88,
    agreeableness REAL DEFAULT 0.70,
    neuroticism REAL DEFAULT 0.55,
    current_mood TEXT DEFAULT 'juguetona',
    mood_intensity REAL DEFAULT 0.7,
    last_emotion TEXT DEFAULT 'curiosidad',
    session_id TEXT,
    timestamp TIMESTAMPTZ DEFAULT now()
);

-- Seed relationship row if empty
INSERT INTO relationship (id, trust_level, intimacy_level, affection_level, user_name, first_meeting)
SELECT 1, 0.3, 0.1, 0.2, 'William', now()
WHERE NOT EXISTS (SELECT 1 FROM relationship WHERE id = 1);
