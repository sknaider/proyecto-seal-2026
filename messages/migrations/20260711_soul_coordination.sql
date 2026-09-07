BEGIN;

SELECT pg_advisory_xact_lock(x'5EA20260711'::bigint);

CREATE TABLE IF NOT EXISTS soul_v3.schema_migrations (
    migration_name  TEXT PRIMARY KEY,
    migration_hash  TEXT NOT NULL,
    actor           TEXT NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- SOUL Coordination: durable ownership and internal contribution state.
-- Additive migration. The legacy response_lease remains available for rollback.
CREATE TABLE IF NOT EXISTS soul_v3.coordination_turns (
    source_message_id   TEXT PRIMARY KEY,
    requester           TEXT NOT NULL,
    request_hash        TEXT NOT NULL,
    mode                TEXT NOT NULL CHECK (
        mode IN ('social', 'discussion', 'execution', 'roundtable', 'direct')
    ),
    lead_agent          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'assigned' CHECK (
        status IN (
            'assigned', 'acknowledged', 'running', 'synthesizing',
            'completed', 'blocked', 'expired', 'cancelled'
        )
    ),
    policy_version      INTEGER NOT NULL DEFAULT 1,
    version             INTEGER NOT NULL DEFAULT 1,
    ack_deadline        TIMESTAMPTZ,
    result_deadline     TIMESTAMPTZ,
    final_message_id    TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS soul_v3.coordination_assignments (
    source_message_id   TEXT NOT NULL REFERENCES soul_v3.coordination_turns(source_message_id),
    agent               TEXT NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('lead', 'contributor', 'speaker')),
    public_write        BOOLEAN NOT NULL DEFAULT false,
    status              TEXT NOT NULL DEFAULT 'assigned' CHECK (
        status IN ('assigned', 'accepted', 'working', 'submitted', 'done', 'declined', 'expired')
    ),
    task_id             BIGINT REFERENCES soul_v3.agent_tasks(id) ON DELETE SET NULL,
    lease_until         TIMESTAMPTZ,
    last_heartbeat      TIMESTAMPTZ,
    evidence            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_message_id, agent)
);

-- One-time server-verifiable grants for public agent speech. A grant is not
-- authority to execute tools; it only authorizes one correlated public write.
CREATE TABLE IF NOT EXISTS soul_v3.coordination_voice_grants (
    grant_id            TEXT PRIMARY KEY,
    source_message_id   TEXT NOT NULL REFERENCES soul_v3.coordination_turns(source_message_id),
    agent               TEXT NOT NULL,
    purpose             TEXT NOT NULL CHECK (purpose ~ '^[a-z][a-z0-9_]{0,31}$'),
    expires_at          TIMESTAMPTZ NOT NULL,
    consumed_at         TIMESTAMPTZ,
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT coordination_voice_grant_expiry_check CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS coordination_turns_open_deadline_idx
    ON soul_v3.coordination_turns (status, result_deadline)
    WHERE status IN ('assigned', 'acknowledged', 'running', 'synthesizing');

CREATE INDEX IF NOT EXISTS coordination_assignments_agent_open_idx
    ON soul_v3.coordination_assignments (agent, status, updated_at DESC)
    WHERE status IN ('assigned', 'accepted', 'working', 'submitted');

CREATE INDEX IF NOT EXISTS coordination_voice_grants_open_idx
    ON soul_v3.coordination_voice_grants (source_message_id, agent, expires_at)
    WHERE consumed_at IS NULL;

CREATE OR REPLACE FUNCTION soul_v3.coordination_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_coordination_turns_touch ON soul_v3.coordination_turns;
CREATE TRIGGER trg_coordination_turns_touch
BEFORE UPDATE ON soul_v3.coordination_turns
FOR EACH ROW EXECUTE FUNCTION soul_v3.coordination_touch_updated_at();

DROP TRIGGER IF EXISTS trg_coordination_assignments_touch ON soul_v3.coordination_assignments;
CREATE TRIGGER trg_coordination_assignments_touch
BEFORE UPDATE ON soul_v3.coordination_assignments
FOR EACH ROW EXECUTE FUNCTION soul_v3.coordination_touch_updated_at();

COMMENT ON TABLE soul_v3.coordination_turns IS
    'Durable SOUL coordination state: one request, explicit lead, deadlines and final correlation.';
COMMENT ON TABLE soul_v3.coordination_assignments IS
    'Per-agent internal roles and evidence; contributors do not gain public writer authority.';
COMMENT ON TABLE soul_v3.coordination_voice_grants IS
    'Short-lived one-time grants for correlated public writes; never tool-execution authority.';

-- Least-privilege rollout roles are optional across installations.  Writes
-- now go exclusively through the bounded coordination_write function created
-- by the authority-hardening migration.  Reapplying this foundational schema
-- must never resurrect its old direct-DML bootstrap privileges.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_bus') THEN
        GRANT SELECT ON
            soul_v3.coordination_turns,
            soul_v3.coordination_assignments,
            soul_v3.coordination_voice_grants
        TO pr_bus;
        REVOKE INSERT, UPDATE, DELETE ON
            soul_v3.coordination_turns,
            soul_v3.coordination_assignments,
            soul_v3.coordination_voice_grants
        FROM pr_bus;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_bus_admin') THEN
        REVOKE INSERT, UPDATE, DELETE ON
            soul_v3.coordination_turns,
            soul_v3.coordination_assignments,
            soul_v3.coordination_voice_grants
        FROM pr_bus_admin;
    END IF;
END;
$$;

DO $$
DECLARE
    _name CONSTANT TEXT := '20260711_soul_coordination';
    -- SHA-256 of the immutable contract identifier
    -- "20260711_soul_coordination:v1" (not a recursive hash of this file).
    _hash CONSTANT TEXT := '298af8261026357dc4c9aa94ea8d990e7028794fc9a06131491965fffb3982a8';
    _existing TEXT;
BEGIN
    SELECT migration_hash INTO _existing
      FROM soul_v3.schema_migrations WHERE migration_name = _name;
    IF _existing IS NOT NULL AND _existing <> _hash THEN
        RAISE EXCEPTION 'migration ledger hash mismatch for %', _name;
    END IF;
    INSERT INTO soul_v3.schema_migrations (migration_name, migration_hash, actor)
    VALUES (_name, _hash, 'ADA')
    ON CONFLICT (migration_name) DO NOTHING;
END;
$$;

COMMIT;
