-- SUIE identity boundary hardening.
-- Existing documents retain identity profile v1; new documents use v2, whose
-- deterministic UUID includes owner/scope/sensitivity/trust boundaries.

BEGIN;

ALTER TABLE soul_v3.ingestion_documents
  ADD COLUMN IF NOT EXISTS trust_tier text
    GENERATED ALWAYS AS (source_descriptor->>'trust_tier') STORED,
  ADD COLUMN IF NOT EXISTS identity_profile text NOT NULL
    DEFAULT 'document_identity_v1';

ALTER TABLE soul_v3.ingestion_documents
  ALTER COLUMN identity_profile SET DEFAULT 'document_identity_v2',
  DROP CONSTRAINT IF EXISTS ingestion_documents_trust_tier_check,
  DROP CONSTRAINT IF EXISTS ingestion_documents_identity_profile_check,
  DROP CONSTRAINT IF EXISTS ingestion_documents_idempotency_key;

ALTER TABLE soul_v3.ingestion_documents
  ADD CONSTRAINT ingestion_documents_trust_tier_check CHECK (
    trust_tier IN ('owner_verified','team_verified','external_trusted','external_untrusted')
  ),
  ADD CONSTRAINT ingestion_documents_identity_profile_check CHECK (
    identity_profile IN ('document_identity_v1','document_identity_v2')
  ),
  ADD CONSTRAINT ingestion_documents_idempotency_key UNIQUE NULLS NOT DISTINCT (
    tenant_id, owner_agent, scope, sensitivity, trust_tier, source_kind,
    source_ref, adapter_id, adapter_version, raw_hash_sha256,
    normalization_profile, identity_profile
  );

COMMIT;
