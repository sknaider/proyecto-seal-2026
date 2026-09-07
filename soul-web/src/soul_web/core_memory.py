"""Serialized adapter from SOUL Web to the canonical SOUL Core memory store."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from soul_framework import Soul
from soul_framework.config import SoulConfig
from soul_framework.embedding.bge_m3 import BgeM3Embedding


@dataclass(frozen=True, slots=True)
class RecalledMemory:
    content: str
    score: float
    similarity: float


@dataclass(frozen=True, slots=True)
class RecallContext:
    boot: str
    memories: tuple[RecalledMemory, ...]


class CoreMemory:
    """One serialized access lane for SQLite + its native ANN sidecar.

    SQLite supports concurrent readers, but Core releases before 0.4.3 could publish
    a partial sidecar from multiple independently opened writers. SOUL Web still
    serializes every Core operation after that framework fix, keeping the app's
    ownership model explicit and easy to audit.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        soul_name: str = "alma_william",
        search_limit: int = 8,
    ) -> None:
        self.database = Path(database).expanduser().resolve()
        self.soul_name = soul_name
        self.search_limit = int(search_limit)
        if self.search_limit < 1 or self.search_limit > 50:
            raise ValueError("search_limit must be between 1 and 50")
        self._lock = threading.RLock()
        self._embedding = BgeM3Embedding()

    def _config(self) -> SoulConfig:
        return SoulConfig(
            backend="sqlite",
            backend_url=str(self.database),
            embedding_provider="bge-m3",
            embedding_dimensions=1024,
            memory_vector_index="auto",
            memory_exact_fallback=True,
        )

    def _run(self, operation):
        with self._lock:
            return asyncio.run(operation())

    def recall(self, query: str, *, context: Sequence[str] = ()) -> RecallContext:
        async def operation() -> RecallContext:
            async with Soul.create(
                self.soul_name,
                config=self._config(),
                embedding=self._embedding,
            ) as soul:
                boot = await soul.boot()
                hits = await soul.memory.search(
                    query,
                    limit=self.search_limit,
                    context=list(context)[-8:],
                )
                return RecallContext(
                    boot,
                    tuple(
                        RecalledMemory(hit.memory.content, hit.score, hit.similarity)
                        for hit in hits
                    ),
                )

        return self._run(operation)

    def store_fact(
        self,
        content: str,
        *,
        confidence: float,
        episode_context: str,
        metadata: dict[str, object],
    ) -> int:
        async def operation() -> int:
            async with Soul.create(
                self.soul_name,
                config=self._config(),
                embedding=self._embedding,
            ) as soul:
                return await soul.memory.store(
                    content,
                    category="fact",
                    importance=8,
                    confidence=confidence,
                    source="conversation_extraction",
                    episode_context=episode_context,
                    metadata=metadata,
                )

        return int(self._run(operation))

    def find_projection(self, fact_id: str) -> int | None:
        """Find a previously projected ledger fact after a crash-before-receipt.

        The fact id is written into Core metadata.  Looking it up before retrying
        closes the gap where Core committed successfully but the ledger process died
        before marking the projection complete.
        """

        async def operation() -> int | None:
            async with Soul.create(
                self.soul_name,
                config=self._config(),
                embedding=self._embedding,
            ) as soul:
                rows = await soul.memory.list(limit=10_000)
                for row in rows:
                    if row.metadata.get("conversation_fact_id") == fact_id:
                        return int(row.id)
                return None

        return self._run(operation)

    def find_episode_projection(self, episode_id: str) -> int | None:
        """Recover a Core episode committed before its ledger receipt was written."""

        async def operation() -> int | None:
            async with Soul.create(
                self.soul_name,
                config=self._config(),
                embedding=self._embedding,
            ) as soul:
                rows = await soul.memory.list(limit=10_000)
                for row in rows:
                    if row.metadata.get("episode_id") == episode_id:
                        return int(row.id)
                return None

        return self._run(operation)

    def store_episode(
        self,
        summary: str,
        *,
        session_id: str,
        episode_id: str,
        turn_ids: Sequence[str],
    ) -> int:
        async def operation() -> int:
            async with Soul.create(
                self.soul_name,
                config=self._config(),
                embedding=self._embedding,
            ) as soul:
                return await soul.memory.store(
                    summary,
                    category="episode",
                    importance=7,
                    source="conversation_episode",
                    episode_context=session_id,
                    metadata={"episode_id": episode_id, "turn_ids": list(turn_ids)},
                )

        return int(self._run(operation))

    def list_memories(self, limit: int = 50) -> list[str]:
        async def operation() -> list[str]:
            async with Soul.create(
                self.soul_name,
                config=self._config(),
                embedding=self._embedding,
            ) as soul:
                rows = await soul.memory.list(limit=limit)
                return [row.content for row in rows]

        return self._run(operation)

    def count(self) -> int:
        async def operation() -> int:
            async with Soul.create(
                self.soul_name,
                config=self._config(),
                embedding=self._embedding,
            ) as soul:
                return int(await soul.memory.count())

        return int(self._run(operation))
