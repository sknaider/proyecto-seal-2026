-- SUIE v1 staging: documentary evidence is isolated from soul_v3.memories.
-- Owner: ADA. Rollout: CANDIDATE only; no promotion function is created here.

BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_ingestion_reader') THEN
    CREATE ROLE pr_ingestion_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_ingestion_processor') THEN
    CREATE ROLE pr_ingestion_processor NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_ingestion_reviewer') THEN
    CREATE ROLE pr_ingestion_reviewer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pr_ingestion_promoter') THEN
    CREATE ROLE pr_ingestion_promoter NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END $$;

GRANT USAGE ON SCHEMA soul_v3 TO pr_ingestion_reader, pr_ingestion_processor,
  pr_ingestion_reviewer, pr_ingestion_promoter;

CREATE TABLE IF NOT EXISTS soul_v3.ingestion_documents (
  document_id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL,
  owner_agent text,
  scope text NOT NULL CHECK (scope IN ('private','shared','team','william','domain')),
  source_kind text NOT NULL CHECK (source_kind IN (
    'chat','text','markdown','pdf','email','gtl_awb','youtube','paper','medical_note'
  )),
  source_ref text NOT NULL,
  source_uri text,
  source_descriptor jsonb NOT NULL CHECK (jsonb_typeof(source_descriptor) = 'object'),
  trust_tier text GENERATED ALWAYS AS (source_descriptor->>'trust_tier') STORED
    CHECK (trust_tier IN ('owner_verified','team_verified','external_trusted','external_untrusted')),
  media_type text NOT NULL,
  language text NOT NULL,
  title text,
  authors jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(authors) = 'array'),
  raw_artifact_ref text NOT NULL,
  raw_hash_sha256 text NOT NULL CHECK (raw_hash_sha256 ~ '^[0-9a-f]{64}$'),
  normalized_hash_sha256 text NOT NULL CHECK (normalized_hash_sha256 ~ '^[0-9a-f]{64}$'),
  normalization_profile text NOT NULL,
  identity_profile text NOT NULL DEFAULT 'document_identity_v2',
  normalized_text text NOT NULL,
  sensitivity text NOT NULL CHECK (sensitivity IN ('public','internal','confidential','medical')),
  retention_policy text NOT NULL,
  adapter_id text NOT NULL,
  adapter_version text NOT NULL,
  state text NOT NULL CHECK (state IN ('received','normalized','processed','quarantined','failed','revoked')),
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
  ingested_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT ingestion_documents_idempotency_key UNIQUE NULLS NOT DISTINCT (
    tenant_id, owner_agent, scope, sensitivity, trust_tier, source_kind,
    source_ref, adapter_id, adapter_version, raw_hash_sha256,
    normalization_profile, identity_profile
  )
);

CREATE TABLE IF NOT EXISTS soul_v3.ingestion_segments (
  segment_id uuid PRIMARY KEY,
  document_id uuid NOT NULL REFERENCES soul_v3.ingestion_documents(document_id) ON DELETE RESTRICT,
  tenant_id uuid NOT NULL,
  ordinal integer NOT NULL CHECK (ordinal >= 0),
  text text NOT NULL,
  text_hash_sha256 text NOT NULL CHECK (text_hash_sha256 ~ '^[0-9a-f]{64}$'),
  start_char integer NOT NULL CHECK (start_char >= 0),
  end_char integer NOT NULL CHECK (end_char >= start_char),
  anchor jsonb NOT NULL CHECK (jsonb_typeof(anchor) = 'object'),
  heading_path jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(heading_path) = 'array'),
  protected_spans jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(protected_spans) = 'array'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (document_id, ordinal),
  UNIQUE (document_id, text_hash_sha256, start_char, end_char)
);

CREATE TABLE IF NOT EXISTS soul_v3.ingestion_derivations (
  derivation_id uuid PRIMARY KEY,
  document_id uuid NOT NULL REFERENCES soul_v3.ingestion_documents(document_id) ON DELETE RESTRICT,
  tenant_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('digest','structured_extract','entities','candidate_set')),
  processor_id text NOT NULL,
  processor_version text NOT NULL,
  config_hash_sha256 text NOT NULL CHECK (config_hash_sha256 ~ '^[0-9a-f]{64}$'),
  input_hash_sha256 text NOT NULL CHECK (input_hash_sha256 ~ '^[0-9a-f]{64}$'),
  output_hash_sha256 text NOT NULL CHECK (output_hash_sha256 ~ '^[0-9a-f]{64}$'),
  content text NOT NULL,
  coverage jsonb NOT NULL CHECK (jsonb_typeof(coverage) = 'object'),
  evidence_anchors jsonb NOT NULL CHECK (jsonb_typeof(evidence_anchors) = 'array'),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL,
  UNIQUE (document_id, kind, processor_id, processor_version, config_hash_sha256, output_hash_sha256)
);

