-- ============================================================================
-- P1 — Rollback (DOWN) del clamp RESTRICTIVE de identidad dura interna.
-- Revierte 001_internal_tenant_clamp_up.sql en orden inverso.
-- ARTEFACTO PROPUESTO — NO APLICAR sin gate de ADA. Revisar en tx + ROLLBACK.
-- ============================================================================

BEGIN;

-- Quitar la política RESTRICTIVE de cada tabla (idempotente con IF EXISTS)
DO $pol$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['memories','session_memory','inner_monologue',
                           'distilled_exchanges','memory_retrieval_log','memories_archive']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS internal_hard_tenant_identity_v1 ON soul_v3.%I', t);
  END LOOP;
END $pol$;

-- Quitar la función (revoca sus grants implícitamente al dropearla)
DROP FUNCTION IF EXISTS soul_v3.internal_role_tenant_id();

-- Quitar la tabla de bindings
DROP TABLE IF EXISTS soul_v3.internal_role_tenant_bindings;

-- Quitar el owner NOLOGIN si ya no posee objetos (no forzar si algo quedó colgado)
DO $owner$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'soul_rls_definer')
     AND NOT EXISTS (
       SELECT 1 FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner
       WHERE r.rolname = 'soul_rls_definer'
       UNION ALL
       SELECT 1 FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner
       WHERE r.rolname = 'soul_rls_definer'
     ) THEN
    DROP ROLE soul_rls_definer;
  END IF;
END $owner$;

COMMIT;
