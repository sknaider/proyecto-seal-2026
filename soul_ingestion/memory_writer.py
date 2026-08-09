"""Canonical SOUL memory writer used only after verified human approval."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


MEMORY_ID_RE = re.compile(r"Memory #(\d+) stored")
INVALIDATED_RE = re.compile(r"Memory #(\d+) invalidated")


def load_private_token(path: Path) -> str:
    """Read a regular, owner-only token file without following symlinks."""

    details = path.lstat()
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise RuntimeError("memory capability must be a regular file")
    if details.st_uid != os.getuid() or details.st_mode & 0o077:
        raise RuntimeError("memory capability permissions are too broad")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise RuntimeError("memory capability is invalid")
    return token


class CanonicalMemoryWriter:
    """Call the protected MCP writer; never write memories or Neo4j directly."""

    def __init__(self, endpoint: str, token_path: Path) -> None:
        self.endpoint = endpoint
        self.token_path = token_path

    async def store(self, candidate: dict[str, Any]) -> int:
        token = load_private_token(self.token_path)
        metadata = {
            "ingestion_tenant_id": str(candidate["tenant_id"]),
            "ingestion_candidate_id": str(candidate["candidate_id"]),
            "ingestion_document_id": str(candidate["document_id"]),
            "ingestion_derivation_id": str(candidate["derivation_id"]),
            "ingestion_raw_hash_sha256": candidate["raw_hash_sha256"],
            "ingestion_derivation_hash_sha256": candidate["output_hash_sha256"],
            "approval_binding_sha256": candidate["approval_binding_sha256"],
            "human_approval_verified": True,
            "original_advisory_importance": int(candidate["proposed_importance"]),
        }
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(self.endpoint, headers=headers) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(
                    "memory_store",
                    {
                        "agent": candidate["proposed_agent"],
                        "category": candidate["proposed_category"],
                        "content": candidate["proposed_content"],
                        "importance": int(candidate["proposed_importance"]),
                        "source": "human_reviewed_ingestion",
                        "metadata": metadata,
                        "scope": candidate["scope"],
                    },
                )
        text = " ".join(getattr(item, "text", "") for item in result.content)
        match = MEMORY_ID_RE.search(text)
        if result.isError or not match:
            raise RuntimeError(f"canonical memory writer did not confirm persistence: {text[:240]}")
        return int(match.group(1))

    async def invalidate(self, memory_id: int, *, agent: str, reason: str) -> None:
        """Invalidate through the canonical bitemporal MCP operation."""

        token = load_private_token(self.token_path)
        headers = {"Authorization": f"Bearer {token}"}
        async with streamablehttp_client(self.endpoint, headers=headers) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                result = await session.call_tool(
                    "memory_gateway",
                    {
                        "action": "invalidate",
                        "memory_id": memory_id,
                        "agent": agent,
                        "extra": {"reason": reason},
                    },
                )
        text = " ".join(getattr(item, "text", "") for item in result.content)
        if result.isError:
            raise RuntimeError(f"canonical memory invalidation failed: {text[:240]}")
        if not INVALIDATED_RE.search(text) and "already invalidated" not in text:
            raise RuntimeError(f"canonical memory writer did not confirm invalidation: {text[:240]}")
