-- ============================================================================
-- P1 — Clamp RESTRICTIVE de identidad dura para grantees internos
-- Migración UP.  ARTEFACTO PROPUESTO — NO APLICAR sin gate de ADA.
-- Owner de ejecución: ADA.  Revisar SIEMPRE en tx + ROLLBACK antes de aplicar.
--
-- Corrige los 5 fallos del RED de ADA (2026-07-22):
--   1. current_user bajo inherit sigue siendo login_* -> el binding mapea AMBOS
--      (login_* y pr_*/svc_*), no solo pr_*.  Sin esto: identidad NULL, servicios rotos.
--   2. GRANT EXECUTE de la función a los roles afectados (tras revocar de PUBLIC).
--   3. Función SECURITY DEFINER, owner NOLOGIN, search_path fijo, grants mínimos.
--   4. Seed valida igualdad o ABORTA (no ON CONFLICT DO NOTHING que oculta errores).
--   5. Migración .sql + rollback + pytest reales (este archivo, _down.sql y el test).
-- ============================================================================

BEGIN;

-- 3. Owner NOLOGIN dedicado del definer (sin login, sin superusuario, sin inherit)
DO $owner$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'soul_rls_definer') THEN
    CREATE ROLE soul_rls_definer NOLOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS;
  END IF;
END $owner$;

-- Tabla de binding rol->tenant (identidad dura por current_user)
CREATE TABLE IF NOT EXISTS soul_v3.internal_role_tenant_bindings (
    db_role      name        PRIMARY KEY,
    tenant_id    uuid        NOT NULL,
    disabled_at  timestamptz
);
ALTER TABLE soul_v3.internal_role_tenant_bindings OWNER TO soul_rls_definer;

-- 3. Función SECURITY DEFINER: corre como el owner (con SELECT sobre la tabla),
--    el invoker solo necesita EXECUTE.  search_path fijo, nombres calificados.
-- FIX RED-2 de ADA: dentro de un SECURITY DEFINER, `current_user` es el OWNER
-- (soul_rls_definer), NO el invocador -> devolvería NULL y cerraría el acceso legítimo.
-- Se ancla a `session_user` = el login AUTENTICADO, inmutable ante SET ROLE (más
-- seguro: forjar SET ROLE no lo cambia). Los roles internos conectan como login_*.
CREATE OR REPLACE FUNCTION soul_v3.internal_role_tenant_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $fn$
    SELECT b.tenant_id
    FROM soul_v3.internal_role_tenant_bindings AS b
    WHERE b.db_role = session_user::name
      AND b.disabled_at IS NULL
    LIMIT 1
$fn$;
ALTER FUNCTION soul_v3.internal_role_tenant_id() OWNER TO soul_rls_definer;

-- 2. EXECUTE: revocar de PUBLIC y otorgar SOLO a los roles afectados
--    (login_* que auto-heredan, los pr_*/svc_* directos, y svc_seal_studio LOGIN).
REVOKE EXECUTE ON FUNCTION soul_v3.internal_role_tenant_id() FROM PUBLIC;
GRANT  EXECUTE ON FUNCTION soul_v3.internal_role_tenant_id() TO
  -- 9 logins originales (login_* auto-heredan pr_*, svc_seal_studio LOGIN)
  login_ada_bridge, login_bus, login_checkpoints, login_dashboard_admin,
  login_dashboard_ro, login_dum_heartbeat, login_infra_watchdog, login_mcp_canary,
  svc_seal_studio,
  -- 10 identidades vivas que ADA cazó: agent-clampadas pero NO tenant-clampadas
  mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum, mcp_runtime_jarvis, mcp_runtime_nexus,
  svc_soul_nerves_ada, svc_soul_nerves_alice, svc_soul_nerves_dum, svc_soul_nerves_jarvis, svc_soul_nerves_nexus,
  -- roles pr_*/svc_ objetivo de la política (por SET ROLE)
  pr_ada_bridge, pr_bus, pr_checkpoints, pr_dashboard_admin, pr_dashboard_ro,
  pr_dum_heartbeat, pr_infra_watchdog, pr_mcp_base, pr_mcp_cognition_write,
  pr_mcp_memory_write, pr_retrieval, svc_soul_nerves;

