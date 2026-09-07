-- Native SUIE human review with durable, least-privilege promotion outbox.
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_soul_ingestion_review') THEN
    CREATE ROLE svc_soul_ingestion_review NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'svc_soul_ingestion_promoter') THEN
    CREATE ROLE svc_soul_ingestion_promoter NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
      NOREPLICATION NOBYPASSRLS NOINHERIT;
  END IF;
END $$;

ALTER ROLE svc_soul_ingestion_review NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE svc_soul_ingestion_promoter NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS NOINHERIT;
GRANT CONNECT ON DATABASE seal_memory TO svc_soul_ingestion_review,
  svc_soul_ingestion_promoter;
GRANT pr_ingestion_reader, pr_ingestion_reviewer TO svc_soul_ingestion_review;
GRANT pr_ingestion_reader, pr_ingestion_promoter TO svc_soul_ingestion_promoter;
REVOKE pr_ingestion_reviewer, pr_ingestion_promoter FROM svc_soul_ingestion;
REVOKE pr_ingestion_promoter FROM svc_soul_ingestion_review;
REVOKE pr_ingestion_reviewer FROM svc_soul_ingestion_promoter;

CREATE TABLE IF NOT EXISTS soul_v3.ingestion_promotion_outbox (
  outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id uuid NOT NULL,
  candidate_id uuid NOT NULL REFERENCES soul_v3.ingestion_memory_candidates(candidate_id)
    ON DELETE RESTRICT,
  action text NOT NULL CHECK (action IN ('promote','revoke')),
  approval_binding_sha256 text NOT NULL CHECK (approval_binding_sha256 ~ '^[0-9a-f]{64}$'),
  actor text NOT NULL,
  actor_session_id text NOT NULL,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','processing','completed','error','cancelled')),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  claimed_at timestamptz,
  lease_until timestamptz,
  worker_id text,
  memory_id bigint,
  last_error text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  completed_at timestamptz,
  UNIQUE (candidate_id, action)
);

ALTER TABLE soul_v3.ingestion_promotion_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE soul_v3.ingestion_promotion_outbox FORCE ROW LEVEL SECURITY;
REVOKE ALL ON soul_v3.ingestion_promotion_outbox FROM PUBLIC;
DROP POLICY IF EXISTS ingestion_promotion_outbox_tenant_read
  ON soul_v3.ingestion_promotion_outbox;
CREATE POLICY ingestion_promotion_outbox_tenant_read
  ON soul_v3.ingestion_promotion_outbox FOR SELECT
  TO pr_ingestion_reviewer, pr_ingestion_promoter
  USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
GRANT SELECT ON soul_v3.ingestion_promotion_outbox TO
  pr_ingestion_reviewer, pr_ingestion_promoter;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON soul_v3.ingestion_promotion_outbox FROM
  pr_ingestion_reviewer, pr_ingestion_promoter;

DROP INDEX IF EXISTS soul_v3.ingestion_one_approval_per_candidate;
DROP INDEX IF EXISTS soul_v3.ingestion_one_rejection_per_candidate;
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_one_initial_decision_per_candidate
  ON soul_v3.ingestion_state_events (candidate_id)
  WHERE event_type IN ('approved','rejected');
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_one_promotion_per_candidate
  ON soul_v3.ingestion_state_events (candidate_id)
  WHERE event_type = 'promoted';
CREATE UNIQUE INDEX IF NOT EXISTS ingestion_one_revocation_per_candidate
  ON soul_v3.ingestion_state_events (candidate_id)
  WHERE event_type = 'revoked';

DROP INDEX IF EXISTS soul_v3.memories_ingestion_candidate_once;
CREATE UNIQUE INDEX memories_ingestion_candidate_once
  ON soul_v3.memories ((metadata->>'ingestion_candidate_id'))
  WHERE source = 'human_reviewed_ingestion'
    AND metadata ? 'ingestion_candidate_id';

