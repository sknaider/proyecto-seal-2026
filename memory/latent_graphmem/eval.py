"""Evaluation: recall@k over a pool of candidate positive subgraphs."""
from __future__ import annotations

import torch

from .data import QueryGroup, serialize_query, serialize_subgraph


@torch.no_grad()
def recall_at_k(
    model,
    train_groups: list[QueryGroup],
    val_groups: list[QueryGroup],
    device: torch.device,
    k: int = 5,
) -> float:
    """Each val query must retrieve its own positive out of
    (train positives + val positives). Random chance ≈ k / pool_size.
    """
    model.eval()
    pool_groups = train_groups + val_groups
    if not pool_groups:
        return 0.0
    pool_texts = [serialize_subgraph(g.positive_subgraph) for g in pool_groups]
    pool_emb = model.encode(pool_texts, device)  # (P, D)

    q_texts = [serialize_query(g.query) for g in val_groups]
    q_emb = model.encode(q_texts, device)        # (V, D)

    sims = q_emb @ pool_emb.t()                  # (V, P)
    # Correct index for each val query = its slot in pool (train_n + i)
    train_n = len(train_groups)
    gold = torch.tensor(
        [train_n + i for i in range(len(val_groups))], device=device
    )
    topk = sims.topk(k=min(k, sims.size(1)), dim=1).indices
    hits = (topk == gold.unsqueeze(1)).any(dim=1).float()
    return hits.mean().item()
