from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import embeddings
import soul_memory_sdk_runtime


class _FakeVector(list[float]):
    def tolist(self) -> list[float]:
        return list(self)


class _FakeModel:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def encode(
        self,
        texts: list[str],
        *,
        show_progress_bar: bool,
        device: str,
    ) -> list[_FakeVector]:
        assert show_progress_bar is False
        assert device == "cpu"
        self.inputs.extend(texts)
        return [_FakeVector([float(len(self.inputs)), 0.5])]


@pytest.mark.asyncio
async def test_query_and_passage_prefixes_use_separate_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = _FakeModel()
    monkeypatch.setattr(embeddings, "_get_model", lambda: fake_model)
    embeddings._embed_cache.clear()
    embeddings._query_embed_cache.clear()
    try:
        passage_first = await embeddings.get_embedding("same text")
        query_first = await embeddings.get_query_embedding("same text")
        passage_cached = await embeddings.get_embedding("same text")
        query_cached = await embeddings.get_query_embedding("same text")

        assert fake_model.inputs == ["passage: same text", "query: same text"]
        assert passage_first == passage_cached == [1.0, 0.5]
        assert query_first == query_cached == [2.0, 0.5]
        assert passage_first != query_first
    finally:
        embeddings._embed_cache.clear()
        embeddings._query_embed_cache.clear()


@pytest.mark.asyncio
async def test_sdk_runtime_calls_query_embedding_not_passage_embedding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_query_embedding(text: str) -> list[float]:
        calls.append(text)
        return [0.25, 0.75]

    async def forbidden_passage_embedding(text: str) -> list[float]:
        raise AssertionError(f"passage encoder called for query: {text}")

    monkeypatch.setattr(embeddings, "get_query_embedding", fake_query_embedding)
    monkeypatch.setattr(embeddings, "get_embedding", forbidden_passage_embedding)

    result = await soul_memory_sdk_runtime._query_embedding("recall this")

    assert result == [0.25, 0.75]
    assert calls == ["recall this"]