-- Direct reviewer/promoter event writes would bypass transition validation.
REVOKE INSERT ON soul_v3.ingestion_state_events FROM
  pr_ingestion_reviewer, pr_ingestion_promoter;

CREATE OR REPLACE FUNCTION soul_v3.ingestion_review_decide(
  p_tenant_id uuid,
  p_candidate_id uuid,
  p_decision text,
  p_actor text,
  p_actor_session_id text,
  p_reason text
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
DECLARE
  c record;
  latest record;
  binding text;
  promoted_memory bigint;
BEGIN
  IF session_user <> 'svc_soul_ingestion_review' THEN
    RAISE EXCEPTION 'review service identity required' USING ERRCODE='42501';
  END IF;
  IF p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid
     OR p_actor <> 'William' OR p_actor_session_id !~ '^[0-9a-f]{64}$'
     OR char_length(p_reason) NOT BETWEEN 3 AND 1000
     OR p_decision NOT IN ('approved','rejected','revoked') THEN
    RAISE EXCEPTION 'invalid review authority or decision' USING ERRCODE='42501';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(p_candidate_id::text, 0));
  SELECT c0.candidate_id, c0.document_id, c0.derivation_id,
         c0.proposed_content, d.raw_hash_sha256, r.output_hash_sha256
    INTO c
  FROM soul_v3.ingestion_memory_candidates c0
  JOIN soul_v3.ingestion_documents d ON d.document_id=c0.document_id
  JOIN soul_v3.ingestion_derivations r ON r.derivation_id=c0.derivation_id
  WHERE c0.candidate_id=p_candidate_id AND c0.tenant_id=p_tenant_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'candidate not found in authorized tenant' USING ERRCODE='P0002';
  END IF;
  SELECT event_type, metadata INTO latest
  FROM soul_v3.ingestion_state_events
  WHERE candidate_id=p_candidate_id
    AND event_type IN ('approved','rejected','promoted','revoked')
  ORDER BY event_id DESC LIMIT 1;

  IF p_decision = 'revoked' THEN
    IF latest.event_type = 'revoked' THEN
      RETURN jsonb_build_object('state','revoked','idempotent_replay',true,
        'memory_id',latest.metadata->>'memory_id');
    END IF;
    IF latest.event_type IS DISTINCT FROM 'promoted' THEN
      RAISE EXCEPTION 'only a promoted candidate can be revoked' USING ERRCODE='23514';
    END IF;
  ELSE
    IF latest.event_type = p_decision THEN
      RETURN jsonb_build_object('state',p_decision,'idempotent_replay',true);
    END IF;
    IF latest.event_type = 'promoted' AND p_decision = 'approved' THEN
      RETURN jsonb_build_object('state','promoted','idempotent_replay',true,
        'memory_id',(latest.metadata->>'memory_id')::bigint);
    END IF;
    IF latest.event_type IS NOT NULL THEN
      RAISE EXCEPTION 'candidate already finalized as %', latest.event_type
        USING ERRCODE='23514';
    END IF;
  END IF;

  binding := encode(soul_v3.digest(convert_to(concat_ws(E'\x1f',
    c.candidate_id::text, c.document_id::text, c.derivation_id::text,
    c.raw_hash_sha256, c.output_hash_sha256,
    encode(soul_v3.digest(convert_to(c.proposed_content,'UTF8'),'sha256'),'hex'),
    p_actor, p_actor_session_id, p_decision),'UTF8'),'sha256'),'hex');

  promoted_memory := CASE WHEN latest.event_type='promoted'
    THEN (latest.metadata->>'memory_id')::bigint ELSE NULL END;
  INSERT INTO soul_v3.ingestion_state_events (
    tenant_id, document_id, candidate_id, event_type, actor, actor_session_id,
    approval_verified, reason, metadata
  ) VALUES (
    p_tenant_id, c.document_id, p_candidate_id, p_decision, p_actor,
    p_actor_session_id, p_decision IN ('approved','revoked'), p_reason,
    jsonb_build_object('approval_binding_sha256',binding,
      'raw_hash_sha256',c.raw_hash_sha256,
      'derivation_hash_sha256',c.output_hash_sha256,
      'memory_id',promoted_memory)
  );
  IF p_decision IN ('approved','revoked') THEN
    INSERT INTO soul_v3.ingestion_promotion_outbox (
      tenant_id,candidate_id,action,approval_binding_sha256,actor,actor_session_id,memory_id
    ) VALUES (
      p_tenant_id,p_candidate_id,
      CASE WHEN p_decision='approved' THEN 'promote' ELSE 'revoke' END,
      binding,p_actor,p_actor_session_id,promoted_memory
    ) ON CONFLICT (candidate_id,action) DO NOTHING;
  END IF;
  RETURN jsonb_build_object(
    'state',CASE WHEN p_decision='approved' THEN 'approved_pending' ELSE p_decision END,
    'idempotent_replay',false,'approval_binding_sha256',binding,
    'memory_id',promoted_memory
  );
