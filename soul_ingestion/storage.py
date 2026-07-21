"""Least-privilege PostgreSQL staging repository."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

import asyncpg

from .contracts import IngestResult, RawArtifact


class StagingRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.pool = pool

    @asynccontextmanager
    async def _processor_transaction(self, tenant_id: UUID) -> AsyncIterator[asyncpg.Connection]:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE pr_ingestion_processor")
                await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
                yield connection

    async def persist(self, artifact: RawArtifact, result: IngestResult) -> bool:
        """Persist one immutable result. Returns True for idempotent replay."""

        document = result.document
        tenant_id = document.source.tenant_id
        async with self._processor_transaction(tenant_id) as connection:
            inserted = await connection.fetchval(
                """
                INSERT INTO soul_v3.ingestion_documents (
                  document_id, tenant_id, owner_agent, scope, source_kind, source_ref,
                  source_uri, source_descriptor, media_type, language, title, authors,
                  raw_artifact_ref, raw_hash_sha256, normalized_hash_sha256,
                  normalization_profile, identity_profile, normalized_text, sensitivity,
                  retention_policy, adapter_id, adapter_version, state, metadata, ingested_at
                ) VALUES (
                  $1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,$10,$11,$12::jsonb,$13,$14,$15,
                  $16,$17,$18,$19,$20,$21,$22,$23,$24::jsonb,$25
                )
                ON CONFLICT ON CONSTRAINT ingestion_documents_idempotency_key DO NOTHING
                RETURNING document_id
                """,
                document.document_id,
                tenant_id,
                document.source.owner_agent,
                document.source.scope.value,
                document.source.source_kind.value,
                document.source.source_ref,
                document.source.source_uri,
                document.source.model_dump(mode="json"),
                document.media_type,
                document.language,
                document.title,
                list(document.authors),
                document.raw_artifact_ref,
                document.raw_hash_sha256,
                document.normalized_hash_sha256,
                document.normalization_profile,
                document.identity_profile,
                document.normalized_text,
                document.sensitivity.value,
                document.retention_policy,
                document.source.adapter_id,
                document.source.adapter_version,
                result.state,
                document.metadata,
                document.ingested_at,
            )
            replay = inserted is None
            if replay:
                existing_hash = await connection.fetchval(
                    "SELECT normalized_hash_sha256 FROM soul_v3.ingestion_documents WHERE document_id=$1",
                    document.document_id,
                )
                if existing_hash != document.normalized_hash_sha256:
                    raise RuntimeError("document UUID collision with different normalized bytes")
            else:
                await connection.executemany(
                    """
                    INSERT INTO soul_v3.ingestion_segments (
                      segment_id, document_id, tenant_id, ordinal, text, text_hash_sha256,
                      start_char, end_char, anchor, heading_path, protected_spans
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11::jsonb)
                    """,
                    [
                        (
                            segment.segment_id,
                            document.document_id,
                            tenant_id,
                            segment.ordinal,
                            segment.text,
                            segment.text_hash_sha256,
                            segment.start_char,
                            segment.end_char,
                            segment.anchor.model_dump(mode="json"),
                            list(segment.heading_path),
                            [span.model_dump(mode="json") for span in segment.protected_spans],
                        )
                        for segment in result.segments
                    ],
                )

            new_derivations: set[UUID] = set()
            for derivation in result.derivations:
                derivation_inserted = await connection.fetchval(
                    """
                    INSERT INTO soul_v3.ingestion_derivations (
                      derivation_id, document_id, tenant_id, kind, processor_id,
                      processor_version, config_hash_sha256, input_hash_sha256,
                      output_hash_sha256, content, coverage, evidence_anchors, created_at
                    ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::jsonb,$13)
                    ON CONFLICT (derivation_id) DO NOTHING
                    RETURNING derivation_id
                    """,
                    derivation.derivation_id,
                    document.document_id,
                    tenant_id,
                    derivation.kind,
                    derivation.processor_id,
                    derivation.processor_version,
                    derivation.config_hash_sha256,
                    derivation.input_hash_sha256,
                    derivation.output_hash_sha256,
                    derivation.content,
                    derivation.coverage.model_dump(mode="json"),
                    [anchor.model_dump(mode="json") for anchor in derivation.evidence_anchors],
                    derivation.created_at,
                )
                if derivation_inserted is not None:
                    new_derivations.add(derivation_inserted)

            new_candidates = []
            if result.candidates:
                for candidate in result.candidates:
                    candidate_inserted = await connection.fetchval(
                        """
                        INSERT INTO soul_v3.ingestion_memory_candidates (
                          candidate_id, document_id, derivation_id, tenant_id, proposed_agent,
                          proposed_category, proposed_content, proposed_importance,
                          importance_advisory, importance_method, confidence,
                          confidence_scorer, confidence_factors, source_anchors,
                          risk_flags, initial_state, approval_verified
                        ) VALUES (
                          $1,$2,$3,$4,$5,$6,$7,$8,true,$9,$10,$11,$12::jsonb,
                          $13::jsonb,$14::jsonb,$15,false
                        )
                        ON CONFLICT (candidate_id) DO NOTHING
                        RETURNING candidate_id
                        """,
                        candidate.candidate_id,
                        document.document_id,
                        candidate.derivation_id,
                        tenant_id,
                        candidate.proposed_agent,
                        candidate.proposed_category,
                        candidate.proposed_content,
                        candidate.proposed_importance,
                        candidate.importance_method,
                        candidate.confidence,
                        candidate.confidence_scorer,
                        candidate.confidence_factors,
                        [anchor.model_dump(mode="json") for anchor in candidate.source_anchors],
                        list(candidate.risk_flags),
                        candidate.state,
                    )
                    if candidate_inserted is not None:
                        new_candidates.append(candidate)
            if new_candidates:
                await connection.executemany(
                    """
                    INSERT INTO soul_v3.ingestion_state_events (
                      tenant_id, document_id, candidate_id, event_type, actor, reason, metadata
                    ) VALUES ($1,$2,$3,'candidate_created',$4,$5,$6::jsonb)
                    """,
                    [
                        (
                            tenant_id,
                            document.document_id,
                            candidate.candidate_id,
                            document.source.owner_agent or "SYSTEM",
                            "deterministic candidate proposal; not approved",
                            {"risk_flags": list(candidate.risk_flags)},
                        )
                        for candidate in new_candidates
                    ],
                )
            changed = not replay or bool(new_derivations) or bool(new_candidates)
            if changed:
                await connection.execute(
                """
                INSERT INTO soul_v3.ingestion_state_events (
                  tenant_id, document_id, event_type, actor, reason, metadata
                ) VALUES ($1,$2,$3,$4,$5,$6::jsonb)
                """,
                    tenant_id,
                    document.document_id,
                    "processed" if result.state == "processed" else "quarantined",
                    document.source.owner_agent or "SYSTEM",
                    "extractive_v1 completed" if result.ok else "coverage gate failed",
                    {
                        "raw_hash_sha256": artifact.raw_hash_sha256,
                        "new_derivations": [str(value) for value in sorted(new_derivations, key=str)],
                    },
                )
            return not changed

    async def health(self, tenant_id: UUID) -> dict[str, object]:
        async with self._processor_transaction(tenant_id) as connection:
            server = await connection.fetchrow(
                "SELECT current_database() db, session_user login, current_user db_role, "
                "current_setting('server_version') version"
            )
            table_count = await connection.fetchval(
                """
                SELECT count(*) FROM information_schema.tables
                WHERE table_schema='soul_v3' AND table_name LIKE 'ingestion_%'
                """
            )
            memory_write = await connection.fetchval(
                "SELECT has_table_privilege(current_user, 'soul_v3.memories', 'INSERT')"
            )
            return {
                "database": server["db"],
                "login": server["login"],
                "role": server["db_role"],
                "server_version": server["version"],
                "staging_tables": table_count,
                "memory_insert_privilege": memory_write,
            }

    async def get_document(self, tenant_id: UUID, document_id: UUID) -> dict[str, object] | None:
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE pr_ingestion_reader")
                await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
                row = await connection.fetchrow(
                    """
                    SELECT document_id, tenant_id, owner_agent, scope, trust_tier, source_kind,
                           source_ref, source_uri, media_type, language, title,
                           raw_artifact_ref, raw_hash_sha256, normalized_hash_sha256,
                           normalization_profile, identity_profile, sensitivity, state,
                           ingested_at, created_at
                    FROM soul_v3.ingestion_documents WHERE document_id=$1
                    """,
                    document_id,
                )
                if row is None:
                    return None
                derivations = await connection.fetch(
                    """
                    SELECT derivation_id, kind, processor_id, processor_version,
                           output_hash_sha256, content, coverage, created_at
                    FROM soul_v3.ingestion_derivations
                    WHERE document_id=$1 ORDER BY created_at, derivation_id
                    """,
                    document_id,
                )
                segments = await connection.fetch(
                    """
                    SELECT segment_id, ordinal, text, text_hash_sha256, start_char,
                           end_char, anchor, heading_path, protected_spans, created_at
                    FROM soul_v3.ingestion_segments
                    WHERE document_id=$1 ORDER BY ordinal
                    """,
                    document_id,
                )
                candidates = await connection.fetch(
                    """
                    SELECT candidate_id, derivation_id, proposed_agent, proposed_category,
                           proposed_content, proposed_importance, importance_advisory,
                           importance_method, confidence, confidence_scorer,
                           confidence_factors, source_anchors, risk_flags, initial_state,
                           approval_verified, created_at
                    FROM soul_v3.ingestion_memory_candidates
                    WHERE document_id=$1 ORDER BY created_at, candidate_id
                    """,
                    document_id,
                )
                return {
                    **dict(row),
                    "segments": [dict(item) for item in segments],
                    "derivations": [dict(item) for item in derivations],
                    "candidates": [dict(item) for item in candidates],
                }

    async def search_documents(
        self, tenant_id: UUID, query: str, *, limit: int = 10
    ) -> list[dict[str, object]]:
        if not query.strip() or len(query) > 500:
            raise ValueError("search query must contain 1–500 characters")
        if not 1 <= limit <= 50:
            raise ValueError("search limit must be 1–50")
        async with self.pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE pr_ingestion_reader")
                await connection.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
                rows = await connection.fetch(
                    """
                    WITH needle AS (SELECT websearch_to_tsquery('simple', $1) AS q)
                    SELECT d.document_id, d.source_kind, d.source_ref, d.title,
                           d.normalized_hash_sha256, d.sensitivity, d.state,
                           ts_rank_cd(to_tsvector('simple', d.normalized_text), needle.q) AS rank,
                           ts_headline('simple', d.normalized_text, needle.q,
                             'MaxFragments=2,MaxWords=30,MinWords=10,StartSel=<b>,StopSel=</b>') AS excerpt
                    FROM soul_v3.ingestion_documents d, needle
                    WHERE to_tsvector('simple', d.normalized_text) @@ needle.q
                    ORDER BY rank DESC, d.created_at DESC
                    LIMIT $2
                    """,
                    query,
                    limit,
                )
                return [dict(row) for row in rows]
