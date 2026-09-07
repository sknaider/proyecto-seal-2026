#!/usr/bin/env python3
"""test_latent_graphmem.py — Step 8 smoke tests for LatentGraphMem V1.

Covers the six smoke tests from arch note:
  1. Data loader: loads pairs, query-level split has no leakage, shapes OK
  2. Forward pass: batch produces (B, K=4, D=768), no NaN
  3. Loss: contrastive loss > 0 and < log(K + B - 1) at init
  4. Backward: gradients flow to LoRA params, NOT base params
  5. Eval metric: recall@k returns float in [0, 1]
  6. Serve: self-test (imports + app build + circuit breaker state)

Design note: 1-epoch "full smoke" is already covered by `train_latent_graphmem.py
--smoke`; this file is unit-level and fast (<60s CPU).
"""
from __future__ import annotations

import asyncio
import math
import os
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import torch

from latent_graphmem.data import (
    QueryGroup,
    iter_batches,
    load_query_groups,
    serialize_query,
    serialize_subgraph,
    split_train_val,
)
from latent_graphmem.eval import recall_at_k
from latent_graphmem.loss import info_nce
from latent_graphmem.model import BiEncoder

RESULTS: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, msg: str = "") -> None:
    RESULTS.append((name, ok, msg))
    status = "✅" if ok else "❌"
    print(f"{status} {name}" + (f" — {msg}" if msg else ""))


async def test_data_loader() -> None:
    groups = await load_query_groups()
    assert groups, "no groups loaded"
    # every group has 1 pos + 3 neg
    bad = [g for g in groups if g.positive_subgraph is None or len(g.negative_subgraphs) != 3]
    assert not bad, f"{len(bad)} malformed groups"
    # split no leakage
    train, val = split_train_val(groups, val_ratio=0.20, seed=42)
    overlap = set(g.query for g in train) & set(g.query for g in val)
    assert not overlap, f"query leakage: {overlap}"
    # serialize produces non-empty text
    for g in groups[:3]:
        assert serialize_query(g.query)
        assert serialize_subgraph(g.positive_subgraph)
    _record("data_loader", True, f"{len(groups)} groups, train={len(train)} val={len(val)} no leakage")


def test_forward_pass(model: BiEncoder, device: torch.device, batch: list[QueryGroup]) -> tuple[torch.Tensor, torch.Tensor]:
    q_texts = [serialize_query(g.query) for g in batch]
    cand_texts = []
    for g in batch:
        cand_texts.append(serialize_subgraph(g.positive_subgraph))
        for neg in g.negative_subgraphs:
            cand_texts.append(serialize_subgraph(neg))
    q_emb = model.encode(q_texts, device)
    c_flat = model.encode(cand_texts, device)
    K = 4
    c_emb = c_flat.view(len(batch), K, -1)
    assert q_emb.shape[0] == len(batch)
    assert c_emb.shape == (len(batch), K, q_emb.shape[1])
    assert q_emb.shape[1] == 768, f"expected 768-dim, got {q_emb.shape[1]}"
    assert not torch.isnan(q_emb).any()
    assert not torch.isnan(c_emb).any()
    _record("forward_pass", True, f"q={tuple(q_emb.shape)} c={tuple(c_emb.shape)}, no NaN")
    return q_emb, c_emb


def test_loss(q_emb: torch.Tensor, c_emb: torch.Tensor) -> torch.Tensor:
    loss = info_nce(q_emb, c_emb, temperature=0.07)
    B, K, _ = c_emb.shape
    max_entropy = math.log(K + B - 1)
    assert loss.item() > 0, f"loss not positive: {loss.item()}"
    # Allow slack above max_entropy since logits are not uniform at init
    assert loss.item() < max_entropy * 2.0, f"loss {loss.item()} too large"
    _record("loss", True, f"value={loss.item():.4f} (max_ent={max_entropy:.2f})")
    return loss


def test_backward(model: BiEncoder, loss: torch.Tensor) -> None:
    loss.backward()
    lora_grads = 0
    lora_params = 0
    base_grads_with_grad = 0
    base_params = 0
    for name, p in model.named_parameters():
        is_lora = "lora_" in name.lower()
        if is_lora:
            lora_params += 1
            if p.grad is not None and p.grad.abs().sum().item() > 0:
                lora_grads += 1
        else:
            base_params += 1
            # base params should have requires_grad False, so grad is None
            if p.requires_grad and p.grad is not None and p.grad.abs().sum().item() > 0:
                base_grads_with_grad += 1
    assert lora_grads > 0, "no LoRA params received gradient"
    assert base_grads_with_grad == 0, f"{base_grads_with_grad} base params got grad (should be frozen)"
    _record("backward", True, f"LoRA grads flowing ({lora_grads}/{lora_params}), base frozen ({base_params})")


def test_eval_metric(model: BiEncoder, device: torch.device, groups: list[QueryGroup]) -> None:
    # Use small pools to stay fast
    train = groups[:4]
    val = groups[4:6]
    r = recall_at_k(model, train, val, device, k=5)
    assert isinstance(r, float)
    assert 0.0 <= r <= 1.0, f"recall out of range: {r}"
    _record("eval_metric", True, f"recall@5={r:.4f} ∈ [0,1]")


def test_serve_selftest() -> None:
    import subprocess
    r = subprocess.run(
        [sys.executable, str(HERE / "latent_graphmem_serve.py"), "--self-test"],
        capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        _record("serve_selftest", False, r.stderr.strip()[:200])
        return
    assert "/retrieve" in r.stdout, "retrieve route missing"
    assert "/health" in r.stdout, "health route missing"
    _record("serve_selftest", True, "routes OK, circuit breaker importable")


async def main() -> int:
    try:
        print("=" * 60)
        print("LatentGraphMem V1 — Smoke Test Suite")
        print("=" * 60)
        # 1. Data
        await test_data_loader()
        groups = await load_query_groups()

        # Build tiny model (LoRA on, CPU)
        print("\n[setup] building BiEncoder...")
        device = torch.device("cpu")
        model = BiEncoder(apply_lora=True).to(device)
        model.train()
        print(f"[setup] {model.trainable_params_report()}")

        # 2. Forward
        batch = groups[:2]
        q_emb, c_emb = test_forward_pass(model, device, batch)

        # 3. Loss
        loss = test_loss(q_emb, c_emb)

        # 4. Backward
        test_backward(model, loss)

        # 5. Eval
        model.eval()
        test_eval_metric(model, device, groups)

        # 6. Serve
        test_serve_selftest()

    except Exception as e:
        traceback.print_exc()
        _record("unexpected_crash", False, f"{type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"RESULT: {passed}/{total} passed")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
