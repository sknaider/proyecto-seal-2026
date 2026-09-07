#!/usr/bin/env python3
"""train_latent_graphmem.py — Step 3 of LatentGraphMem (Track A).

Bi-encoder LoRA training on latent_graphmem_training_pairs.
Arch note: memory/research/latent_graphmem_architecture.md

Usage:
    python3 train_latent_graphmem.py --smoke          # 1 epoch, tiny
    python3 train_latent_graphmem.py                  # full 10 epochs
"""
from __future__ import annotations

import argparse
import asyncio
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import torch
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR, LambdaLR

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

OUT_DIR = Path(os.path.expanduser("~/IA/modelos/latent-graphmem-soul-v1"))
HERE = Path(__file__).resolve().parent
TRAIN_SOURCE = "synthetic_v1"  # V1.1 leak-fix: test_set_v1 is held-out eval, never train


def _run_contamination_gate(source: str) -> None:
    """Pre-train gate — refuses to load model if training pairs overlap test set.

    Origin: post-mortem 2026-04-12 (see research/latent_graphmem_architecture.md).
    Rule (William): silent bugs scale exponentially — audit before every train.
    """
    gate = HERE / "latent_graphmem" / "contamination_check.py"
    if not gate.exists():
        print(f"[gate] WARN: {gate} not found — skipping contamination check",
              file=sys.stderr)
        return
    print(f"[gate] running contamination_check.py --pairs-source {source}")
    r = subprocess.run(
        [sys.executable, str(gate), "--pairs-source", source],
        cwd=str(HERE),
    )
    if r.returncode != 0:
        sys.exit(
            f"[gate] ABORT: contamination detected in source='{source}' "
            f"(exit={r.returncode}). Training refused."
        )
    print("[gate] CLEAN — training authorized")


def forward_batch(
    model: BiEncoder, batch: list[QueryGroup], device: torch.device
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return (query_emb [B,D], candidate_emb [B,K,D])."""
    queries = [serialize_query(g.query) for g in batch]
    cands = []  # flatten: B*K texts
    for g in batch:
        cands.append(serialize_subgraph(g.positive_subgraph))
        for neg in g.negative_subgraphs:
            cands.append(serialize_subgraph(neg))
    K = 1 + len(batch[0].negative_subgraphs)  # 4
    q_emb = model.encode(queries, device)               # (B, D)
    c_emb_flat = model.encode(cands, device)            # (B*K, D)
    c_emb = c_emb_flat.view(len(batch), K, -1)
    return q_emb, c_emb


def train(
    epochs: int,
    batch_size: int,
    lr: float,
    smoke: bool,
    save_dir: Path,
    exclude_types: list[str] | None = None,
    resume_from: Path | None = None,
    warmup_frac: float = 0.1,
) -> dict:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[init] device={device}")

    print("[data] loading pairs from pg...")
    groups = asyncio.run(load_query_groups())
    print(f"[data] loaded {len(groups)} query groups (1 pos + 3 neg each)")
    if exclude_types:
        before = len(groups)
        groups = [g for g in groups if g.query_type not in exclude_types]
        print(f"[data] excluded types {exclude_types}: {before} → {len(groups)}")
    if not groups:
        print("[err] no training data", file=sys.stderr)
        return {"status": "no_data"}

    train_groups, val_groups = split_train_val(groups, val_ratio=0.20, seed=42)
    if smoke:
        train_groups = train_groups[:4]
        val_groups = val_groups[:2]
    print(f"[data] train={len(train_groups)} val={len(val_groups)}")

    if resume_from is not None:
        from peft import PeftModel
        print(f"[model] resuming LoRA adapter from {resume_from}")
        model = BiEncoder(apply_lora=False)
        model.encoder = PeftModel.from_pretrained(
            model.encoder, str(resume_from), is_trainable=True
        )
        model.to(device)
    else:
        print("[model] building BiEncoder with fresh LoRA...")
        model = BiEncoder().to(device)
    print(f"[model] {model.trainable_params_report()}")

    optim = AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=lr,
        weight_decay=0.01,
    )
    steps_per_epoch = max(1, math.ceil(len(train_groups) / batch_size))
    total_steps = max(1, steps_per_epoch * epochs)
    warmup_steps = max(1, int(round(total_steps * warmup_frac)))

    def _lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return step / max(1, warmup_steps)
        progress = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        return 0.5 * (1.0 + math.cos(math.pi * progress))

    sched = LambdaLR(optim, lr_lambda=_lr_lambda)
    print(f"[sched] total_steps={total_steps} warmup={warmup_steps} ({warmup_frac:.0%})")

    best_recall = -1.0
    history: list[dict] = []
    step = 0
    t0 = time.perf_counter()

    for epoch in range(1, epochs + 1):
        model.train()
        epoch_loss = 0.0
        n_batches = 0
        for batch in iter_batches(train_groups, batch_size, shuffle=True, seed=epoch):
            if len(batch) < 2:
                continue  # InfoNCE in-batch needs B>=2
            q_emb, c_emb = forward_batch(model, batch, device)
            loss = info_nce(q_emb, c_emb, temperature=0.07)
            optim.zero_grad()
            loss.backward()
            optim.step()
            sched.step()
            step += 1
            epoch_loss += loss.item()
            n_batches += 1
            if step % 5 == 0 or smoke:
                print(f"  [step {step}] loss={loss.item():.4f} lr={sched.get_last_lr()[0]:.2e}")

        avg_loss = epoch_loss / max(1, n_batches)
        val_recall = recall_at_k(model, train_groups, val_groups, device, k=5)
        elapsed = time.perf_counter() - t0
        print(f"[epoch {epoch}/{epochs}] loss={avg_loss:.4f} "
              f"val_recall@5={val_recall:.4f} elapsed={elapsed:.1f}s")
        history.append({"epoch": epoch, "loss": avg_loss, "recall@5": val_recall})

        if val_recall > best_recall:
            best_recall = val_recall
            save_dir.mkdir(parents=True, exist_ok=True)
            best_path = save_dir / "best"
            best_path.mkdir(exist_ok=True)
            model.encoder.save_pretrained(str(best_path))
            model.tokenizer.save_pretrained(str(best_path))
            print(f"[save] new best recall@5={best_recall:.4f} → {best_path}")

    return {
        "status": "ok",
        "best_recall": best_recall,
        "history": history,
        "elapsed_s": round(time.perf_counter() - t0, 1),
    }


def main():
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    import torch
    print(f"[init] torch={torch.__version__} cuda_available={torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"[init] cuda_device={torch.cuda.get_device_name(0)}")
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="1 epoch, 4 train groups")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--out-dir", type=str, default=str(OUT_DIR))
    ap.add_argument("--exclude-types", type=str, default="",
                    help="comma-separated query_types to drop (e.g. 'negation')")
    ap.add_argument("--resume-from", type=str, default="",
                    help="path to an existing LoRA adapter to continue training")
    ap.add_argument("--warmup-frac", type=float, default=0.1)
    args = ap.parse_args()

    _run_contamination_gate(TRAIN_SOURCE)

    epochs = 1 if args.smoke else args.epochs
    exclude_list = [t.strip() for t in args.exclude_types.split(",") if t.strip()] or None
    resume_path = Path(os.path.expanduser(args.resume_from)) if args.resume_from else None
    report = train(
        epochs=epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        smoke=args.smoke,
        save_dir=Path(os.path.expanduser(args.out_dir)),
        exclude_types=exclude_list,
        resume_from=resume_path,
        warmup_frac=args.warmup_frac,
    )
    print(f"[done] {report}")


if __name__ == "__main__":
    main()
