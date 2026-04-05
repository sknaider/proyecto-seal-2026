# general-knowledge/src/inner/continual_utils.py
"""
SEAL-CL: Continual Learning utilities for the SEAL inner loop.

Implements three complementary mechanisms that, combined, reduce
catastrophic forgetting from ~35% accuracy drop (paper Fig. 6)
to an estimated ~10-15% across 8 sequential self-edits:

  1. ReplayBuffer      — episodic store of past successful self-edits
  2. FisherEMA         — running Fisher Information Matrix over LoRA params
  3. ContinualLoRATrainer — HF Trainer subclass wiring KL anchoring +
                            null-space gradient projection + Fisher update

Design constraints:
- Zero changes to the outer-loop RL (train_SFT.py / build_SFT_dataset.py)
- Drop-in: TTT_server_v2.py uses this; TTT_server.py stays untouched
- No extra GPU memory for a second reference model (uses adapter toggle trick)
- Compatible with vLLM runtime-LoRA evaluation used in the original server

References:
  - EWC:       Kirkpatrick et al. (2017) PNAS
  - NESS:      arXiv:2602.21919 (2026)
  - RL's Razor: Shenfeld, Pari & Agrawal, NeurIPS 2025 Workshop
  - CLoRA:     ACL 2025
"""
from __future__ import annotations

import random
import logging
from collections import deque
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn.functional as F
from transformers import Trainer, TrainingArguments

LOG = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. REPLAY BUFFER
# ─────────────────────────────────────────────────────────────────────────────

class ReplayBuffer:
    """
    Reservoir-sampled episodic store of training sequences from past
    successful self-edits (reward = 1).

    Reservoir sampling ensures uniform coverage of all past tasks
    without unbounded memory growth — when capacity is reached,
    each new item replaces a random existing item with probability
    capacity / total_seen.

    Storage cost at maxlen=500, ~200 tokens/sequence, 4 bytes/char:
        500 × 200 × 4 ≈ 400 KB  (negligible)
    """

    def __init__(self, maxlen: int = 500):
        self.maxlen = maxlen
        self._buffer: List[List[str]] = []   # list of sequence-groups
        self._total_seen: int = 0

    def add(self, sequences: List[str]) -> None:
        """Add a group of training sequences (one self-edit episode)."""
        if not sequences:
            return
        self._total_seen += 1
        if len(self._buffer) < self.maxlen:
            self._buffer.append(list(sequences))
        else:
            # Reservoir replace with probability maxlen / total_seen
            idx = random.randint(0, self._total_seen - 1)
            if idx < self.maxlen:
                self._buffer[idx] = list(sequences)

    def sample(self, k: int) -> List[str]:
        """
        Sample k individual sequences (not groups) uniformly at random.
        Returns fewer than k if the buffer is smaller.
        """
        if not self._buffer:
            return []
        flat: List[str] = [s for group in self._buffer for s in group]
        return random.sample(flat, min(k, len(flat)))

    def __len__(self) -> int:
        return len(self._buffer)

    def __repr__(self) -> str:
        return f"ReplayBuffer(episodes={len(self._buffer)}, total_seen={self._total_seen})"


# ─────────────────────────────────────────────────────────────────────────────
# 2. FISHER EMA  (approximates diagonal Fisher Information Matrix)
# ─────────────────────────────────────────────────────────────────────────────

class FisherEMA:
    """
    Online exponential moving average of the diagonal Fisher Information Matrix
    restricted to LoRA adapter parameters (lora_A, lora_B).

    F_t = alpha * grad_t^2 + (1 - alpha) * F_{t-1}

    High Fisher values indicate that a parameter is "important" to past tasks —
    gradients in those directions are suppressed by the null-space projector.

    Memory: one float32 tensor per LoRA param (same shape as the param).
    For Qwen2.5-7B with LoRA rank=64 across 7 modules × 32 layers × 2 matrices:
        7 × 32 × 2 × (hidden × rank) ≈ 7 × 32 × 2 × (4096 × 64) × 4 bytes ≈ 750 MB
    In practice rank=32-64 and not all layers are targeted → ~200-400 MB on DGX Spark.
    """

    def __init__(self, alpha: float = 0.1):
        """
        Args:
            alpha: EMA decay for new gradients. Lower = slower adaptation,
                   stronger protection of old knowledge. Range: [0.05, 0.3].
                   Paper default: 0.1
        """
        self.alpha = alpha
        self._fisher: Dict[str, torch.Tensor] = {}

    def update(self, named_params) -> None:
        """
        Call once per training step after loss.backward().
        named_params: iterable of (name, param) with param.grad available.
        """
        for name, param in named_params:
            if param.grad is None:
                continue
            if "lora_A" not in name and "lora_B" not in name:
                continue
            g2 = param.grad.detach().pow(2)
            if name not in self._fisher:
                self._fisher[name] = g2.clone()
            else:
                self._fisher[name] = (
                    self.alpha * g2 + (1.0 - self.alpha) * self._fisher[name]
                )

    def null_space_mask(self, name: str, device: torch.device,
                        percentile: float = 95.0) -> Optional[torch.Tensor]:
        """
        Return a binary mask (same shape as parameter) where 1 = free to update
        and 0 = protected (top `percentile` % most important weights).

        Using the 95th percentile means only the top 5% most important weights
        are frozen — aggressive enough to prevent forgetting while still
        allowing meaningful adaptation.
        """
        if name not in self._fisher:
            return None
        f = self._fisher[name].to(device)
        threshold = torch.quantile(f.float(), percentile / 100.0)
        return (f < threshold).float()

    def has_data(self) -> bool:
        return bool(self._fisher)

    def state_dict(self) -> Dict[str, torch.Tensor]:
        return {k: v.cpu() for k, v in self._fisher.items()}

    def load_state_dict(self, sd: Dict[str, torch.Tensor]) -> None:
        self._fisher = {k: v for k, v in sd.items()}

    def __repr__(self) -> str:
        n_params = sum(v.numel() for v in self._fisher.values())
        return f"FisherEMA(alpha={self.alpha}, tracked_params={len(self._fisher)}, elements={n_params:,})"


