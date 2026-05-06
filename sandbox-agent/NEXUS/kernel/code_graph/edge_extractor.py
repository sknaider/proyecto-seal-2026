"""Cathedral II Layer 5 — call edge extractor using tree-sitter.

Walks a parsed AST and emits structural call edges. Per-language configs
map language names to the call-expression node types and callee field names.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import re

CALL_CONFIG: dict[str, dict] = {
    "python":     {"call_types": {"call"},            "callee_field": "function"},
    "typescript": {"call_types": {"call_expression"}, "callee_field": "function"},
    "tsx":        {"call_types": {"call_expression"}, "callee_field": "function"},
    "javascript": {"call_types": {"call_expression"}, "callee_field": "function"},
    "go":         {"call_types": {"call_expression"}, "callee_field": "function"},
    "rust":       {"call_types": {"call_expression", "method_call_expression"}, "callee_field": "function"},
    "java":       {"call_types": {"method_invocation"}, "callee_field": "name"},
    "ruby":       {"call_types": {"call", "method_call"}, "callee_field": "method"},
}

IDENTIFIER_TYPES = {
    "identifier", "property_identifier", "field_identifier",
    "simple_identifier", "type_identifier", "constant",
    "shorthand_property_identifier", "scoped_identifier",
}

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ExtractedEdge:
    call_site_start_byte: int
    to_symbol: str
    edge_type: str = "calls"


def _extract_callee_name(node, callee_field: str) -> Optional[str]:
    callee = node.child_by_field_name(callee_field)
    if callee is None:
        return None

    cur = callee
    for _ in range(6):
        if cur is None:
            return None
        node_type = cur.type
        if node_type in IDENTIFIER_TYPES:
            text = cur.text.decode("utf-8", errors="replace")
            last_seg = re.split(r"[:.]", text)[-1]
            return _sanitize(last_seg)
        if node_type in ("member_expression", "field_expression"):
            prop = cur.child_by_field_name("property") or cur.child_by_field_name("field")
            if prop:
                cur = prop
                continue
            return None
        if node_type in ("scoped_call_expression", "scoped_identifier"):
            name = cur.child_by_field_name("name")
            if name:
                cur = name
                continue
            return None
        # fallback: take last identifier token from raw text
        text = cur.text.decode("utf-8", errors="replace")
        m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", text)
        return _sanitize(m.group(1)) if m else None
    return None


def _sanitize(s: str) -> Optional[str]:
    return s if _IDENT_RE.match(s) else None


def extract_call_edges(root_node, language: str) -> list[ExtractedEdge]:
    cfg = CALL_CONFIG.get(language)
    if cfg is None:
        return []

    call_types: set[str] = cfg["call_types"]
    callee_field: str = cfg["callee_field"]
    out: list[ExtractedEdge] = []

    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type in call_types:
            callee = _extract_callee_name(node, callee_field)
            if callee:
                out.append(ExtractedEdge(
                    call_site_start_byte=node.start_byte,
                    to_symbol=callee,
                ))
        for child in node.children:
            stack.append(child)
    return out


def find_chunk_for_byte(byte_offset: int, source: bytes,
                        chunks: list[dict]) -> Optional[int]:
    """Map a byte offset to the innermost chunk index that contains it."""
    line = 1
    for i in range(min(byte_offset, len(source))):
        if source[i] == 10:  # newline
            line += 1

    best: Optional[int] = None
    best_span = float("inf")
    for i, c in enumerate(chunks):
        start, end = c.get("start_line", 0), c.get("end_line", 0)
        if line < start or line > end:
            continue
        span = end - start
        if span < best_span:
            best_span = span
            best = i
    return best
