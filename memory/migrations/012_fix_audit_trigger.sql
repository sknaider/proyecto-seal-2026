-- Migration 012 — Fix trg_audit_fn to handle tables without 'agent' column
-- Problem: trigger assumed all tables have agent column; rules table uses set_by instead.
-- Applied as hotfix 2026-04-17; this migration makes it permanent across container rebuilds.

CREATE OR REPLACE FUNCTION public.trg_audit_fn()
RETURNS trigger LANGUAGE plpgsql AS $func$
DECLARE
    v_agent TEXT;
    v_old   JSONB;
    v_new   JSONB;
    v_id    BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_old   := to_jsonb(OLD);
        v_id    := OLD.id;
        v_agent := COALESCE(v_old->>'agent', v_old->>'set_by', 'system');
    ELSIF TG_OP = 'INSERT' THEN
        v_new   := to_jsonb(NEW);
        v_id    := NEW.id;
        v_agent := COALESCE(v_new->>'agent', v_new->>'set_by', 'system');
    ELSE -- UPDATE
        v_old   := to_jsonb(OLD);
        v_new   := to_jsonb(NEW);
        v_id    := NEW.id;
        v_agent := COALESCE(v_new->>'agent', v_new->>'set_by', 'system');
    END IF;

    -- Strip embedding vectors from memories (too large, meaningless in audit)
    IF TG_TABLE_NAME = 'memories' THEN
        v_old := v_old - 'embedding';
        v_new := v_new - 'embedding';
    END IF;

    INSERT INTO soul_audit_log(table_name, operation, agent, row_id, old_data, new_data)
    VALUES (TG_TABLE_NAME, TG_OP, v_agent, v_id, v_old, v_new);

    RETURN NULL;
END;
$func$;
