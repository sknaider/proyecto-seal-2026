BEGIN;

DROP POLICY IF EXISTS nerves_agent_memory_insert_v2
  ON soul_v3.memories;

COMMIT;
