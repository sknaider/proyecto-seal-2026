"""Generate embeddings via sentence-transformers (CPU, sin Ollama)."""
from __future__ import annotations

import os
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


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME, cache_folder=CACHE_DIR, device="cpu")
    return _model


async def get_embedding(text: str) -> list[float]:
    """Get 768-dim embedding vector via multilingual-e5-base (CPU, sin Ollama)."""
    model = _get_model()
    # e5 models expect "passage: " prefix for documents
    vector = model.encode([f"passage: {text}"], show_progress_bar=False, device="cpu")
    return vector[0].tolist()
