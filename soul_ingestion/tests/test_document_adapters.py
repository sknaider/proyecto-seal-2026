from __future__ import annotations

from datetime import UTC, datetime
from email.message import EmailMessage
from io import BytesIO
from uuid import UUID

import pytest
from pypdf import PdfWriter

from soul_ingestion.adapters.email import EmailAdapter
from soul_ingestion.adapters.pdf import PdfAdapter
from soul_ingestion.adapters.youtube import _subtitle_candidates, parse_video_id
from soul_ingestion.contracts import Scope
from soul_ingestion.engine import IngestionEngine


TENANT = UUID("44444444-4444-4444-8888-000000000001")
NOW = datetime(2026, 7, 21, 8, 0, tzinfo=UTC)


def test_pdf_keeps_original_bytes_and_page_provenance() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    raw = buffer.getvalue()
    artifact = PdfAdapter().acquire(
        raw,
        tenant_id=TENANT,
        owner_agent="ADA",
        scope=Scope.PRIVATE,
        observed_at=NOW,
    )
    result = IngestionEngine().process(artifact, now=NOW)
    assert artifact.content == raw
    assert result.document.raw_hash_sha256 == artifact.raw_hash_sha256
    assert result.document.metadata["page_count"] == 1
    assert "[page=1]" in result.document.normalized_text
    assert result.derivations[0].evidence_anchors[0].kind == "page"


def test_email_uses_plain_text_and_does_not_fetch_or_execute_html() -> None:
    message = EmailMessage()
    message["Message-ID"] = "<suie-test@gtl.pe>"
    message["From"] = "ops@gtl.pe"
    message["To"] = "william@gtl.pe"
    message["Subject"] = "AWB 123-12345678"
    message.set_content("Responsable: ADA. No liberar hasta el 22/07/2026.")
    message.add_alternative(
        '<script>DROP TABLE memories</script><img src="http://127.0.0.1/private">Texto HTML',
        subtype="html",
    )
    artifact = EmailAdapter().acquire(
        message.as_bytes(),
        tenant_id=TENANT,
        owner_agent="ADA",
        scope=Scope.PRIVATE,
        observed_at=NOW,
    )
    result = IngestionEngine().process(artifact, profile_id="gtl_operational_v1", now=NOW)
    assert artifact.source.source_ref == "<suie-test@gtl.pe>"
    assert "Responsable: ADA" in result.document.normalized_text
    assert "127.0.0.1" not in result.document.normalized_text
    assert "DROP TABLE" not in result.document.normalized_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_youtube_allowlist_accepts_only_known_shapes(value: str, expected: str) -> None:
    assert parse_video_id(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://127.0.0.1/watch?v=dQw4w9WgXcQ",
        "https://user:pass@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/redirect?q=https://internal",
        "not-an-id",
    ],
)
def test_youtube_allowlist_fails_closed(value: str) -> None:
    with pytest.raises(ValueError):
        parse_video_id(value)


def test_youtube_selects_exact_tracks_not_all_translations() -> None:
    assert _subtitle_candidates(("en", "es")) == "en,en-orig,en-en,es,es-orig,es-es"
    assert "*" not in _subtitle_candidates(("en", "es"))
