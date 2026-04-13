"""InfoNCE contrastive loss with in-batch negatives.

For each query in the batch, the positive is at column 0 among its K
explicit candidates, and all positives from OTHER queries in the batch
serve as additional negatives (free augmentation).
"""
from __future__ import annotations

import torch
import torch.nn.functional as F


def info_nce(
    query_emb: torch.Tensor,        # (B, D)
    candidate_emb: torch.Tensor,    # (B, K, D)
    temperature: float = 0.07,
) -> torch.Tensor:
    """InfoNCE with in-batch negatives.

    For query i, positives = candidate_emb[i, 0]. Negatives = candidate_emb[i, 1:]
    plus candidate_emb[j, 0] for j != i (other queries' positives).
    Total options per query = K + (B - 1).
    """
    B, K, D = candidate_emb.shape
    # Per-query candidates: (B, K, D)
    own_logits = torch.einsum("bd,bkd->bk", query_emb, candidate_emb) / temperature
    # In-batch positives from other queries: (B, B-1)
    other_pos = candidate_emb[:, 0, :]  # (B, D)
    cross = query_emb @ other_pos.t() / temperature  # (B, B)
    # Mask the diagonal (self already in own_logits[:,0])
    mask = torch.eye(B, dtype=torch.bool, device=query_emb.device)
    cross = cross.masked_fill(mask, float("-inf"))
    logits = torch.cat([own_logits, cross], dim=1)  # (B, K + B)
    labels = torch.zeros(B, dtype=torch.long, device=query_emb.device)  # pos at col 0
    return F.cross_entropy(logits, labels)
