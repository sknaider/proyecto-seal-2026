"""Direct UTF-8/UTF-16 text adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from ..contracts import RawArtifact, Scope, Sensitivity, SourceDescriptor, SourceKind, TrustTier, sha256_bytes


class TextAdapter:
    adapter_id = "text_v1"
    adapter_version = "1.0.0"
    max_bytes = 5 * 1024 * 1024

    def acquire(
        self,
        text: str | bytes,
        *,
        tenant_id: UUID,
        owner_agent: str | None,
        scope: Scope = Scope.PRIVATE,
        source_ref: str | None = None,
        media_type: str = "text/plain",
        language: str = "es",
        title: str | None = None,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        trust_tier: TrustTier = TrustTier.OWNER_VERIFIED,
        observed_at: datetime | None = None,
    ) -> RawArtifact:
        content = text.encode("utf-8") if isinstance(text, str) else bytes(text)
        if len(content) > self.max_bytes:
            raise ValueError(f"direct text exceeds {self.max_bytes} byte limit")
        reference = source_ref or f"sha256:{sha256_bytes(content)}"
        source_kind = SourceKind.MARKDOWN if media_type == "text/markdown" else SourceKind.TEXT
        source = SourceDescriptor(
            source_kind=source_kind,
            source_ref=reference,
            source_uri=None,
            tenant_id=tenant_id,
            owner_agent=owner_agent,
            scope=scope,
            observed_at=observed_at or datetime.now(UTC),
            trust_tier=trust_tier,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
        )
        return RawArtifact(
            source=source,
            media_type=media_type,
            content=content,
            title=title,
            language=language,
            sensitivity=sensitivity,
        )
