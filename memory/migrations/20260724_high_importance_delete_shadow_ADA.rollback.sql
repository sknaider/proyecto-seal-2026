-- Rollback for 20260724_high_importance_delete_shadow_ADA.sql.
-- Do not run without first counting/reviewing audit rows: dropping the log is
-- destructive evidence removal.

DROP TRIGGER IF EXISTS memories_high_importance_delete_shadow
    ON soul_v3.memories;

DROP FUNCTION IF EXISTS soul_v3.audit_high_importance_memory_delete_shadow();

DROP TABLE IF EXISTS soul_v3.memory_delete_shadow_log;