CREATE TABLE IF NOT EXISTS soul_v3.ingestion_memory_candidates (
  candidate_id uuid PRIMARY KEY,
  document_id uuid NOT NULL REFERENCES soul_v3.ingestion_documents(document_id) ON DELETE RESTRICT,
  derivation_id uuid NOT NULL REFERENCES soul_v3.ingestion_derivations(derivation_id) ON DELETE RESTRICT,
  tenant_id uuid NOT NULL,
  proposed_agent text NOT NULL,
  proposed_category text NOT NULL CHECK (proposed_category IN ('fact','decision','correction','milestone','pattern','insight')),
  proposed_content text NOT NULL,
  proposed_importance smallint NOT NULL CHECK (proposed_importance BETWEEN 1 AND 10),
  importance_advisory boolean NOT NULL DEFAULT true CHECK (importance_advisory),
  importance_method text NOT NULL DEFAULT 'candidate_rules_v1',
  confidence double precision NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
  confidence_scorer text NOT NULL DEFAULT 'candidate_rules_v1',
  confidence_factors jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(confidence_factors) = 'object'),
  source_anchors jsonb NOT NULL CHECK (jsonb_typeof(source_anchors) = 'array'),
  risk_flags jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(risk_flags) = 'array'),
  initial_state text NOT NULL DEFAULT 'candidate' CHECK (initial_state IN ('candidate','review_blocked')),
  approval_verified boolean NOT NULL DEFAULT false CHECK (approval_verified = false),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (derivation_id, proposed_agent, proposed_category, proposed_content)
);

CREATE TABLE IF NOT EXISTS soul_v3.ingestion_state_events (
  event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id uuid NOT NULL,
  document_id uuid REFERENCES soul_v3.ingestion_documents(document_id) ON DELETE RESTRICT,
  candidate_id uuid REFERENCES soul_v3.ingestion_memory_candidates(candidate_id) ON DELETE RESTRICT,
  event_type text NOT NULL CHECK (event_type IN (
    'received','normalized','processed','quarantined','candidate_created','approved',
    'rejected','promoted','failed','revoked'
  )),
  actor text NOT NULL,
  actor_session_id text,
  approval_verified boolean NOT NULL DEFAULT false,
  reason text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata) = 'object'),
  created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (document_id IS NOT NULL OR candidate_id IS NOT NULL),
  CHECK (event_type NOT IN ('approved','promoted') OR approval_verified)
);