END;
$$;

DROP FUNCTION IF EXISTS soul_v3.ingestion_promoted_memory(uuid);
DROP FUNCTION IF EXISTS soul_v3.ingestion_promoter_complete(uuid,bigint,bigint);

CREATE OR REPLACE FUNCTION soul_v3.ingestion_promoted_memory(
  p_tenant_id uuid, p_candidate_id uuid
) RETURNS bigint
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
DECLARE resolved bigint;
BEGIN
  IF session_user <> 'svc_soul_ingestion_promoter'
     OR p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid THEN
    RAISE EXCEPTION 'promoter service identity required' USING ERRCODE='42501';
  END IF;
  SELECT memory_id INTO resolved FROM (
    SELECT (e.metadata->>'memory_id')::bigint AS memory_id, 0 AS priority
    FROM soul_v3.ingestion_state_events e
    WHERE e.tenant_id=p_tenant_id AND e.candidate_id=p_candidate_id
      AND e.event_type='promoted'
    UNION ALL
    SELECT m.id, 1 FROM soul_v3.memories m
    WHERE m.source='human_reviewed_ingestion'
      AND m.metadata->>'ingestion_candidate_id'=p_candidate_id::text
      AND m.metadata->>'ingestion_tenant_id'=p_tenant_id::text
  ) candidates WHERE memory_id IS NOT NULL ORDER BY priority, memory_id LIMIT 1;
  RETURN resolved;
END;
$$;

