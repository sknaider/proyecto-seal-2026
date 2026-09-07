-- SAFETY STATUS: DEFINED BUT DISABLED.
-- The first live version blocked explicit high-importance supersession/manual
-- invalidation while callers continued updating Qdrant/Neo4j, creating a
-- cross-store divergence risk. Keep the definition for audit/design work, but
-- do not enforce it until every writer carries an explicit provenance contract.
--
-- William preservation floor (2026-05-21), refined after measuring importance
-- contamination: importance >= 7 is protected unless another exact live copy
-- exists for the same tenant and agent. Central DB enforcement covers every
-- current/future writer, not only MCP dedup.

BEGIN;

CREATE OR REPLACE FUNCTION soul_v3.guard_high_importance_memory_invalidation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    has_exact_live_twin boolean := false;
BEGIN
    IF OLD.invalid_at IS NULL
       AND NEW.invalid_at IS NOT NULL
       AND GREATEST(OLD.importance, NEW.importance) >= 7 THEN
        -- Lock one candidate twin while deciding. Besides making the predicate
        -- stable for normal maintenance, this makes two concurrent attempts on
        -- the final pair conflict instead of silently removing both copies.
        PERFORM 1
        FROM soul_v3.memories AS twin
        WHERE twin.id <> OLD.id
          AND twin.invalid_at IS NULL
          AND twin.agent = OLD.agent
          AND twin.tenant_id IS NOT DISTINCT FROM OLD.tenant_id
          AND twin.content_hash_sha256 = OLD.content_hash_sha256
          AND twin.content = OLD.content
        ORDER BY twin.id
        LIMIT 1
        FOR UPDATE;
        has_exact_live_twin := FOUND;

        IF has_exact_live_twin THEN
            RETURN NEW;
        END IF;

        -- Never report a silent success. The prior row-skip produced `UPDATE 0`, so a
        -- caller could continue mutating Qdrant/Neo4j while PostgreSQL stayed
        -- active. A prohibited transition must abort the transaction before
        -- any caller treats it as success.
        RAISE EXCEPTION
            'memory #% importance=% is protected: no exact live twin for the same tenant and agent',
            OLD.id,
            OLD.importance
            USING ERRCODE = 'integrity_constraint_violation',
                  HINT = 'use an explicit, audited supersession contract';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS memories_high_importance_invalidation_guard
    ON soul_v3.memories;

CREATE TRIGGER memories_high_importance_invalidation_guard
BEFORE UPDATE OF invalid_at ON soul_v3.memories
FOR EACH ROW
EXECUTE FUNCTION soul_v3.guard_high_importance_memory_invalidation();

-- Fail safe for migration replay: the application-level memory_store guard is
-- active; this broader trigger remains disabled pending compatibility tests
-- for manual invalidation, bitemporal supersession and cross-store writers.
ALTER TABLE soul_v3.memories
    DISABLE TRIGGER memories_high_importance_invalidation_guard;

COMMENT ON FUNCTION soul_v3.guard_high_importance_memory_invalidation() IS
    'DEFINED BUT NOT PRODUCTION POLICY. RAISE EXCEPTION is a fail-safe for accidental enablement, not approved batch semantics. The trigger is intentionally disabled because generic writers lack a per-row applied/rejected contract and may continue to Neo4j after PostgreSQL rejects a transition. Do not enable without William-approved durable audit, rowcount handling, batch semantics and compensation.';

COMMENT ON TRIGGER memories_high_importance_invalidation_guard
    ON soul_v3.memories IS
    'INTENTIONALLY DISABLED. memory_store has the active causal dedup guard. Do not enable until a William-approved coordinated invalidation contract exists: the current RAISE EXCEPTION body is only a fail-safe and can abort bulk maintenance.';

COMMIT;
