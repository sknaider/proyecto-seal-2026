"""Shared SOUL Memory SDK contracts.

This module intentionally has no third-party dependencies. It is the stable
edge between SDK clients, server gates, and internal consolidation tooling.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any


MEMORY_CATEGORIES = {
    "fact",
    "preference",
    "decision",
    "insight",
    "correction",
    "milestone",
    "pattern",
    "emotion",
    "trust",
    "humor",
    "dynamic",
    "full_exchange",
}

APPROVAL_RE = re.compile(
    r"^\s*OK\s+ADA\s+aplica\s+lote\s+"
    r"(?P<agent>[A-Z][A-Z0-9_]*)\s+"
    r"(?P<action>[a-z0-9_]+)\s+"
    r"count=(?P<count>[0-9]+)\s*$",
    re.IGNORECASE,
)
MATRIX_PREFIX_RE = re.compile(r"^\s*\[Matrix\]\s*", re.IGNORECASE)


def content_hash(content: str) -> str:
    """Return the canonical SHA-256 content hash used by SOUL gates."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def hash_api_key(api_key: str) -> str:
    """Hash API keys for storage/audit logs; never persist raw keys."""
    if not api_key:
        raise ValueError("api_key is required")
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def safe_excerpt(content: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", content or "").strip()
    return compact[:limit]


def normalize_category(category: str) -> str:
    normalized = (category or "").strip().lower()
    if normalized not in MEMORY_CATEGORIES:
        allowed = ", ".join(sorted(MEMORY_CATEGORIES))
        raise ValueError(f"invalid category {category!r}; allowed: {allowed}")
    return normalized


def validate_importance(importance: int) -> int:
    value = int(importance)
    if value < 1 or value > 10:
        raise ValueError("importance must be between 1 and 10")
    return value


@dataclass(frozen=True)
class ApprovalRequest:
    agent: str
    action: str
    count: int

    @classmethod
    def parse(cls, text: str) -> "ApprovalRequest | None":
        normalized = MATRIX_PREFIX_RE.sub("", text or "", count=1)
        match = APPROVAL_RE.match(normalized)
        if not match:
            return None
        return cls(
            agent=match.group("agent").upper(),
            action=match.group("action").lower(),
            count=int(match.group("count")),
        )


@dataclass(frozen=True)
class MemoryStoreRequest:
    agent_id: str
    content: str
    category: str = "fact"
    importance: int = 5
    memory_type: str = "semantic"
    scope: str | None = None
    valid_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self, include_hash: bool = True) -> dict[str, Any]:
        metadata = dict(self.metadata or {})
        if include_hash:
            metadata.setdefault("content_hash", content_hash(self.content))
        payload: dict[str, Any] = {
            "agent_id": self.agent_id,
            "content": self.content,
            "memory_type": self.memory_type,
            "importance": validate_importance(self.importance),
            "category": normalize_category(self.category),
        }
        if self.scope is not None:
            payload["scope"] = self.scope
        if self.valid_at is not None:
            payload["valid_at"] = self.valid_at
        if metadata:
            payload["metadata"] = metadata
        return payload


@dataclass(frozen=True)
class MemoryRecord:
    id: int | str
    content: str
    category: str | None = None
    importance: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    content_hash: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "MemoryRecord":
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        return cls(
            id=data.get("id", ""),
            content=data.get("content") or data.get("memory") or "",
            category=data.get("category"),
            importance=data.get("importance"),
            metadata=metadata,
            content_hash=data.get("content_hash") or metadata.get("content_hash"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