CREATE OR REPLACE FUNCTION soul_v3.ingestion_promoter_claim(
  p_tenant_id uuid, p_worker_id text, p_lease_seconds integer DEFAULT 60
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
DECLARE task soul_v3.ingestion_promotion_outbox%ROWTYPE; payload jsonb;
BEGIN
  IF session_user <> 'svc_soul_ingestion_promoter'
     OR p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid
     OR p_worker_id !~ '^[A-Za-z0-9_.:-]{8,128}$'
     OR p_lease_seconds NOT BETWEEN 15 AND 300 THEN
    RAISE EXCEPTION 'invalid promoter claim authority' USING ERRCODE='42501';
  END IF;
  SELECT * INTO task FROM soul_v3.ingestion_promotion_outbox
  WHERE tenant_id=p_tenant_id AND available_at <= clock_timestamp()
    AND (status IN ('pending','error') OR
         (status='processing' AND lease_until < clock_timestamp()))
  ORDER BY outbox_id FOR UPDATE SKIP LOCKED LIMIT 1;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE soul_v3.ingestion_promotion_outbox SET status='processing',
    attempts=attempts+1,claimed_at=clock_timestamp(),
    lease_until=clock_timestamp()+make_interval(secs=>p_lease_seconds),
    worker_id=p_worker_id,last_error=NULL
  WHERE outbox_id=task.outbox_id RETURNING * INTO task;
  SELECT jsonb_build_object(
    'outbox_id',task.outbox_id,'tenant_id',task.tenant_id,
    'candidate_id',task.candidate_id,'action',task.action,
    'approval_binding_sha256',task.approval_binding_sha256,
    'actor',task.actor,'actor_session_id',task.actor_session_id,
    'memory_id',task.memory_id,'attempts',task.attempts,
    'document_id',c.document_id,'derivation_id',c.derivation_id,
    'proposed_agent',c.proposed_agent,'proposed_category',c.proposed_category,
    'proposed_content',c.proposed_content,
    'proposed_importance',c.proposed_importance,'scope',d.scope,
    'raw_hash_sha256',d.raw_hash_sha256,
    'output_hash_sha256',r.output_hash_sha256
  ) INTO payload
  FROM soul_v3.ingestion_memory_candidates c
  JOIN soul_v3.ingestion_documents d ON d.document_id=c.document_id AND d.tenant_id=p_tenant_id
  JOIN soul_v3.ingestion_derivations r ON r.derivation_id=c.derivation_id AND r.tenant_id=p_tenant_id
  WHERE c.candidate_id=task.candidate_id AND c.tenant_id=p_tenant_id;
  IF payload IS NULL THEN RAISE EXCEPTION 'promotion candidate missing' USING ERRCODE='P0002'; END IF;
  RETURN payload;
END;
$$;

CREATE OR REPLACE FUNCTION soul_v3.ingestion_promoter_fail(
  p_tenant_id uuid, p_outbox_id bigint, p_worker_id text,
  p_error text, p_retry_seconds integer DEFAULT 15
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
BEGIN
  IF session_user <> 'svc_soul_ingestion_promoter'
     OR p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid
     OR p_retry_seconds NOT BETWEEN 1 AND 3600 THEN
    RAISE EXCEPTION 'invalid promoter failure authority' USING ERRCODE='42501';
  END IF;
  UPDATE soul_v3.ingestion_promotion_outbox SET status='error',
    available_at=clock_timestamp()+make_interval(secs=>p_retry_seconds),
    lease_until=NULL,last_error=left(p_error,500)
  WHERE outbox_id=p_outbox_id AND tenant_id=p_tenant_id
    AND status='processing' AND worker_id=p_worker_id
    AND lease_until >= clock_timestamp();
  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION soul_v3.ingestion_promoter_complete(
  p_tenant_id uuid, p_outbox_id bigint, p_worker_id text, p_memory_id bigint
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, soul_v3
AS $$
DECLARE task record; latest record; doc_id uuid; memory_row record;
BEGIN
  IF session_user <> 'svc_soul_ingestion_promoter'
     OR p_tenant_id IS DISTINCT FROM NULLIF(current_setting('app.tenant_id', true), '')::uuid THEN
    RAISE EXCEPTION 'promoter service identity required' USING ERRCODE='42501';
  END IF;
  SELECT * INTO task FROM soul_v3.ingestion_promotion_outbox
  WHERE outbox_id=p_outbox_id AND tenant_id=p_tenant_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'promotion task not found' USING ERRCODE='P0002'; END IF;
  IF task.status <> 'processing' OR task.worker_id IS DISTINCT FROM p_worker_id
     OR task.lease_until < clock_timestamp() THEN
    RAISE EXCEPTION 'stale or unowned promotion lease' USING ERRCODE='40001';
  END IF;
  PERFORM pg_advisory_xact_lock(hashtextextended(task.candidate_id::text, 0));
  SELECT event_type,metadata INTO latest FROM soul_v3.ingestion_state_events
  WHERE tenant_id=p_tenant_id AND candidate_id=task.candidate_id
    AND event_type IN ('approved','rejected','promoted','revoked')
  ORDER BY event_id DESC LIMIT 1;
  IF latest.metadata->>'approval_binding_sha256' IS DISTINCT FROM task.approval_binding_sha256 THEN
    RAISE EXCEPTION 'approval binding mismatch' USING ERRCODE='23514';
  END IF;
  IF task.action='promote' THEN
    IF latest.event_type='promoted' THEN p_memory_id := (latest.metadata->>'memory_id')::bigint;
    ELSIF latest.event_type IS DISTINCT FROM 'approved' THEN
      RAISE EXCEPTION 'latest state is not approved' USING ERRCODE='23514';
    END IF;
    SELECT id,invalid_at INTO memory_row FROM soul_v3.memories
    WHERE id=p_memory_id AND source='human_reviewed_ingestion'
      AND metadata->>'ingestion_candidate_id'=task.candidate_id::text
      AND metadata->>'ingestion_tenant_id'=p_tenant_id::text;
    IF NOT FOUND OR memory_row.invalid_at IS NOT NULL THEN
      RAISE EXCEPTION 'canonical memory binding not verified' USING ERRCODE='23514';
    END IF;
    IF latest.event_type <> 'promoted' THEN
      SELECT document_id INTO doc_id FROM soul_v3.ingestion_memory_candidates
      WHERE candidate_id=task.candidate_id AND tenant_id=p_tenant_id;
      INSERT INTO soul_v3.ingestion_state_events (
        tenant_id,document_id,candidate_id,event_type,actor,actor_session_id,
        approval_verified,reason,metadata
      ) VALUES (
        p_tenant_id,doc_id,task.candidate_id,'promoted',task.actor,
        task.actor_session_id,true,'canonical memory_store confirmed persistence',
        jsonb_build_object('memory_id',p_memory_id,
          'approval_binding_sha256',task.approval_binding_sha256)
      );
    END IF;
  ELSE
    p_memory_id := COALESCE(task.memory_id,p_memory_id);
    IF latest.event_type IS DISTINCT FROM 'revoked' THEN
      RAISE EXCEPTION 'latest state is not revoked' USING ERRCODE='23514';
    END IF;
    SELECT id,invalid_at INTO memory_row FROM soul_v3.memories
    WHERE id=p_memory_id AND source='human_reviewed_ingestion'
      AND metadata->>'ingestion_candidate_id'=task.candidate_id::text
      AND metadata->>'ingestion_tenant_id'=p_tenant_id::text;
    IF NOT FOUND OR memory_row.invalid_at IS NULL THEN
      RAISE EXCEPTION 'canonical invalidation not verified' USING ERRCODE='23514';
    END IF;
  END IF;
  UPDATE soul_v3.ingestion_promotion_outbox SET status='completed',memory_id=p_memory_id,
    completed_at=clock_timestamp(),lease_until=NULL,last_error=NULL
  WHERE outbox_id=p_outbox_id;
  RETURN CASE WHEN task.action='promote' THEN 'promoted' ELSE 'revoked' END;
END;
$$;

REVOKE ALL ON FUNCTION soul_v3.ingestion_review_decide(uuid,uuid,text,text,text,text)
  FROM PUBLIC;
REVOKE ALL ON FUNCTION soul_v3.ingestion_promoted_memory(uuid,uuid) FROM PUBLIC;
REVOKE ALL ON FUNCTION soul_v3.ingestion_promoter_claim(uuid,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION soul_v3.ingestion_promoter_fail(uuid,bigint,text,text,integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION soul_v3.ingestion_promoter_complete(uuid,bigint,text,bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION soul_v3.ingestion_review_decide(uuid,uuid,text,text,text,text)
  TO pr_ingestion_reviewer;
GRANT EXECUTE ON FUNCTION soul_v3.ingestion_promoted_memory(uuid,uuid)
  TO pr_ingestion_promoter;
GRANT EXECUTE ON FUNCTION soul_v3.ingestion_promoter_claim(uuid,text,integer)
  TO pr_ingestion_promoter;
GRANT EXECUTE ON FUNCTION soul_v3.ingestion_promoter_fail(uuid,bigint,text,text,integer)
  TO pr_ingestion_promoter;
GRANT EXECUTE ON FUNCTION soul_v3.ingestion_promoter_complete(uuid,bigint,text,bigint)
  TO pr_ingestion_promoter;

COMMIT;
