-- Dedicated login for SUIE. Password material is provisioned separately from
-- the service token by scripts/bootstrap_soul_ingestion_db_role.py.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_soul_ingestion') THEN
    CREATE ROLE svc_soul_ingestion NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS NOINHERIT;
  END IF;
END $$;

ALTER ROLE svc_soul_ingestion NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS NOINHERIT;
GRANT CONNECT ON DATABASE seal_memory TO svc_soul_ingestion;
GRANT pr_ingestion_reader, pr_ingestion_processor TO svc_soul_ingestion;

COMMIT;
