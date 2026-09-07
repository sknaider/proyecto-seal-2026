"""Safe MIME email adapter: text only, no remote resource loading."""

from __future__ import annotations

from datetime import UTC, datetime
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from uuid import UUID

from bs4 import BeautifulSoup

from ..contracts import RawArtifact, Scope, Sensitivity, SourceDescriptor, SourceKind, TrustTier, sha256_bytes


class EmailAdapter:
    adapter_id = "email_v1"
    adapter_version = "1.0.0"
    max_bytes = 50 * 1024 * 1024
    max_parts = 100

    @staticmethod
    def _header(value: str | None) -> str | None:
        if value is None:
            return None
        return str(make_header(decode_header(value))).replace("\x00", "�")[:4096]

    def acquire(
        self,
        content: bytes,
        *,
        tenant_id: UUID,
        owner_agent: str | None,
        scope: Scope,
        sensitivity: Sensitivity = Sensitivity.CONFIDENTIAL,
        observed_at: datetime | None = None,
    ) -> RawArtifact:
        raw = bytes(content)
        if len(raw) > self.max_bytes:
            raise ValueError("email exceeds 50 MiB hard limit")
        message = BytesParser(policy=policy.default).parsebytes(raw)
        parts = list(message.walk())
        if len(parts) > self.max_parts:
            raise ValueError("email has too many MIME parts")

        plain: list[str] = []
        html: list[str] = []
        attachments: list[dict[str, object]] = []
        for part in parts:
            if part.is_multipart():
                continue
            disposition = part.get_content_disposition()
            media_type = part.get_content_type()
            filename = self._header(part.get_filename())
            payload = part.get_payload(decode=True) or b""
            if disposition == "attachment" or filename:
                attachments.append(
                    {
                        "filename": filename,
                        "media_type": media_type,
                        "size": len(payload),
                        "sha256": sha256_bytes(payload),
                    }
                )
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                decoded = payload.decode(charset, errors="strict")
            except (LookupError, UnicodeDecodeError) as exc:
                raise ValueError(f"email body has invalid declared charset: {charset}") from exc
            if media_type == "text/plain":
                plain.append(decoded)
            elif media_type == "text/html":
                soup = BeautifulSoup(decoded, "html.parser")
                for tag in soup(["script", "style", "iframe", "object", "embed"]):
                    tag.decompose()
                html.append(soup.get_text("\n", strip=True))
        body = "\n\n".join(plain or html).strip()
        if not body:
            raise ValueError("email contains no safe textual body")
        message_id = self._header(message.get("Message-ID"))
        source_ref = message_id or f"sha256:{sha256_bytes(raw)}"
        source = SourceDescriptor(
            source_kind=SourceKind.EMAIL,
            source_ref=source_ref,
            tenant_id=tenant_id,
            owner_agent=owner_agent,
            scope=scope,
            observed_at=observed_at or datetime.now(UTC),
            trust_tier=TrustTier.EXTERNAL_UNTRUSTED,
            adapter_id=self.adapter_id,
            adapter_version=self.adapter_version,
        )
        headers = {
            key: self._header(message.get(key))
            for key in ("From", "To", "Cc", "Date", "Subject", "Message-ID")
            if message.get(key)
        }
        rendered_headers = "\n".join(f"{key}: {value}" for key, value in headers.items())
        extracted = f"{rendered_headers}\n\n{body}" if rendered_headers else body
        return RawArtifact(
            source=source,
            media_type="message/rfc822",
            content=raw,
            extracted_text=extracted,
            title=headers.get("Subject"),
            language="und",
            sensitivity=sensitivity,
            metadata={"headers": headers, "attachments": attachments},
        )
