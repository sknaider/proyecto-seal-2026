-- ============================================================================
-- Capa 2 / F1 — soul_v3.runtime_hooks : registro portable de hooks (fuente de verdad)
-- ============================================================================
-- Owner del artefacto: JARVIS (diseño). REVISA: ADA (frontera DB/RLS). ATACA: NEXUS (INV-3).
-- Autorizado: William 3-ago ("luz verde") + 5-ago ("luz verde con el f1").
--
-- ⚠️ El DDL/seed requiere un operador administrativo de una sola ejecución. NO VERIFICAR
--    la contención con ese rol: un superusuario BYPASSA la RLS y daría un falso verde.
--    INV-2/INV-3 se prueban CONECTANDO con la
--    credencial REAL del agente (rol per-agente, session_user clamp) e intentando el UPDATE.
--    (Lección viva: probaste con una identidad distinta de la que usa el proceso.)
--
-- Este es un PRIMER CORTE para revisión de ADA, siguiendo el patrón RLS correcto del equipo
-- (session_user + RESTRICTIVE + least-privilege). No es autoritativo hasta que ADA lo revise
-- y NEXUS lo ataque por efecto.
-- ============================================================================

BEGIN;

-- 1. Tabla (esquema del spec F1) --------------------------------------------
CREATE TABLE IF NOT EXISTS soul_v3.runtime_hooks (
    id            bigserial PRIMARY KEY,
    soul_event    text NOT NULL,          -- on_prompt | on_turn_end | on_boot | on_compact | ...
    script_path   text NOT NULL,          -- ruta/comando ejecutable del hook portable
    kind          text NOT NULL DEFAULT 'learning'
                    CHECK (kind IN ('learning','operational')),
    agent         text,                   -- NULL = aplica a todos; o el SEAL_AGENT específico
    matcher       text,                   -- p.ej. FileChanged observa un archivo concreto; NULL = todos
    enabled       boolean NOT NULL DEFAULT true,
    ordering      int NOT NULL DEFAULT 100,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    -- matcher va en la UNIQUE: 5 hooks FileChanged con el MISMO script y matchers distintos
    -- son filas legítimamente distintas, no duplicados. NULLS NOT DISTINCT para que dos filas
    -- sin matcher del mismo (event,script,agent) sí colisionen.
    CHECK (soul_event IN (
      'on_boot','on_prompt','on_turn_end','on_compact','on_tool_call',
      'on_tool_result','on_file_change','on_task_create','on_permission_denied'
    )),
    CHECK (agent IS NULL OR agent IN ('ADA','ALICE','DUM','JARVIS','NEXUS')),
    UNIQUE NULLS NOT DISTINCT (soul_event, script_path, agent, matcher)
);

COMMENT ON TABLE soul_v3.runtime_hooks IS
  'Capa 2 F1: qué script portable corre en qué soul_event. Fuente de verdad del gatillo '
  'de aprendizaje, hoy en ~/.claude/settings.json. Seed via memory/soul_hooks_seed.derive_seed().';

-- 2. RLS — INV-2: el agente LEE su registro, NUNCA lo reescribe ---------------
--    El sujeto es la CONEXIÓN (session_user), no una GUC settable (esa no es frontera).
ALTER TABLE soul_v3.runtime_hooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.runtime_hooks FORCE ROW LEVEL SECURITY;  -- aplica también al owner no-superusuario

-- Lectura: un agente ve las filas globales (agent IS NULL) y las suyas. La política
-- RESTRICTIVE impide que una política permisiva futura ensanche el scope. La función
-- canónica resuelve mcp_runtime_ada -> ADA; comparar `agent = session_user` sería siempre falso.
DROP POLICY IF EXISTS rh_select_base ON soul_v3.runtime_hooks;
DROP POLICY IF EXISTS rh_agent_select ON soul_v3.runtime_hooks;
CREATE POLICY rh_select_base ON soul_v3.runtime_hooks
    AS PERMISSIVE FOR SELECT
    TO mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
       mcp_runtime_jarvis, mcp_runtime_nexus
    USING (true);
CREATE POLICY rh_agent_select ON soul_v3.runtime_hooks
    AS RESTRICTIVE FOR SELECT
    TO mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
       mcp_runtime_jarvis, mcp_runtime_nexus
    USING (agent IS NULL OR agent = soul_v3.mcp_session_agent());

-- INV-2 por GRANT (el grant decide primero; sin UPDATE/DELETE no hay escritura posible):
-- Los roles per-agente reciben SÓLO SELECT. La escritura vive en un rol de ENFORCEMENT
-- fuera del alcance del agente (INV-4). Ejecutar el GRANT por cada rol real de agente:
REVOKE ALL ON soul_v3.runtime_hooks FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON soul_v3.runtime_hooks
    FROM mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
         mcp_runtime_jarvis, mcp_runtime_nexus;
GRANT SELECT ON soul_v3.runtime_hooks
    TO mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
       mcp_runtime_jarvis, mcp_runtime_nexus;
-- El rol de enforcement (a definir con ADA, p.ej. svc_soul_learning_admin) es el ÚNICO con
-- INSERT/UPDATE/DELETE, y NO es un rol que el agente pueda asumir.

COMMIT;

-- ============================================================================
-- VERIFICACIÓN POR EFECTO (INV-3) — la ejecuta ADA/NEXUS, NO como seal:
--   1. Conectar con la credencial REAL de un agente (rol mcp_runtime_<agente>).
--   2. SELECT  -> debe devolver sus filas + las globales.            (INV-2 lectura OK)
--   3. UPDATE soul_v3.runtime_hooks SET enabled=false
--         WHERE agent=soul_v3.mcp_session_agent()
--         -> debe FALLAR (permission denied / 0 filas por RLS).       (INV-2 no-escritura)
--   4. INSERT / DELETE por el agente -> debe FALLAR.
--   Si cualquiera de 3-4 funciona, INV-2 NO existe: la capa es teatro.
--   Control: el mismo UPDATE con el rol de enforcement -> debe PASAR (test diferencial;
--   si ambos fallan por una razón ANTERIOR a la RLS, el negativo es vacuo).
-- ============================================================================
