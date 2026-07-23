BEGIN;

DROP POLICY IF EXISTS ada_bridge_hard_session_identity ON soul_v3.working_state;
DROP POLICY IF EXISTS ada_bridge_hard_session_identity ON soul_v3.emotional_diary;
DROP POLICY IF EXISTS ada_bridge_hard_session_identity ON soul_v3.identity;

DROP FUNCTION IF EXISTS soul_v3.ada_bridge_session_agent();

COMMIT;
