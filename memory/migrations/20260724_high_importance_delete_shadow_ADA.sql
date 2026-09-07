-- High-importance memory DELETE telemetry — SHADOW ONLY.
--
-- This migration deliberately has no enforcement switch and never suppresses a
-- DELETE.  It measures the exact predicate that a future policy could protect:
-- importance >= 7 and the content survives neither as an exact live twin in
-- the same tenant+agent nor as the exact archived row.
--
-- William approval is still required before any enforcement or mass purge.

CREATE TABLE IF NOT EXISTS soul_v3.memory_delete_shadow_log (
    id                   bigserial PRIMARY KEY,
    memory_id            bigint      NOT NULL,
    tenant_id            uuid        NOT NULL,
    agent                 varchar     NOT NULL,
    importance            integer,
    invalid_at            timestamptz,
    content_hash_sha256   char(64)    NOT NULL,
    observed_at           timestamptz NOT NULL DEFAULT clock_timestamp(),
    db_session_user       text        NOT NULL,
    app_actor             text,
    app_reason            text,
    had_exact_live_twin   boolean     NOT NULL,
    had_exact_archive     boolean     NOT NULL,
    would_block           boolean     NOT NULL,
    guard_version         text        NOT NULL DEFAULT 'delete-shadow-v1'
);

CREATE INDEX IF NOT EXISTS memory_delete_shadow_log_observed_idx
    ON soul_v3.memory_delete_shadow_log (observed_at DESC);

CREATE INDEX IF NOT EXISTS memory_delete_shadow_log_would_block_idx
    ON soul_v3.memory_delete_shadow_log (observed_at DESC)
    WHERE would_block;

CREATE INDEX IF NOT EXISTS memory_delete_shadow_log_memory_idx
    ON soul_v3.memory_delete_shadow_log (memory_id);

ALTER TABLE soul_v3.memory_delete_shadow_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.memory_delete_shadow_log FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS memory_delete_shadow_log_admin_read
    ON soul_v3.memory_delete_shadow_log;
CREATE POLICY memory_delete_shadow_log_admin_read
    ON soul_v3.memory_delete_shadow_log
    FOR SELECT
    TO soul_admin
    USING (true);

REVOKE ALL ON TABLE soul_v3.memory_delete_shadow_log FROM PUBLIC;
REVOKE ALL ON SEQUENCE soul_v3.memory_delete_shadow_log_id_seq FROM PUBLIC;
GRANT SELECT ON TABLE soul_v3.memory_delete_shadow_log TO soul_admin;

CREATE OR REPLACE FUNCTION soul_v3.audit_high_importance_memory_delete_shadow()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $function$
DECLARE
    v_has_live_twin boolean := false;
    v_has_archive   boolean := false;
BEGIN
    -- The floor applies only to high-importance rows.  Lower-importance DELETEs
    -- keep their previous behavior and do not create audit volume.
    IF COALESCE(OLD.importance, 0) < 7 THEN
        RETURN OLD;
    END IF;

    -- A twin belonging to another tenant or agent is not a survivable copy of
    -- this private memory.  Use the canonical SHA-256 column/index, then confirm
    -- exact content equality so a hash collision cannot change the verdict.
    SELECT EXISTS (
        SELECT 1
        FROM soul_v3.memories AS twin
        WHERE twin.id <> OLD.id
          AND twin.tenant_id = OLD.tenant_id
          AND twin.agent = OLD.agent
          AND twin.invalid_at IS NULL
          AND twin.content_hash_sha256 = OLD.content_hash_sha256
          AND twin.content = OLD.content
    )
    INTO v_has_live_twin;

    -- The archive escape is row-specific.  A same-content archive belonging to
    -- another id/tenant/agent does not prove that OLD was durably archived.
    SELECT EXISTS (
        SELECT 1
        FROM soul_v3.memories_archive AS archived
        WHERE archived.id = OLD.id
          AND archived.tenant_id = OLD.tenant_id
          AND archived.agent = OLD.agent
    )
    INTO v_has_archive;

    INSERT INTO soul_v3.memory_delete_shadow_log (
        memory_id,
        tenant_id,
        agent,
        importance,
        invalid_at,
        content_hash_sha256,
        db_session_user,
        app_actor,
        app_reason,
        had_exact_live_twin,
        had_exact_archive,
        would_block
    )
    VALUES (
        OLD.id,
        OLD.tenant_id,
        OLD.agent,
        OLD.importance,
        OLD.invalid_at,
        OLD.content_hash_sha256,
        session_user,
        NULLIF(current_setting('seal.actor', true), ''),
        NULLIF(current_setting('seal.reason', true), ''),
        v_has_live_twin,
        v_has_archive,
        NOT v_has_live_twin AND NOT v_has_archive
    );

    RETURN OLD;
EXCEPTION
    WHEN OTHERS THEN
        -- Shadow telemetry must never become accidental enforcement.  Emit a
        -- database warning, but preserve the DELETE behavior that existed
        -- before this migration.
        RAISE WARNING
            'memory DELETE shadow audit failed for id=%: %',
            OLD.id,
            SQLERRM;
        RETURN OLD;
END;
$function$;

REVOKE ALL ON FUNCTION soul_v3.audit_high_importance_memory_delete_shadow()
    FROM PUBLIC;

DROP TRIGGER IF EXISTS memories_high_importance_delete_shadow
    ON soul_v3.memories;
CREATE TRIGGER memories_high_importance_delete_shadow
BEFORE DELETE ON soul_v3.memories
FOR EACH ROW
EXECUTE FUNCTION soul_v3.audit_high_importance_memory_delete_shadow();

COMMENT ON TABLE soul_v3.memory_delete_shadow_log IS
    'Shadow-only evidence for importance>=7 DELETEs. Stores hashes and provenance, never memory plaintext.';

COMMENT ON FUNCTION soul_v3.audit_high_importance_memory_delete_shadow() IS
    'SHADOW ONLY: records whether a high-importance DELETE would lack an exact same-tenant/same-agent live twin and an exact archive row. Never blocks or skips DELETE.';

COMMENT ON TRIGGER memories_high_importance_delete_shadow
    ON soul_v3.memories IS
    'SHADOW ONLY. Always preserves DELETE behavior; enforcement requires separate William-approved migration.';