CREATE TABLE IF NOT EXISTS soul_v3.ingestion_outbox (
  outbox_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id uuid NOT NULL,
  document_id uuid NOT NULL REFERENCES soul_v3.ingestion_documents(document_id) ON DELETE RESTRICT,
  event_kind text NOT NULL CHECK (event_kind IN ('embed_segments','index_document','sync_neo4j','revoke_document')),
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  idempotency_key text NOT NULL UNIQUE,
  available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  claimed_at timestamptz,
  completed_at timestamptz,
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error text,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS ingestion_documents_tenant_created_idx
  ON soul_v3.ingestion_documents (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_segments_document_ordinal_idx
  ON soul_v3.ingestion_segments (document_id, ordinal);
CREATE INDEX IF NOT EXISTS ingestion_derivations_document_created_idx
  ON soul_v3.ingestion_derivations (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_candidates_review_idx
  ON soul_v3.ingestion_memory_candidates (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_events_document_idx
  ON soul_v3.ingestion_state_events (document_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ingestion_outbox_ready_idx
  ON soul_v3.ingestion_outbox (available_at, outbox_id) WHERE completed_at IS NULL;

CREATE OR REPLACE FUNCTION soul_v3.ingestion_reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% is append-only; append a state event instead', TG_TABLE_NAME
    USING ERRCODE = 'integrity_constraint_violation';
END;
$$;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'ingestion_documents','ingestion_segments','ingestion_derivations',
    'ingestion_memory_candidates','ingestion_state_events'
  ] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON soul_v3.%I', table_name || '_no_update_delete', table_name);
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON soul_v3.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION soul_v3.ingestion_reject_mutation()',
      table_name || '_no_update_delete', table_name
    );
    EXECUTE format('DROP TRIGGER IF EXISTS %I ON soul_v3.%I', table_name || '_no_truncate', table_name);
    EXECUTE format(
      'CREATE TRIGGER %I BEFORE TRUNCATE ON soul_v3.%I '
      'FOR EACH STATEMENT EXECUTE FUNCTION soul_v3.ingestion_reject_mutation()',
      table_name || '_no_truncate', table_name
    );
  END LOOP;
END $$;

DO $$
DECLARE
  table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'ingestion_documents','ingestion_segments','ingestion_derivations',
    'ingestion_memory_candidates','ingestion_state_events','ingestion_outbox'
  ] LOOP
    EXECUTE format('ALTER TABLE soul_v3.%I ENABLE ROW LEVEL SECURITY', table_name);
    EXECUTE format('ALTER TABLE soul_v3.%I FORCE ROW LEVEL SECURITY', table_name);
    EXECUTE format('REVOKE ALL ON TABLE soul_v3.%I FROM PUBLIC', table_name);
    EXECUTE format('DROP POLICY IF EXISTS %I ON soul_v3.%I', table_name || '_tenant_read', table_name);
    EXECUTE format(
      'CREATE POLICY %I ON soul_v3.%I FOR SELECT TO pr_ingestion_reader, '
      'pr_ingestion_processor, pr_ingestion_reviewer, pr_ingestion_promoter '
      'USING (tenant_id = NULLIF(current_setting(''app.tenant_id'', true), '''')::uuid)',
      table_name || '_tenant_read', table_name
    );
  END LOOP;
END $$;

DROP POLICY IF EXISTS ingestion_documents_processor_insert ON soul_v3.ingestion_documents;
CREATE POLICY ingestion_documents_processor_insert ON soul_v3.ingestion_documents
  FOR INSERT TO pr_ingestion_processor
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
DROP POLICY IF EXISTS ingestion_segments_processor_insert ON soul_v3.ingestion_segments;
CREATE POLICY ingestion_segments_processor_insert ON soul_v3.ingestion_segments
  FOR INSERT TO pr_ingestion_processor
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
DROP POLICY IF EXISTS ingestion_derivations_processor_insert ON soul_v3.ingestion_derivations;
CREATE POLICY ingestion_derivations_processor_insert ON soul_v3.ingestion_derivations
  FOR INSERT TO pr_ingestion_processor
  WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
DROP POLICY IF EXISTS ingestion_candidates_processor_insert ON soul_v3.ingestion_memory_candidates;
CREATE POLICY ingestion_candidates_processor_insert ON soul_v3.ingestion_memory_candidates
  FOR INSERT TO pr_ingestion_processor
  WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    AND approval_verified = false
    AND initial_state IN ('candidate','review_blocked')
  );
DROP POLICY IF EXISTS ingestion_events_processor_insert ON soul_v3.ingestion_state_events;
CREATE POLICY ingestion_events_processor_insert ON soul_v3.ingestion_state_events
  FOR INSERT TO pr_ingestion_processor
  WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    AND event_type IN ('received','normalized','processed','quarantined','candidate_created','failed')
    AND approval_verified = false
  );
DROP POLICY IF EXISTS ingestion_events_reviewer_insert ON soul_v3.ingestion_state_events;
CREATE POLICY ingestion_events_reviewer_insert ON soul_v3.ingestion_state_events
  FOR INSERT TO pr_ingestion_reviewer
  WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    AND event_type IN ('approved','rejected','revoked')
    AND (event_type <> 'approved' OR approval_verified)
  );
DROP POLICY IF EXISTS ingestion_events_promoter_insert ON soul_v3.ingestion_state_events;
CREATE POLICY ingestion_events_promoter_insert ON soul_v3.ingestion_state_events
  FOR INSERT TO pr_ingestion_promoter
  WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    AND event_type = 'promoted'
    AND approval_verified
  );
DROP POLICY IF EXISTS ingestion_outbox_processor_insert ON soul_v3.ingestion_outbox;
CREATE POLICY ingestion_outbox_processor_insert ON soul_v3.ingestion_outbox
  FOR INSERT TO pr_ingestion_processor
  WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    AND event_kind IN ('embed_segments','index_document')
  );

GRANT SELECT ON soul_v3.ingestion_documents, soul_v3.ingestion_segments,
  soul_v3.ingestion_derivations, soul_v3.ingestion_memory_candidates,
  soul_v3.ingestion_state_events, soul_v3.ingestion_outbox TO pr_ingestion_reader;
GRANT SELECT, INSERT ON soul_v3.ingestion_documents, soul_v3.ingestion_segments,
  soul_v3.ingestion_derivations, soul_v3.ingestion_memory_candidates,
  soul_v3.ingestion_state_events, soul_v3.ingestion_outbox TO pr_ingestion_processor;
GRANT SELECT ON soul_v3.ingestion_documents, soul_v3.ingestion_segments,
  soul_v3.ingestion_derivations, soul_v3.ingestion_memory_candidates,
  soul_v3.ingestion_state_events TO pr_ingestion_reviewer, pr_ingestion_promoter;
GRANT INSERT ON soul_v3.ingestion_state_events TO pr_ingestion_reviewer, pr_ingestion_promoter;
GRANT USAGE, SELECT ON SEQUENCE soul_v3.ingestion_state_events_event_id_seq TO
  pr_ingestion_processor, pr_ingestion_reviewer, pr_ingestion_promoter;
GRANT USAGE, SELECT ON SEQUENCE soul_v3.ingestion_outbox_outbox_id_seq TO pr_ingestion_processor;

-- Membership only permits an explicit SET ROLE. The service must enter the
-- least-privilege role inside each transaction before setting tenant context.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'seal') THEN
    GRANT pr_ingestion_reader, pr_ingestion_processor, pr_ingestion_reviewer,
      pr_ingestion_promoter TO seal;
  END IF;
END $$;

COMMENT ON TABLE soul_v3.ingestion_documents IS
  'SUIE documentary staging; never part of SOUL active memory recall.';
COMMENT ON TABLE soul_v3.ingestion_memory_candidates IS
  'Untrusted memory proposals; no direct promotion grant or memories write exists.';
COMMENT ON ROLE pr_ingestion_processor IS
  'SUIE acquisition/derivation writer; intentionally has zero privileges on soul_v3.memories.';

COMMIT;
