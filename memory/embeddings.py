"""Generate embeddings via sentence-transformers (CPU, sin Ollama)."""
from __future__ import annotations

import asyncio
import functools
import os
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")  # CPU only — max 20% GPU rule

# Patch: transformers 4.57+ removed find_pruneable_heads_and_indices from pytorch_utils
# but sentence-transformers may still try to import it. Inject a stub if missing.
try:
    from transformers.pytorch_utils import find_pruneable_heads_and_indices  # noqa: F401
except ImportError:
    import transformers.pytorch_utils as _pu
    def _find_stub(heads, n_heads, head_size, already_pruned):
        import torch
        mask = torch.ones(n_heads, head_size)
        for h in already_pruned: mask[h] = 0
        for h in heads: mask[h] = 0
        index = torch.arange(len(mask.view(-1)))[mask.view(-1).bool()]
        return heads, index
    _pu.find_pruneable_heads_and_indices = _find_stub

from sentence_transformers import SentenceTransformer

MODEL_NAME = "intfloat/multilingual-e5-base"  # 768 dims — mismo que nomic-embed-text
CACHE_DIR = os.path.expanduser("~/IA/cache/huggingface")

_model: SentenceTransformer | None = None
_embed_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="embed")

# LRU cache for embeddings — same text = 0ms (no recompute)
_EMBED_CACHE_MAX = 3000
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME, cache_folder=CACHE_DIR, device="cpu")
    return _model


def _encode_sync(text: str) -> list[float]:
    """CPU-bound encode — runs in thread pool to not block event loop."""
    model = _get_model()
    vector = model.encode([f"passage: {text}"], show_progress_bar=False, device="cpu")
    return vector[0].tolist()


async def get_embedding(text: str) -> list[float]:
    """Get 768-dim embedding vector — cached LRU + async non-blocking.

    Cache: identical text returns immediately (0ms).
    Compute: runs in ThreadPoolExecutor so event loop stays unblocked.
    """
    cache_key = text[:300]  # key by first 300 chars (enough for uniqueness)

    if cache_key in _embed_cache:
        # LRU: move to end (most recently used)
        _embed_cache.move_to_end(cache_key)
        return _embed_cache[cache_key]

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        _embed_executor,
        functools.partial(_encode_sync, text)
    )

    # Insert into LRU cache, evict oldest if full
    if len(_embed_cache) >= _EMBED_CACHE_MAX:
        _embed_cache.popitem(last=False)  # remove oldest
    _embed_cache[cache_key] = result
    return result


async def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """Batch embedding — more efficient than calling get_embedding() N times.

    Returns a list of vectors in the same order as input texts.
    Cached texts are returned instantly; uncached texts are encoded together.
    """
    results: list[list[float] | None] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []

    for i, text in enumerate(texts):
        cache_key = text[:300]
        if cache_key in _embed_cache:
            _embed_cache.move_to_end(cache_key)
            results[i] = _embed_cache[cache_key]
        else:
            uncached_indices.append(i)
            uncached_texts.append(text)

    if uncached_texts:
        def _batch_encode():
            model = _get_model()
            prefixed = [f"passage: {t}" for t in uncached_texts]
            vecs = model.encode(prefixed, show_progress_bar=False, device="cpu")
            return [v.tolist() for v in vecs]

        loop = asyncio.get_event_loop()
        batch_vecs = await loop.run_in_executor(_embed_executor, _batch_encode)

        for idx, vec in zip(uncached_indices, batch_vecs):
            text = texts[idx]
            cache_key = text[:300]
            if len(_embed_cache) >= _EMBED_CACHE_MAX:
                _embed_cache.popitem(last=False)
            _embed_cache[cache_key] = vec
            results[idx] = vec

    return results  # type: ignore[return-value]


def warmup_model() -> None:
    """Pre-load model into memory at startup — avoids cold start on first embed call."""
    _get_model()
