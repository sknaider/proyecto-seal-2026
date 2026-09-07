-- SUIE v1.1 contract hardening.
-- Idempotency is over raw evidence plus the normalization contract, not only
-- the normalized output. Candidate scoring remains advisory and auditable.

BEGIN;

ALTER TABLE soul_v3.ingestion_documents
  DROP CONSTRAINT IF EXISTS ingestion_documents_tenant_id_source_kind_source_ref_adapte_key;
ALTER TABLE soul_v3.ingestion_documents
  DROP CONSTRAINT IF EXISTS ingestion_documents_idempotency_key;
ALTER TABLE soul_v3.ingestion_documents
  ADD CONSTRAINT ingestion_documents_idempotency_key UNIQUE NULLS NOT DISTINCT (
    tenant_id, owner_agent, scope, sensitivity, trust_tier, source_kind,
    source_ref, adapter_id, adapter_version, raw_hash_sha256,
    normalization_profile, identity_profile
  );

ALTER TABLE soul_v3.ingestion_derivations
  DROP CONSTRAINT IF EXISTS ingestion_derivations_kind_check;
ALTER TABLE soul_v3.ingestion_derivations
  ADD CONSTRAINT ingestion_derivations_kind_check CHECK (
    kind IN ('digest','structured_extract','entities','candidate_set')
  );

ALTER TABLE soul_v3.ingestion_memory_candidates
  ADD COLUMN IF NOT EXISTS importance_advisory boolean NOT NULL DEFAULT true,
  ADD COLUMN IF NOT EXISTS importance_method text NOT NULL DEFAULT 'candidate_rules_v1',
  ADD COLUMN IF NOT EXISTS confidence_scorer text NOT NULL DEFAULT 'candidate_rules_v1',
  ADD COLUMN IF NOT EXISTS confidence_factors jsonb NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE soul_v3.ingestion_memory_candidates
  DROP CONSTRAINT IF EXISTS ingestion_memory_candidates_importance_advisory_check,
  DROP CONSTRAINT IF EXISTS ingestion_memory_candidates_confidence_factors_check,
  DROP CONSTRAINT IF EXISTS ingestion_memory_candidates_initial_state_check;
ALTER TABLE soul_v3.ingestion_memory_candidates
  ADD CONSTRAINT ingestion_memory_candidates_importance_advisory_check
    CHECK (importance_advisory),
  ADD CONSTRAINT ingestion_memory_candidates_confidence_factors_check
    CHECK (jsonb_typeof(confidence_factors) = 'object'),
  ADD CONSTRAINT ingestion_memory_candidates_initial_state_check
    CHECK (initial_state IN ('candidate','review_blocked'));

DROP POLICY IF EXISTS ingestion_candidates_processor_insert
  ON soul_v3.ingestion_memory_candidates;
CREATE POLICY ingestion_candidates_processor_insert
  ON soul_v3.ingestion_memory_candidates
  FOR INSERT TO pr_ingestion_processor
  WITH CHECK (
    tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
    AND approval_verified = false
    AND importance_advisory
    AND initial_state IN ('candidate','review_blocked')
  );

COMMIT;
