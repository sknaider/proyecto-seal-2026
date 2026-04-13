"""Bi-encoder wrapper for LatentGraphMem.

Shared-weights encoder (query and subgraph go through the same LoRA-wrapped
multilingual-e5-base). Mean-pool over attention mask, then L2 normalize.
"""
from __future__ import annotations

import os

import torch
import torch.nn as nn
import torch.nn.functional as F
from peft import LoraConfig, TaskType, get_peft_model
from transformers import AutoModel, AutoTokenizer

BASE_MODEL = "intfloat/multilingual-e5-base"
CACHE_DIR = os.path.expanduser("~/IA/cache/huggingface")


def build_lora_config() -> LoraConfig:
    return LoraConfig(
        task_type=TaskType.FEATURE_EXTRACTION,
        r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        target_modules=["query", "key", "value", "dense"],
    )


def _mean_pool(last_hidden: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
    mask = attention_mask.unsqueeze(-1).float()
    summed = (last_hidden * mask).sum(dim=1)
    counts = mask.sum(dim=1).clamp(min=1e-6)
    return summed / counts


class BiEncoder(nn.Module):
    def __init__(self, base_name: str = BASE_MODEL, apply_lora: bool = True):
        super().__init__()
        self.tokenizer = AutoTokenizer.from_pretrained(base_name, cache_dir=CACHE_DIR)
        base = AutoModel.from_pretrained(base_name, cache_dir=CACHE_DIR)
        if apply_lora:
            self.encoder = get_peft_model(base, build_lora_config())
        else:
            self.encoder = base

    def encode(self, texts: list[str], device: torch.device) -> torch.Tensor:
        enc = self.tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        ).to(device)
        out = self.encoder(**enc)
        pooled = _mean_pool(out.last_hidden_state, enc["attention_mask"])
        return F.normalize(pooled, p=2, dim=-1)

    def trainable_params_report(self) -> str:
        trainable = sum(p.numel() for p in self.parameters() if p.requires_grad)
        total = sum(p.numel() for p in self.parameters())
        pct = 100.0 * trainable / max(1, total)
        return f"trainable {trainable:,} / total {total:,} ({pct:.3f}%)"