-- 1 + 4. Seed: la función keyea por `session_user` = el LOGIN autenticado. Los roles
--        internos conectan como login_* (o svc_seal_studio, que es LOGIN). Los pr_*/
--        svc_soul_nerves son NOLOGIN -> NUNCA pueden ser session_user, así que NO
--        necesitan binding (la política igual los cubre por membresía). Valida-o-ABORTA.
DO $seed$
DECLARE
  r record;
  internal_tenant CONSTANT uuid := '00000000-0000-0000-0000-000000000000';
BEGIN
  FOR r IN SELECT unnest(ARRAY[
      -- 9 originales
      'login_ada_bridge','login_bus','login_checkpoints','login_dashboard_admin',
      'login_dashboard_ro','login_dum_heartbeat','login_infra_watchdog','login_mcp_canary',
      'svc_seal_studio',
      -- 10 identidades vivas (LOGIN) que ADA cazó: agent-clampadas, tenant-desprotegidas
      'mcp_runtime_ada','mcp_runtime_alice','mcp_runtime_dum','mcp_runtime_jarvis','mcp_runtime_nexus',
      'svc_soul_nerves_ada','svc_soul_nerves_alice','svc_soul_nerves_dum','svc_soul_nerves_jarvis','svc_soul_nerves_nexus'
    ]::name[]) AS db_role
  LOOP
    -- abortar si ya existe un binding con OTRO tenant (no ocultar el conflicto)
    IF EXISTS (SELECT 1 FROM soul_v3.internal_role_tenant_bindings b
               WHERE b.db_role = r.db_role AND b.tenant_id <> internal_tenant) THEN
      RAISE EXCEPTION 'binding conflict for %: existing tenant differs from seed', r.db_role;
    END IF;
    -- insertar o REACTIVAR: un binding con tenant correcto pero disabled_at != NULL
    -- haría que el resolver (filtra disabled_at IS NULL) devuelva NULL -> acceso cerrado.
    -- Por eso se fuerza disabled_at = NULL cuando el tenant coincide (nunca DO NOTHING).
    INSERT INTO soul_v3.internal_role_tenant_bindings (db_role, tenant_id, disabled_at)
    VALUES (r.db_role, internal_tenant, NULL)
    ON CONFLICT (db_role) DO UPDATE
      SET disabled_at = NULL
      WHERE internal_role_tenant_bindings.tenant_id = EXCLUDED.tenant_id;
  END LOOP;
END $seed$;
-- NOTA soul_admin: excluido a propósito. Rol admin de propósito amplio -> decisión
-- de ADA: clampar (agregar a seed + policy) o documentar como excepción auditada.

-- Política RESTRICTIVE por tabla. SE COMPONE con el clamp de AGENTE existente
-- (mcp_hard_*/nerves_hard fijan agente; esta fija tenant) => RESTRICTIVE agente AND
-- RESTRICTIVE tenant. TO los pr_*/svc_ (login_* aplican por membresía) + los 10 LOGIN
-- vivos (mcp_runtime_*/svc_soul_nerves_*) que NO son miembros de los pr_*.
-- Clampa aunque exista la PERMISSIVE débil TO public.
DO $pol$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['memories','session_memory','inner_monologue',
                           'distilled_exchanges','memory_retrieval_log','memories_archive']
  LOOP
    EXECUTE format($f$
      CREATE POLICY internal_hard_tenant_identity_v1 ON soul_v3.%I
        AS RESTRICTIVE FOR ALL
        TO pr_ada_bridge, pr_bus, pr_checkpoints, pr_dashboard_admin, pr_dashboard_ro,
           pr_dum_heartbeat, pr_infra_watchdog, pr_mcp_base, pr_mcp_cognition_write,
           pr_mcp_memory_write, pr_retrieval, svc_seal_studio, svc_soul_nerves,
           mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum, mcp_runtime_jarvis, mcp_runtime_nexus,
           svc_soul_nerves_ada, svc_soul_nerves_alice, svc_soul_nerves_dum,
           svc_soul_nerves_jarvis, svc_soul_nerves_nexus
        USING      (tenant_id = soul_v3.internal_role_tenant_id())
        WITH CHECK (tenant_id = soul_v3.internal_role_tenant_id())
    $f$, t);
  END LOOP;
END $pol$;

COMMIT;
