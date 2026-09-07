"""Deterministic discovery of evidence that summaries must preserve."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .contracts import ProtectedSpan


FLAGS = re.IGNORECASE | re.UNICODE

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "awb",
        re.compile(
            r"(?<!\w)(?:\d{3}[- ]\d{8}|(?:HAWB|MAWB|AWB)\s*[:#-]?\s*"
            r"(?=[A-Z0-9-]{6,20}(?!\w))(?=[A-Z0-9-]*\d)[A-Z0-9-]{6,20})(?!\w)",
            FLAGS,
        ),
    ),
    ("date", re.compile(r"(?<!\w)(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+de\s+[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:\s+de\s+\d{4})?)(?!\w)", FLAGS)),
    ("deadline", re.compile(r"\b(?:deadline|vence|vencimiento|hasta|antes del?)\s+(?:el\s+)?(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}:\d{2}|[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)\b", FLAGS)),
    (
        "amount",
        re.compile(
            r"(?<!\w)(?:US\$|S/\.?|USD|PEN|EUR|€|\$)\s*"
            r"(?:\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
            r"(?:\s*(?:mil|millones?))?(?!\w)",
            FLAGS,
        ),
    ),
    (
        "quantity",
        re.compile(
            r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:kg|kgs?|lb|lbs|piezas?|pieces?|pcs|pallets?)\b",
            FLAGS,
        ),
    ),
    ("dose", re.compile(r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:mg|mcg|µg|g|ml|mL|UI|U)\b(?:\s*(?:cada|c/|x)\s*\d+\s*h(?:oras?)?)?", FLAGS)),
    ("owner", re.compile(r"\b(?:responsable|owner|asignado a|a cargo de)\s*[:=-]?\s*[A-ZÁÉÍÓÚÜÑ][\wÁÉÍÓÚÜÑáéíóúüñ.-]{1,63}", FLAGS)),
    ("negation", re.compile(r"\b(?:no|nunca|jamás|sin|niega|negativo para|descartado|prohibido)\b", FLAGS)),
    ("id", re.compile(r"(?<!\w)(?:ID|DOI|RUC|DNI|PID|SHA(?:-?256)?|Paper)\s*[:#=-]?\s*[A-Z0-9][A-Z0-9./:_-]{2,127}(?!\w)", FLAGS)),
)


def find_protected_spans(text: str, *, base_offset: int = 0) -> tuple[ProtectedSpan, ...]:
    candidates: list[ProtectedSpan] = []
    occupied: set[tuple[int, int, str]] = set()
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            key = (match.start(), match.end(), kind)
            if key in occupied:
                continue
            occupied.add(key)
            candidates.append(
                ProtectedSpan(
                    kind=kind,
                    text=match.group(0),
                    start=base_offset + match.start(),
                    end=base_offset + match.end(),
                )
            )
    return tuple(sorted(candidates, key=lambda span: (span.start, span.end, span.kind)))


def spans_within(
    spans: Iterable[ProtectedSpan], start: int, end: int
) -> tuple[ProtectedSpan, ...]:
    return tuple(span for span in spans if span.start >= start and span.end <= end)
