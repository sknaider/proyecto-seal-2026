"""
SEAL Memory Python SDK
======================
"Las empresas dan el cerebro. Nosotros el alma."

Usage:
    from seal_memory import SealMemory

    client = SealMemory(api_key="soul_xxx", base_url="http://localhost:8767")

    client.store("sofia", "María es ingeniera en Lima")
    results = client.recall("qué estudió?", agent_id="sofia")
"""

from typing import TYPE_CHECKING, Any

from .client import SealMemory
from .async_client import AsyncSealMemory
from .contracts import (
    ApprovalRequest,
    MemoryRecord,
    MemoryStoreRequest,
    content_hash,
    hash_api_key,
    normalize_category,
    safe_excerpt,
    validate_importance,
)
from .exceptions import SealMemoryError, AuthenticationError, RateLimitError

if TYPE_CHECKING:
    from .mem0_compat import MemoryClient as MemoryClient


__version__ = "0.2.0"
__all__ = [
    "SealMemory", "AsyncSealMemory", "MemoryClient",
    "ApprovalRequest", "MemoryRecord", "MemoryStoreRequest",
    "content_hash", "hash_api_key", "normalize_category",
    "safe_excerpt", "validate_importance",
    "SealMemoryError", "AuthenticationError", "RateLimitError",
]


def __getattr__(name: str) -> Any:
    """Load optional compatibility code only when explicitly requested."""
    if name == "MemoryClient":
        from .mem0_compat import MemoryClient

        return MemoryClient
    raise AttributeError(name)
