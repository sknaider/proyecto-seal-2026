"""
SEAL Memory Python SDK
======================
"Las empresas dan el cerebro. Nosotros el alma."

Usage:
    from seal_memory import SealMemory

    client = SealMemory(api_key="soul_xxx", base_url="http://localhost:8767")

    # Auto-extract facts from conversation
    client.add("sofia", messages=[
        {"role": "user", "content": "Me llamo María, soy ingeniera en Lima"},
        {"role": "assistant", "content": "¡Hola María!"},
    ])

    # Search with optional LLM query expansion
    results = client.search("sofia", query="qué estudió?", expand=True)

    # Get agent soul (OCEAN, emotions, beliefs)
    soul = client.boot("sofia")
"""

from .client import SealMemory
from .async_client import AsyncSealMemory
from .mem0_compat import MemoryClient
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

__version__ = "0.1.1"
__all__ = [
    "SealMemory", "AsyncSealMemory", "MemoryClient",
    "ApprovalRequest", "MemoryRecord", "MemoryStoreRequest",
    "content_hash", "hash_api_key", "normalize_category",
    "safe_excerpt", "validate_importance",
    "SealMemoryError", "AuthenticationError", "RateLimitError",
]
