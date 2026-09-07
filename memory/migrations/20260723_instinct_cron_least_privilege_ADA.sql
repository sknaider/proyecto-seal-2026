-- Least-privilege capability for the cross-agent instinct/sleep maintenance job.
--
-- The existing login_consolidate login is already the dedicated, non-superuser,
-- NOBYPASSRLS identity for cross-agent consolidation.  Extend only its
-- NOLOGIN capability role; never give the cron a seal/superuser DSN.

BEGIN;

GRANT SELECT ON
    soul_v3.instincts,
    soul_v3.procedural_memories
TO pr_consolidate;

GRANT UPDATE (strength, invalid_at)
ON soul_v3.instincts
TO pr_consolidate;

GRANT UPDATE (active)
ON soul_v3.procedural_memories
TO pr_consolidate;

-- The cron performs replay/forget/archive only.  Column grants prevent it from
-- changing memory content, ownership, scope, tenant, embeddings, or provenance.
GRANT UPDATE (importance, invalid_at, metadata)
ON soul_v3.memories
TO pr_consolidate;

GRANT INSERT ON soul_v3.event_log TO pr_consolidate;
GRANT USAGE, SELECT ON SEQUENCE soul_v3.event_log_id_seq TO pr_consolidate;

DROP POLICY IF EXISTS instincts_consolidate_read
ON soul_v3.instincts;
CREATE POLICY instincts_consolidate_read
ON soul_v3.instincts
FOR SELECT TO pr_consolidate
USING (true);

DROP POLICY IF EXISTS instincts_consolidate_maintenance
ON soul_v3.instincts;
CREATE POLICY instincts_consolidate_maintenance
ON soul_v3.instincts
FOR UPDATE TO pr_consolidate
USING (true)
WITH CHECK (true);

DROP POLICY IF EXISTS procedural_memories_consolidate_read
ON soul_v3.procedural_memories;
CREATE POLICY procedural_memories_consolidate_read
ON soul_v3.procedural_memories
FOR SELECT TO pr_consolidate
USING (true);

DROP POLICY IF EXISTS procedural_memories_consolidate_maintenance
ON soul_v3.procedural_memories;
CREATE POLICY procedural_memories_consolidate_maintenance
ON soul_v3.procedural_memories
FOR UPDATE TO pr_consolidate
USING (true)
WITH CHECK (true);

-- Replace the invalidation-only policy: column-level grants above remain the
-- hard mutation boundary, while the cron may also replay/decay importance.
DROP POLICY IF EXISTS memories_consolidate_invalidate
ON soul_v3.memories;
DROP POLICY IF EXISTS memories_consolidate_maintenance
ON soul_v3.memories;
CREATE POLICY memories_consolidate_maintenance
ON soul_v3.memories
FOR UPDATE TO pr_consolidate
USING (true)
WITH CHECK (true);

DROP POLICY IF EXISTS event_log_consolidate_insert
ON soul_v3.event_log;
CREATE POLICY event_log_consolidate_insert
ON soul_v3.event_log
FOR INSERT TO pr_consolidate
WITH CHECK (agent = 'SYSTEM' AND event_type = 'heartbeat');

COMMENT ON ROLE pr_consolidate IS
    'Cross-agent consolidation/instinct maintenance; column-scoped writes, NOSUPERUSER/NOBYPASSRLS.';

COMMIT;
