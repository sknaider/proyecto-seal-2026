BEGIN;

CREATE SCHEMA IF NOT EXISTS soul_v3 AUTHORIZATION seal;
CREATE SCHEMA IF NOT EXISTS soul_f3 AUTHORIZATION seal;
REVOKE ALL ON SCHEMA soul_f3 FROM PUBLIC;

CREATE OR REPLACE FUNCTION soul_v3.mcp_session_agent()
RETURNS text
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT CASE session_user
    WHEN 'mcp_runtime_ada' THEN 'ADA'
    WHEN 'mcp_runtime_alice' THEN 'ALICE'
    WHEN 'mcp_runtime_dum' THEN 'DUM'
    WHEN 'mcp_runtime_jarvis' THEN 'JARVIS'
    WHEN 'mcp_runtime_nexus' THEN 'NEXUS'
    ELSE NULL
  END
$$;

CREATE TABLE IF NOT EXISTS soul_v3.runtime_hooks (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  soul_event text NOT NULL,
  script_path text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('learning', 'operational')),
  agent text,
  enabled boolean NOT NULL DEFAULT true,
  ordering integer NOT NULL DEFAULT 100,
  matcher text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (soul_event IN (
    'on_boot', 'on_prompt', 'on_turn_end', 'on_compact',
    'on_tool_call', 'on_tool_result', 'on_file_change',
    'on_task_create', 'on_permission_denied'
  ))
);

CREATE UNIQUE INDEX IF NOT EXISTS runtime_hooks_f3_identity_uq
ON soul_v3.runtime_hooks (soul_event, script_path, COALESCE(agent, ''), COALESCE(matcher, ''));

ALTER TABLE soul_v3.runtime_hooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.runtime_hooks FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname='soul_v3' AND tablename='runtime_hooks' AND policyname='runtime_hooks_agent_read'
  ) THEN
    CREATE POLICY runtime_hooks_agent_read ON soul_v3.runtime_hooks
      FOR SELECT
      USING (agent IS NULL OR agent = soul_v3.mcp_session_agent());
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS soul_f3.parity_evidence_v2 (
  suite_run_id text NOT NULL,
  runtime_id text NOT NULL CHECK (runtime_id IN ('claude_code', 'local_llama')),
  native_event text NOT NULL,
  soul_event text NOT NULL,
  script_path text NOT NULL,
  script_sha256 text NOT NULL CHECK (script_sha256 ~ '^[0-9a-f]{64}$'),
  runtime_pid integer NOT NULL CHECK (runtime_pid > 1),
  agent text NOT NULL,
  nonce text NOT NULL CHECK (nonce ~ '^[0-9a-f]{32}$'),
  token_sha256 text NOT NULL CHECK (token_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_by text NOT NULL DEFAULT session_user,
  recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (suite_run_id, runtime_id, native_event, soul_event, script_path, agent, nonce)
);

ALTER TABLE soul_f3.parity_evidence_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_f3.parity_evidence_v2 FORCE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname='soul_f3' AND tablename='parity_evidence_v2' AND policyname='parity_evidence_v2_agent_select'
  ) THEN
    CREATE POLICY parity_evidence_v2_agent_select ON soul_f3.parity_evidence_v2
      FOR SELECT
      USING (agent = soul_v3.mcp_session_agent());
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname='soul_f3' AND tablename='parity_evidence_v2' AND policyname='parity_evidence_v2_agent_insert'
  ) THEN
    CREATE POLICY parity_evidence_v2_agent_insert ON soul_f3.parity_evidence_v2
      FOR INSERT
      WITH CHECK (
        agent = soul_v3.mcp_session_agent()
        AND recorded_by = session_user
      );
  END IF;
END
$$;

REVOKE ALL ON soul_v3.runtime_hooks FROM PUBLIC;
REVOKE ALL ON soul_f3.parity_evidence_v2 FROM PUBLIC;

GRANT USAGE ON SCHEMA soul_v3, soul_f3 TO
  mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
  mcp_runtime_jarvis, mcp_runtime_nexus;
GRANT EXECUTE ON FUNCTION soul_v3.mcp_session_agent() TO
  mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
  mcp_runtime_jarvis, mcp_runtime_nexus;
GRANT SELECT ON soul_v3.runtime_hooks TO
  mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
  mcp_runtime_jarvis, mcp_runtime_nexus;
GRANT SELECT, INSERT ON soul_f3.parity_evidence_v2 TO
  mcp_runtime_ada, mcp_runtime_alice, mcp_runtime_dum,
  mcp_runtime_jarvis, mcp_runtime_nexus;

INSERT INTO soul_v3.runtime_hooks
  (soul_event, script_path, kind, agent, enabled, ordering, matcher)
SELECT event_name,
       'memory/soul_parity_effect_hook.py',
       CASE WHEN event_name IN ('on_boot','on_prompt','on_turn_end','on_compact')
            THEN 'learning' ELSE 'operational' END,
       NULL, true, 10, NULL
FROM unnest(ARRAY[
  'on_boot','on_prompt','on_turn_end','on_compact',
  'on_tool_call','on_tool_result','on_file_change',
  'on_task_create','on_permission_denied'
]) AS event_name
ON CONFLICT DO NOTHING;

COMMIT;
