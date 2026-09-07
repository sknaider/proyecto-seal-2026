-- The dedicated SUIE login needs the read-only role for GET/search routes in
-- addition to the processor role used by ingestion transactions.

BEGIN;
GRANT pr_ingestion_reader TO svc_soul_ingestion;
COMMIT;
