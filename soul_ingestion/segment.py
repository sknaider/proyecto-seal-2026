"""Structure-aware paragraphs, sections and sentence anchors."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contracts import (
    DocumentSegment,
    EvidenceAnchor,
    NormalizedDocument,
    ProtectedSpan,
    sha256_text,
    stable_uuid,
)
from .protected_spans import find_protected_spans, spans_within


HEADING_RE = re.compile(
    r"^(?:#{1,6}\s+.+|(?:\d+(?:\.\d+)*[.)]?\s+)?[A-ZÁÉÍÓÚÜÑ][^.!?]{0,100}:?)$"
)
SENTENCE_RE = re.compile(r".+?(?:[.!?]+(?=\s+|$)|\n+|$)", re.DOTALL | re.UNICODE)


@dataclass(frozen=True, slots=True)
class Sentence:
    ordinal: int
    text: str
    start_char: int
    end_char: int
    section: str
    heading_path: tuple[str, ...]
    protected_spans: tuple[ProtectedSpan, ...]


def _raw_offset(document: NormalizedDocument, position: int) -> int:
    return document.offset_map[min(position, len(document.offset_map) - 1)].raw_offset


def _anchor(document: NormalizedDocument, start: int, end: int, kind: str = "char_range") -> EvidenceAnchor:
    fragment = document.normalized_text[start:end]
    anchor_kind = kind
    anchor_start = str(start)
    anchor_end = str(end)
    for page in document.metadata.get("page_ranges", []):
        if start >= page["start_char"] and end <= page["end_char"]:
            anchor_kind = "page"
            anchor_start = anchor_end = str(page["page"])
            break
    for message in document.metadata.get("message_ranges", []):
        if start >= message["start_char"] and end <= message["end_char"]:
            anchor_kind = "message"
            anchor_start = anchor_end = str(message["message_id"])
            break
    for timed in document.metadata.get("timed_segments", []):
        if start >= timed.get("start_char", -1) and end <= timed.get("end_char", -1):
            anchor_kind = "timestamp"
            anchor_start = f"{timed['start_ms'] / 1000:.3f}"
            anchor_end = f"{(timed['start_ms'] + timed['duration_ms']) / 1000:.3f}"
            break
    return EvidenceAnchor(
        kind=anchor_kind,
        start=anchor_start,
        end=anchor_end,
        start_char=start,
        end_char=end,
        raw_start_char=_raw_offset(document, start),
        raw_end_char=_raw_offset(document, end),
        text_hash_sha256=sha256_text(fragment),
    )


def split_sentences(document: NormalizedDocument) -> tuple[Sentence, ...]:
    text = document.normalized_text
    all_spans = find_protected_spans(text)
    sentences: list[Sentence] = []
    heading_path: tuple[str, ...] = ()
    current_section = "__root__"

    # Keep the final paragraph even when the file ends in a newline. ``\s*``
    # in the separator would swallow multiple blocks, so only horizontal
    # whitespace is accepted between the two delimiter newlines.
    paragraph_re = re.compile(r"\S.*?(?=\n[ \t]*\n|\Z)", re.DOTALL)
    for paragraph in paragraph_re.finditer(text):
        paragraph_text = paragraph.group(0)
        stripped = paragraph_text.strip()
        leading = len(paragraph_text) - len(paragraph_text.lstrip())
        paragraph_start = paragraph.start() + leading
        if HEADING_RE.match(stripped) and "\n" not in stripped:
            heading = stripped.lstrip("#").strip().rstrip(":")
            heading_path = (heading,)
            current_section = heading
            continue
        for match in SENTENCE_RE.finditer(stripped):
            raw_sentence = match.group(0)
            sentence_text = raw_sentence.strip()
            if not sentence_text:
                continue
            if sentence_text.endswith(":") and len(sentence_text) <= 100:
                heading = sentence_text.rstrip(":").strip()
                heading_path = (heading,)
                current_section = heading
                continue
            local_leading = len(raw_sentence) - len(raw_sentence.lstrip())
            start = paragraph_start + match.start() + local_leading
            end = start + len(sentence_text)
            sentences.append(
                Sentence(
                    ordinal=len(sentences),
                    text=sentence_text,
                    start_char=start,
                    end_char=end,
                    section=current_section,
                    heading_path=heading_path,
                    protected_spans=spans_within(all_spans, start, end),
                )
            )
    if not sentences and text.strip():
        start = len(text) - len(text.lstrip())
        sentence_text = text.strip()
        end = start + len(sentence_text)
        sentences.append(
            Sentence(
                ordinal=0,
                text=sentence_text,
                start_char=start,
                end_char=end,
                section="__root__",
                heading_path=(),
                protected_spans=spans_within(all_spans, start, end),
            )
        )
    return tuple(sentences)


def build_segments(
    document: NormalizedDocument,
    *,
    target_chars: int = 3000,
    max_chars: int = 4000,
) -> tuple[DocumentSegment, ...]:
    if not 500 <= target_chars <= max_chars <= 10_000:
        raise ValueError("invalid segment size policy")
    sentences = split_sentences(document)
    if not sentences:
        return ()
    groups: list[list[Sentence]] = []
    current: list[Sentence] = []
    current_chars = 0
    for sentence in sentences:
        projected = current_chars + (1 if current else 0) + len(sentence.text)
        section_changed = current and sentence.section != current[-1].section
        if current and (projected > max_chars or (section_changed and current_chars >= target_chars // 2)):
            groups.append(current)
            current = []
            current_chars = 0
        current.append(sentence)
        current_chars += (1 if current_chars else 0) + len(sentence.text)
        if current_chars >= target_chars:
            groups.append(current)
            current = []
            current_chars = 0
    if current:
        groups.append(current)

    segments: list[DocumentSegment] = []
    for ordinal, group in enumerate(groups):
        start = group[0].start_char
        end = group[-1].end_char
        segment_text = document.normalized_text[start:end]
        segment_id = stable_uuid("segment", str(document.document_id), str(ordinal), sha256_text(segment_text))
        segments.append(
            DocumentSegment(
                segment_id=segment_id,
                document_id=document.document_id,
                ordinal=ordinal,
                text=segment_text,
                text_hash_sha256=sha256_text(segment_text),
                start_char=start,
                end_char=end,
                anchor=_anchor(document, start, end),
                heading_path=group[0].heading_path,
                protected_spans=tuple(span for sentence in group for span in sentence.protected_spans),
            )
        )
    if len(segments) > 10_000:
        raise ValueError("segment count exceeds hard limit")
    return tuple(segments)


def sentence_anchor(document: NormalizedDocument, sentence: Sentence) -> EvidenceAnchor:
    return _anchor(document, sentence.start_char, sentence.end_char)
