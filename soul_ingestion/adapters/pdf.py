"""Bounded PDF text extraction without JavaScript, macros or network access."""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID

from pypdf import PdfReader

from ..contracts import RawArtifact, Scope, Sensitivity, SourceDescriptor, SourceKind, TrustTier, sha256_bytes


class PdfAdapter:
    adapter_id = "pdf_v1"
    adapter_version = "1.0.0"
    max_bytes = 50 * 1024 * 1024
    max_pages = 1000

    def acquire(
        self,
        content: bytes,
        *,
        tenant_id: UUID,
        owner_agent: str | None,
        scope: Scope,
        source_ref: str | None = None,
        title: str | None = None,
        language: str = "und",
        sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL,
        trust_tier: TrustTier = TrustTier.OWNER_VERIFIED,
        observed_at: datetime | None = None,
    ) -> RawArtifact:
        raw = bytes(content)
        if len(raw) > self.max_bytes:
            raise ValueError("PDF exceeds 50 MiB hard limit")
        reader = PdfReader(BytesIO(raw), strict=True)
        if reader.is_encrypted:
            raise ValueError("encrypted PDFs require an explicit future capability")
        if len(reader.pages) > self.max_pages:
            raise ValueError("PDF exceeds 1000 page hard limit")
        parts: list[str] = []
        page_ranges: list[dict[str, int]] = []
        cursor = 0
        for page_number, page in enumerate(reader.pages, start=1):
            page_text = page.extract_text(extraction_mode="layout") if "/Contents" in page else ""
            fragment = f"[page={page_number}]\n{page_text.strip()}"
            if parts:
                cursor += 2
            start = cursor
            parts.append(fragment)
            cursor += len(fragment)
            page_ranges.append({"page": page_number, "start_char": start, "end_char": cursor})
        extracted = "\n\n".join(parts)
        source = SourceDescriptor(
            source_kind=SourceKind.PDF,
            source_ref=source_ref or f"sha256:{sha256_bytes(raw)}",
            tenant_id=tenant_id,
            owner_agent=owner_agent,
            scope=scope,
            observed_at=observed_at or datetime.now(UTC),
            trust_tier=trust_tier,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
        )
        metadata_title = getattr(reader.metadata, "title", None) if reader.metadata else None
        return RawArtifact(
            source=source,
            media_type="application/pdf",
            content=raw,
            extracted_text=extracted,
            title=title or metadata_title,
            language=language,
            sensitivity=sensitivity,
            metadata={"page_count": len(reader.pages), "page_ranges": page_ranges},
        )
