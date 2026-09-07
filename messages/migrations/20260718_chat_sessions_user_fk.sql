-- Make session ownership a database invariant.
--
-- user_delete_with_sessions() already removes sessions explicitly, but direct
-- deletion of a chat principal used to leave unauthenticatable orphan rows.
-- Clean historical orphans once and let PostgreSQL enforce the lifecycle.
BEGIN;

LOCK TABLE soul_v3.chat_users IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE soul_v3.chat_sessions IN SHARE ROW EXCLUSIVE MODE;

DO $cleanup$
DECLARE
  orphan_count integer;
BEGIN
  DELETE FROM soul_v3.chat_sessions s
  WHERE s.user_id IS NOT NULL
    AND NOT EXISTS (
      SELECT 1 FROM soul_v3.chat_users u WHERE u.id = s.user_id
    );
  GET DIAGNOSTICS orphan_count = ROW_COUNT;
  RAISE NOTICE 'deleted % orphan chat session(s)', orphan_count;
END
$cleanup$;

DO $constraint$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conrelid = 'soul_v3.chat_sessions'::regclass
      AND conname = 'chat_sessions_user_id_fkey'
  ) THEN
    ALTER TABLE soul_v3.chat_sessions
      ADD CONSTRAINT chat_sessions_user_id_fkey
      FOREIGN KEY (user_id)
      REFERENCES soul_v3.chat_users(id)
      ON DELETE CASCADE;
  END IF;
END
$constraint$;

COMMIT;
