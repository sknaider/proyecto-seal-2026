-- SSAI v1 — production SHADOW registry and DUAL_VERIFY audit spine.
-- Additive only: soul_v3.identity remains the source of truth until ENFORCE gates pass.
BEGIN;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';

CREATE TABLE IF NOT EXISTS soul_v3.ssai_registry (
    id                  BIGSERIAL PRIMARY KEY,
    agent               TEXT NOT NULL UNIQUE,
    soul_id             UUID NOT NULL UNIQUE,
    soul_dni            TEXT NOT NULL UNIQUE,
    lifecycle_state     TEXT NOT NULL DEFAULT 'candidate'
                        CHECK (lifecycle_state IN ('candidate', 'active', 'quarantined', 'revoked')),
    assurance           TEXT NOT NULL DEFAULT 'TOFU_UNANCHORED'
                        CHECK (assurance IN ('TOFU_UNANCHORED', 'EXTERNALLY_ANCHORED', 'HARDWARE_ANCHORED')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (id, agent),
    CHECK (soul_dni = 'urn:soul:agent:' || soul_id::text)
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_candidate_projections (
    id                      BIGSERIAL PRIMARY KEY,
    registry_id             BIGINT NOT NULL,
    agent                   TEXT NOT NULL,
    source_identity_updated_at TIMESTAMPTZ NOT NULL,
    projection_jcs          BYTEA NOT NULL,
    projection_hash         TEXT NOT NULL CHECK (projection_hash ~ '^sha256:[0-9a-f]{64}$'),
    projection_version      TEXT NOT NULL DEFAULT 'identity-v1',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (registry_id, projection_hash),
    FOREIGN KEY (registry_id, agent) REFERENCES soul_v3.ssai_registry(id, agent),
    CHECK (octet_length(projection_jcs) > 1)
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_keys (
    id                  BIGSERIAL PRIMARY KEY,
    registry_id         BIGINT NOT NULL REFERENCES soul_v3.ssai_registry(id),
    key_id              TEXT NOT NULL UNIQUE,
    role                TEXT NOT NULL CHECK (role IN ('genesis_root', 'agent_identity', 'custodian', 'recovery', 'log_signer')),
    algorithm           TEXT NOT NULL DEFAULT 'Ed25519' CHECK (algorithm = 'Ed25519'),
    public_key          BYTEA NOT NULL CHECK (octet_length(public_key) = 32),
    state               TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'revoked', 'retired')),
    valid_from          TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((state = 'revoked') = (revoked_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_manifests (
    id                  BIGSERIAL PRIMARY KEY,
    registry_id         BIGINT NOT NULL REFERENCES soul_v3.ssai_registry(id),
    sequence            BIGINT NOT NULL CHECK (sequence > 0),
    manifest_jcs        BYTEA NOT NULL CHECK (octet_length(manifest_jcs) > 1),
    manifest_hash       TEXT NOT NULL CHECK (manifest_hash ~ '^sha256:[0-9a-f]{64}$'),
    previous_manifest_hash TEXT CHECK (previous_manifest_hash IS NULL OR previous_manifest_hash ~ '^sha256:[0-9a-f]{64}$'),
    state               TEXT NOT NULL DEFAULT 'candidate' CHECK (state IN ('candidate', 'accepted', 'superseded', 'rejected')),
    assurance           TEXT NOT NULL CHECK (assurance IN ('TOFU_UNANCHORED', 'EXTERNALLY_ANCHORED', 'HARDWARE_ANCHORED')),
    effective_at        TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (registry_id, sequence),
    UNIQUE (registry_id, manifest_hash),
    CHECK ((sequence = 1 AND previous_manifest_hash IS NULL) OR
           (sequence > 1 AND previous_manifest_hash IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_signatures (
    id                  BIGSERIAL PRIMARY KEY,
    manifest_id         BIGINT NOT NULL REFERENCES soul_v3.ssai_manifests(id),
    key_id              TEXT NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('genesis_root', 'agent_identity', 'custodian', 'recovery', 'log_signer')),
    signature           BYTEA NOT NULL CHECK (octet_length(signature) = 64),
    signed_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (manifest_id, key_id)
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_events (
    id                  BIGSERIAL PRIMARY KEY,
    registry_id         BIGINT NOT NULL REFERENCES soul_v3.ssai_registry(id),
    sequence            BIGINT NOT NULL CHECK (sequence > 0),
    event_type          TEXT NOT NULL,
    event_jcs           BYTEA NOT NULL CHECK (octet_length(event_jcs) > 1),
    previous_event_hash TEXT CHECK (previous_event_hash IS NULL OR previous_event_hash ~ '^sha256:[0-9a-f]{64}$'),
    event_hash          TEXT NOT NULL CHECK (event_hash ~ '^sha256:[0-9a-f]{64}$'),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (registry_id, sequence),
    UNIQUE (registry_id, event_hash),
    CHECK ((sequence = 1 AND previous_event_hash IS NULL) OR
           (sequence > 1 AND previous_event_hash IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_tree_heads (
    id                  BIGSERIAL PRIMARY KEY,
    registry_id         BIGINT NOT NULL REFERENCES soul_v3.ssai_registry(id),
    tree_size           BIGINT NOT NULL CHECK (tree_size > 0),
    root_hash           TEXT NOT NULL CHECK (root_hash ~ '^sha256:[0-9a-f]{64}$'),
    signer_key_id       TEXT NOT NULL,
    signature           BYTEA NOT NULL CHECK (octet_length(signature) = 64),
    witness_domain      TEXT NOT NULL,
    witnessed_at        TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (registry_id, tree_size, witness_domain)
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_rollout_state (
    registry_id         BIGINT PRIMARY KEY,
    agent               TEXT NOT NULL UNIQUE,
    mode                TEXT NOT NULL DEFAULT 'SHADOW' CHECK (mode IN ('SHADOW', 'DUAL_VERIFY', 'ENFORCE')),
    dual_verify_since   TIMESTAMPTZ,
    enforce_eligible_after TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (registry_id, agent) REFERENCES soul_v3.ssai_registry(id, agent),
    CHECK ((mode = 'SHADOW') OR (dual_verify_since IS NOT NULL)),
    CHECK (enforce_eligible_after IS NULL OR enforce_eligible_after >= dual_verify_since)
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_rollout_events (
    id                  BIGSERIAL PRIMARY KEY,
    registry_id         BIGINT NOT NULL,
    agent               TEXT NOT NULL,
    mode_from           TEXT CHECK (mode_from IS NULL OR mode_from IN ('SHADOW', 'DUAL_VERIFY', 'ENFORCE')),
    mode_to             TEXT NOT NULL CHECK (mode_to IN ('SHADOW', 'DUAL_VERIFY', 'ENFORCE')),
    authorized_by       TEXT NOT NULL,
    reason              TEXT NOT NULL,
    evidence            JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(evidence) = 'object'),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (registry_id, agent) REFERENCES soul_v3.ssai_registry(id, agent)
);

CREATE TABLE IF NOT EXISTS soul_v3.ssai_verification_runs (
    id                  BIGSERIAL PRIMARY KEY,
    registry_id         BIGINT,
    agent               TEXT NOT NULL,
    mode                TEXT NOT NULL CHECK (mode IN ('SHADOW', 'DUAL_VERIFY', 'ENFORCE')),
    status              TEXT NOT NULL CHECK (status IN ('PASS', 'DIVERGENCE', 'NO_CANDIDATE', 'NO_BIV', 'ERROR')),
    expected_projection_hash TEXT CHECK (expected_projection_hash IS NULL OR expected_projection_hash ~ '^sha256:[0-9a-f]{64}$'),
    observed_projection_hash TEXT CHECK (observed_projection_hash IS NULL OR observed_projection_hash ~ '^sha256:[0-9a-f]{64}$'),
    biv_pass            BOOLEAN,
    latency_ms          INTEGER NOT NULL CHECK (latency_ms >= 0),
    details             JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (registry_id, agent) REFERENCES soul_v3.ssai_registry(id, agent)
);

CREATE INDEX IF NOT EXISTS ssai_projection_agent_created_idx
    ON soul_v3.ssai_candidate_projections(agent, created_at DESC);
CREATE INDEX IF NOT EXISTS ssai_keys_registry_state_idx
    ON soul_v3.ssai_keys(registry_id, state, role);
CREATE INDEX IF NOT EXISTS ssai_manifests_registry_state_idx
    ON soul_v3.ssai_manifests(registry_id, state, sequence DESC);
CREATE INDEX IF NOT EXISTS ssai_events_registry_sequence_idx
    ON soul_v3.ssai_events(registry_id, sequence DESC);
CREATE INDEX IF NOT EXISTS ssai_tree_heads_registry_created_idx
    ON soul_v3.ssai_tree_heads(registry_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ssai_runs_agent_created_idx
    ON soul_v3.ssai_verification_runs(agent, created_at DESC);
CREATE INDEX IF NOT EXISTS ssai_runs_registry_created_idx
    ON soul_v3.ssai_verification_runs(registry_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ssai_runs_failures_idx
    ON soul_v3.ssai_verification_runs(agent, status, created_at DESC)
    WHERE status <> 'PASS';

CREATE OR REPLACE FUNCTION soul_v3.ssai_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, soul_v3
AS $$
BEGIN
    RAISE EXCEPTION 'SSAI append-only table % rejects %', TG_TABLE_NAME, TG_OP
        USING ERRCODE = '55000';
END;
$$;

CREATE OR REPLACE FUNCTION soul_v3.ssai_validate_rollout_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, soul_v3
AS $$
BEGIN
    IF NEW.mode = 'DUAL_VERIFY' THEN
        IF TG_OP = 'UPDATE' AND OLD.mode = 'SHADOW' THEN
            -- The database, not caller-supplied timestamps, starts the gate.
            NEW.dual_verify_since := now();
            NEW.enforce_eligible_after := now() + interval '14 days';
        ELSIF TG_OP = 'UPDATE' AND OLD.mode IN ('DUAL_VERIFY', 'ENFORCE') THEN
            IF NEW.dual_verify_since IS DISTINCT FROM OLD.dual_verify_since
               OR NEW.enforce_eligible_after IS DISTINCT FROM OLD.enforce_eligible_after THEN
                RAISE EXCEPTION 'DUAL_VERIFY observation timestamps are immutable';
            END IF;
        END IF;
        IF NEW.dual_verify_since IS NULL OR NEW.enforce_eligible_after IS NULL
           OR NEW.enforce_eligible_after < NEW.dual_verify_since + interval '14 days' THEN
            RAISE EXCEPTION 'DUAL_VERIFY requires an observation window of at least 14 days';
        END IF;
    ELSIF NEW.mode = 'ENFORCE' THEN
        IF TG_OP <> 'UPDATE' OR OLD.mode <> 'DUAL_VERIFY' THEN
            RAISE EXCEPTION 'ENFORCE requires a prior DUAL_VERIFY state';
        END IF;
        IF NEW.dual_verify_since IS DISTINCT FROM OLD.dual_verify_since
           OR NEW.enforce_eligible_after IS DISTINCT FROM OLD.enforce_eligible_after THEN
            RAISE EXCEPTION 'ENFORCE cannot rewrite observation timestamps';
        END IF;
        IF OLD.dual_verify_since IS NULL OR OLD.enforce_eligible_after IS NULL
           OR now() < OLD.dual_verify_since + interval '14 days'
           OR now() < OLD.enforce_eligible_after THEN
            RAISE EXCEPTION 'ENFORCE blocked until the 14-day observation gate completes';
        END IF;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS ssai_rollout_transition_guard ON soul_v3.ssai_rollout_state;
CREATE TRIGGER ssai_rollout_transition_guard
    BEFORE INSERT OR UPDATE ON soul_v3.ssai_rollout_state
    FOR EACH ROW EXECUTE FUNCTION soul_v3.ssai_validate_rollout_transition();

DO $triggers$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'ssai_candidate_projections', 'ssai_keys', 'ssai_manifests',
        'ssai_signatures', 'ssai_events', 'ssai_tree_heads', 'ssai_rollout_events',
        'ssai_verification_runs'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = table_name || '_immutable_row'
              AND tgrelid = ('soul_v3.' || table_name)::regclass
              AND NOT tgisinternal
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON soul_v3.%I '
                'FOR EACH ROW EXECUTE FUNCTION soul_v3.ssai_reject_mutation()',
                table_name || '_immutable_row', table_name
            );
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = table_name || '_immutable_truncate'
              AND tgrelid = ('soul_v3.' || table_name)::regclass
              AND NOT tgisinternal
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER %I BEFORE TRUNCATE ON soul_v3.%I '
                'FOR EACH STATEMENT EXECUTE FUNCTION soul_v3.ssai_reject_mutation()',
                table_name || '_immutable_truncate', table_name
            );
        END IF;
    END LOOP;
END
$triggers$;

ALTER TABLE soul_v3.ssai_registry ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_registry FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_candidate_projections ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_candidate_projections FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_keys FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_manifests ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_manifests FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_signatures ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_signatures FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_events FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_tree_heads ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_tree_heads FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_rollout_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_rollout_state FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_rollout_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_rollout_events FORCE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_verification_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ssai_verification_runs FORCE ROW LEVEL SECURITY;

DO $policies$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'ssai_registry', 'ssai_candidate_projections', 'ssai_keys', 'ssai_manifests',
        'ssai_events', 'ssai_tree_heads', 'ssai_rollout_state', 'ssai_rollout_events',
        'ssai_verification_runs'
    ] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='soul_v3' AND tablename=table_name
              AND policyname=table_name || '_seal_admin'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON soul_v3.%I FOR ALL TO seal USING (true) WITH CHECK (true)',
                table_name || '_seal_admin', table_name
            );
        END IF;
    END LOOP;
END
$policies$;

DROP POLICY IF EXISTS ssai_registry_runtime_read ON soul_v3.ssai_registry;
CREATE POLICY ssai_registry_runtime_read ON soul_v3.ssai_registry
    FOR SELECT TO pr_mcp_base
    USING (agent = nullif(current_setting('app.agent', true), ''));
DROP POLICY IF EXISTS ssai_projection_runtime_read ON soul_v3.ssai_candidate_projections;
CREATE POLICY ssai_projection_runtime_read ON soul_v3.ssai_candidate_projections
    FOR SELECT TO pr_mcp_base
    USING (agent = nullif(current_setting('app.agent', true), ''));
DROP POLICY IF EXISTS ssai_keys_runtime_read ON soul_v3.ssai_keys;
CREATE POLICY ssai_keys_runtime_read ON soul_v3.ssai_keys
    FOR SELECT TO pr_mcp_base
    USING ((SELECT EXISTS (
        SELECT 1 FROM soul_v3.ssai_registry r
        WHERE r.id = ssai_keys.registry_id
          AND r.agent = nullif(current_setting('app.agent', true), '')
    )));
DROP POLICY IF EXISTS ssai_manifests_runtime_read ON soul_v3.ssai_manifests;
CREATE POLICY ssai_manifests_runtime_read ON soul_v3.ssai_manifests
    FOR SELECT TO pr_mcp_base
    USING ((SELECT EXISTS (
        SELECT 1 FROM soul_v3.ssai_registry r
        WHERE r.id = ssai_manifests.registry_id
          AND r.agent = nullif(current_setting('app.agent', true), '')
    )));
DROP POLICY IF EXISTS ssai_events_runtime_read ON soul_v3.ssai_events;
CREATE POLICY ssai_events_runtime_read ON soul_v3.ssai_events
    FOR SELECT TO pr_mcp_base
    USING ((SELECT EXISTS (
        SELECT 1 FROM soul_v3.ssai_registry r
        WHERE r.id = ssai_events.registry_id
          AND r.agent = nullif(current_setting('app.agent', true), '')
    )));
DROP POLICY IF EXISTS ssai_tree_heads_runtime_read ON soul_v3.ssai_tree_heads;
CREATE POLICY ssai_tree_heads_runtime_read ON soul_v3.ssai_tree_heads
    FOR SELECT TO pr_mcp_base
    USING ((SELECT EXISTS (
        SELECT 1 FROM soul_v3.ssai_registry r
        WHERE r.id = ssai_tree_heads.registry_id
          AND r.agent = nullif(current_setting('app.agent', true), '')
    )));
DROP POLICY IF EXISTS ssai_rollout_runtime_read ON soul_v3.ssai_rollout_state;
CREATE POLICY ssai_rollout_runtime_read ON soul_v3.ssai_rollout_state
    FOR SELECT TO pr_mcp_base
    USING (agent = nullif(current_setting('app.agent', true), ''));
DROP POLICY IF EXISTS ssai_runs_runtime_read ON soul_v3.ssai_verification_runs;
CREATE POLICY ssai_runs_runtime_read ON soul_v3.ssai_verification_runs
    FOR SELECT TO pr_mcp_base
    USING (agent = nullif(current_setting('app.agent', true), ''));
DROP POLICY IF EXISTS ssai_rollout_events_runtime_read ON soul_v3.ssai_rollout_events;
CREATE POLICY ssai_rollout_events_runtime_read ON soul_v3.ssai_rollout_events
    FOR SELECT TO pr_mcp_base
    USING (agent = nullif(current_setting('app.agent', true), ''));
DROP POLICY IF EXISTS ssai_runs_runtime_insert ON soul_v3.ssai_verification_runs;
CREATE POLICY ssai_runs_runtime_insert ON soul_v3.ssai_verification_runs
    FOR INSERT TO pr_mcp_audit_write
    WITH CHECK (agent = nullif(current_setting('app.agent', true), ''));

-- Signature rows inherit agent isolation through their manifest without exposing a
-- per-row agent column.  Wrap the lookup in SELECT so it is evaluated once per row.
DROP POLICY IF EXISTS ssai_signatures_admin ON soul_v3.ssai_signatures;
CREATE POLICY ssai_signatures_admin ON soul_v3.ssai_signatures
    FOR ALL TO seal USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS ssai_signatures_runtime_read ON soul_v3.ssai_signatures;
CREATE POLICY ssai_signatures_runtime_read ON soul_v3.ssai_signatures
    FOR SELECT TO pr_mcp_base
    USING ((SELECT EXISTS (
        SELECT 1 FROM soul_v3.ssai_manifests m
        JOIN soul_v3.ssai_registry r ON r.id = m.registry_id
        WHERE m.id = manifest_id
          AND r.agent = nullif(current_setting('app.agent', true), '')
    )));

GRANT SELECT ON soul_v3.ssai_registry, soul_v3.ssai_candidate_projections,
    soul_v3.ssai_keys, soul_v3.ssai_manifests, soul_v3.ssai_signatures,
    soul_v3.ssai_events, soul_v3.ssai_tree_heads, soul_v3.ssai_rollout_state,
    soul_v3.ssai_rollout_events, soul_v3.ssai_verification_runs TO pr_mcp_base;
GRANT INSERT ON soul_v3.ssai_verification_runs TO pr_mcp_audit_write;
GRANT USAGE, SELECT ON SEQUENCE soul_v3.ssai_verification_runs_id_seq TO pr_mcp_audit_write;
REVOKE UPDATE, DELETE, TRUNCATE ON soul_v3.ssai_candidate_projections,
    soul_v3.ssai_keys, soul_v3.ssai_manifests, soul_v3.ssai_signatures,
    soul_v3.ssai_events, soul_v3.ssai_tree_heads, soul_v3.ssai_rollout_events,
    soul_v3.ssai_verification_runs
    FROM PUBLIC, pr_mcp_base, pr_mcp_audit_write;

COMMIT;