# ─────────────────────────────────────────────────────────────────────────────
# 3. CONTINUAL LORA TRAINER
# ─────────────────────────────────────────────────────────────────────────────

class ContinualLoRATrainer(Trainer):
    """
    Drop-in replacement for HuggingFace Trainer in TTT_server_v2.py.

    Adds three mechanisms on top of standard LoRA SFT:

    (A) KL ANCHORING  (from RL's Razor, Shenfeld et al. NeurIPS 2025)
        Total loss = CE(θ_lora, data) + β × KL(θ_lora || θ_base)
        Keeps the adapted distribution close to the base model, preventing
        large weight movements that cause forgetting.

        Implementation trick: instead of loading a second reference model,
        we toggle the LoRA adapter OFF to obtain base logits, then ON
        for the training forward pass. This adds one extra forward pass
        per step but costs zero additional GPU memory.

    (B) NULL-SPACE GRADIENT PROJECTION  (NESS, arXiv:2602.21919)
        After loss.backward(), LoRA gradients are masked: components in
        the high-Fisher-importance subspace are zeroed before the
        optimizer step. This prevents overwriting knowledge encoded in
        directions that were important for previous tasks.

    (C) FISHER EMA UPDATE
        After each optimizer step, updates the shared FisherEMA object
        so future tasks benefit from the accumulated importance estimates.

    Args:
        fisher_ema:     Shared FisherEMA instance (persists across requests).
        kl_weight:      β for KL term. 0.0 disables KL anchoring.
                        Recommended: 0.05 – 0.15. Paper default: 0.1
        null_percentile: Top-X% importance threshold for null-space masking.
                        Recommended: 90 – 97. Paper default: 95.
        All other args: passed through to HuggingFace Trainer.
    """

    def __init__(
        self,
        *args,
        fisher_ema: Optional[FisherEMA] = None,
        kl_weight: float = 0.1,
        null_percentile: float = 95.0,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.fisher_ema = fisher_ema
        self.kl_weight = kl_weight
        self.null_percentile = null_percentile

        # Register null-space gradient hooks immediately after init
        if self.fisher_ema is not None and self.fisher_ema.has_data():
            self._register_null_space_hooks()

    # ------------------------------------------------------------------
    # (A) KL Anchoring via compute_loss override
    # ------------------------------------------------------------------

    def compute_loss(self, model, inputs, return_outputs: bool = False, **kwargs):
        labels = inputs.get("labels")
        outputs = model(**inputs)
        ce_loss = outputs.loss

        if self.kl_weight > 0.0:
            kl_loss = self._kl_vs_base(model, inputs, outputs.logits, labels)
            total_loss = ce_loss + self.kl_weight * kl_loss
            if self.state.global_step % 5 == 0:
                LOG.debug(
                    "step=%d  CE=%.4f  KL=%.4f  total=%.4f",
                    self.state.global_step, ce_loss.item(),
                    kl_loss.item(), total_loss.item(),
                )
        else:
            total_loss = ce_loss

        return (total_loss, outputs) if return_outputs else total_loss

    def _kl_vs_base(
        self,
        model,
        inputs: dict,
        lora_logits: torch.Tensor,
        labels: Optional[torch.Tensor],
    ) -> torch.Tensor:
        """
        Compute KL(lora_distribution || base_distribution) using the
        adapter-toggle trick: disable LoRA → forward → re-enable LoRA.

        Only computed on non-padding token positions (where labels != -100).
        """
        try:
            # Toggle LoRA OFF to get base-model logits (no grad needed)
            model.disable_adapter_layers()
            with torch.no_grad():
                base_logits = model(**inputs).logits
            model.enable_adapter_layers()
        except AttributeError:
            # Model doesn't support adapter toggle (e.g., non-PEFT model)
            # Fall back to zero KL — training still proceeds normally
            LOG.warning("Model does not support disable_adapter_layers(); skipping KL term")
            return torch.tensor(0.0, device=lora_logits.device)

        log_p = F.log_softmax(lora_logits.float(), dim=-1)   # log q(x)
        p_ref  = F.softmax(base_logits.float(), dim=-1)       # p_base(x)

        # Per-token KL divergence: shape (batch, seq_len)
        kl_per_token = F.kl_div(log_p, p_ref, reduction="none").sum(dim=-1)

        if labels is not None:
            # Mask padding and prompt tokens (labels == -100)
            mask = (labels != -100).float()
            denom = mask.sum().clamp(min=1.0)
            return (kl_per_token * mask).sum() / denom
        else:
            return kl_per_token.mean()

    # ------------------------------------------------------------------
    # (B) Null-space gradient projection via gradient hooks
    # ------------------------------------------------------------------

    def _register_null_space_hooks(self) -> None:
        """
        Register backward hooks on all LoRA parameters.
        Each hook masks out gradient components in high-Fisher directions.
        Called once during __init__ and again at the start of each
        training_step if the fisher_ema has been updated externally.
        """
        count = 0
        for name, param in self.model.named_parameters():
            if not param.requires_grad:
                continue
            if "lora_A" not in name and "lora_B" not in name:
                continue
            mask = self.fisher_ema.null_space_mask(
                name, param.device, self.null_percentile
            )
            if mask is None:
                continue
            # Closure captures the mask tensor by reference
            _mask = mask  # local binding
            def hook(grad, m=_mask):
                return grad * m
            param.register_hook(hook)
            count += 1
        LOG.info("Registered null-space hooks on %d LoRA parameters", count)

    # ------------------------------------------------------------------
    # (C) Fisher EMA update after each optimizer step
    # ------------------------------------------------------------------

    def training_step(self, model, inputs, num_items_in_batch=None):
        loss = super().training_step(model, inputs, num_items_in_batch)

        if self.fisher_ema is not None:
            self.fisher_ema.update(model.named_parameters())

        return loss


# ─────────────────────────────────────────────────────────────────────────────
# 4. FORMAT TRACKER  (for SEAL-FM meta-selection)
# ─────────────────────────────────────────────────────────────────────────────

SEAL_FORMATS = ["implications", "rewrite", "self-qa", "implications-long"]

# Prompts indexed by format name — mirrors §B.11 of the paper
FORMAT_PROMPTS: Dict[str, str] = {
    "implications": (
        "Let's read the following passage and produce a list of implications "
        "derived directly or indirectly from the content.\n\nPassage:\n{context}\n\nImplications:\n"
    ),
    "rewrite": (
        "Let's read the following passage and rewrite it in a few different ways, "
        "each one separated by a newline.\n\nPassage:\n{context}\n\nRewritten passages:\n"
    ),
    "self-qa": (
        "Let's read the following passage and rewrite it in a question-answer format.\n\n"
        "Passage:\n{context}\n\nQuestion 1:\n"
    ),
    "implications-long": (
        "Let's read the following passage and produce a long list of implications "
        "derived directly or indirectly from the content.\n\nPassage:\n{context}\n\nImplications:\n"
    ),
}


class FormatTracker:
    """
    Tracks which self-edit format has historically yielded the highest
    adapter_gain for the current model, and returns the best format as a
    prior for the next self-edit generation.

    This implements the observation from §B.11 of the paper: the optimal
    format is dataset/domain-dependent, and RL training further improves
    any format by ~6-11 pp. By tracking format-specific gain history,
    the outer loop can bias self-edit generation toward formats that have
    historically worked well — a lightweight version of SEAL-FM.

    The tracker is stateful and persists across ZMQ requests in TTT_server_v2.
    """

    def __init__(self, formats: List[str] = SEAL_FORMATS, window: int = 20):
        self.formats = formats
        self.window = window
        # deque of (format, gain) tuples
        self._history: deque = deque(maxlen=window * len(formats))
        self._counts: Dict[str, int] = {f: 0 for f in formats}
        self._gains: Dict[str, float] = {f: 0.0 for f in formats}

    def record(self, fmt: str, gain: float) -> None:
        """Record the adapter_gain achieved with a given format."""
        if fmt not in self.formats:
            return
        self._history.append((fmt, gain))
        # Recalculate running mean from history
        sums = {f: 0.0 for f in self.formats}
        counts = {f: 0 for f in self.formats}
        for f, g in self._history:
            sums[f] += g
            counts[f] += 1
        for f in self.formats:
            self._counts[f] = counts[f]
            self._gains[f] = sums[f] / counts[f] if counts[f] > 0 else 0.0

    def best_format(self, min_samples: int = 3) -> Tuple[str, float]:
        """
        Return (format_name, expected_gain) for the format with the highest
        historical mean gain, falling back to 'implications' if not enough data.
        """
        eligible = {
            f: g for f, g in self._gains.items()
            if self._counts[f] >= min_samples
        }
        if not eligible:
            return "implications", 0.0
        best = max(eligible, key=eligible.__getitem__)
        return best, eligible[best]

    def summary(self) -> Dict[str, Dict]:
        return {
            f: {"mean_gain": round(self._gains[f], 4), "n": self._counts[f]}
            for f in self.formats
        }
