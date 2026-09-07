"""Unicode-safe text normalization with a reproducible raw-offset map."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime

from .contracts import NormalizedDocument, OffsetPoint, RawArtifact, sha256_text, stable_uuid


MAX_NORMALIZED_CHARS = 5_000_000


class NormalizationError(ValueError):
    pass


def decode_text(content: bytes) -> str:
    """Decode deterministic text inputs without guessing arbitrary encodings."""

    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NormalizationError("input is not valid UTF-8/UTF-16 text") from exc


def _normalization_units(raw: str) -> list[tuple[str, int, int]]:
    """Split raw text into units whose normalized bytes have one raw span."""

    units: list[tuple[str, int, int]] = []
    index = 0
    while index < len(raw):
        start = index
        char = raw[index]
        if char == "\r":
            index += 2 if index + 1 < len(raw) and raw[index + 1] == "\n" else 1
            units.append(("\n", start, index))
            continue
        if char == "\n":
            index += 1
            units.append(("\n", start, index))
            continue
        if char == "\x00":
            index += 1
            units.append(("�", start, index))
            continue
        index += 1
        while index < len(raw) and unicodedata.combining(raw[index]):
            index += 1
        units.append((unicodedata.normalize("NFC", raw[start:index]), start, index))
    return units


def _normalize_with_offsets(raw: str) -> tuple[str, tuple[OffsetPoint, ...]]:
    normalized_parts: list[str] = []
    raw_boundaries = [0]
    for output, raw_start, raw_end in _normalization_units(raw):
        normalized_parts.append(output)
        width = len(output)
        raw_width = raw_end - raw_start
        for offset in range(1, width + 1):
            raw_boundaries.append(raw_start + round(raw_width * offset / width))
    normalized = "".join(normalized_parts)
    # NFC is idempotent here; assert the unit construction did not miss a
    # cross-unit composition (for example an exotic combining sequence).
    if unicodedata.normalize("NFC", normalized) != normalized:
        raise NormalizationError("unable to create an exact NFC offset map")
    return normalized, tuple(
        OffsetPoint(normalized_offset=index, raw_offset=raw_offset)
        for index, raw_offset in enumerate(raw_boundaries)
    )


def normalize_artifact(artifact: RawArtifact, *, now: datetime | None = None) -> NormalizedDocument:
    raw_text = artifact.extracted_text if artifact.extracted_text is not None else decode_text(artifact.content)
    normalized, offset_map = _normalize_with_offsets(raw_text)
    if len(normalized) > MAX_NORMALIZED_CHARS:
        raise NormalizationError(
            f"normalized text exceeds hard limit ({len(normalized)} > {MAX_NORMALIZED_CHARS})"
        )
    normalized_hash = sha256_text(normalized)
    document_id = stable_uuid(
        "document",
        str(artifact.source.tenant_id),
        artifact.source.source_kind.value,
        artifact.source.source_ref,
        artifact.source.adapter_id,
        artifact.source.adapter_version,
        artifact.source.owner_agent or "",
        artifact.source.scope.value,
        artifact.source.trust_tier.value,
        artifact.sensitivity.value,
        artifact.raw_hash_sha256,
        "unicode_nfc_text_v2",
        "document_identity_v2",
    )
    return NormalizedDocument(
        document_id=document_id,
        source=artifact.source,
        media_type=artifact.media_type,
        language=artifact.language,
        title=artifact.title,
        authors=artifact.authors,
        normalized_text=normalized,
        raw_artifact_ref=f"artifact://sha256/{artifact.raw_hash_sha256}",
        raw_hash_sha256=artifact.raw_hash_sha256,
        normalized_hash_sha256=normalized_hash,
        char_count=len(normalized),
        byte_count=len(normalized.encode("utf-8")),
        sensitivity=artifact.sensitivity,
        retention_policy=artifact.retention_policy,
        ingested_at=now or datetime.now(UTC),
        offset_map=offset_map,
        metadata=artifact.metadata,
    )
