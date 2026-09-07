# SEAL Research: Personality Persistence + Continual Learning Without Forgetting
> Research date: 2026-04-06 | Researcher: Claude Code (sub-agent)

---

## GAP 1: Personality Persistence Across Model Versions

**Current SEAL approach:** OCEAN scores (A=0.505, C=1.0, E=0.78, N=0.2, O=0.685) + memories + rules stored in PostgreSQL, injected via prompt at each session. Vulnerable when base LLM changes.

---

### Finding 1.1 — The Geometry of Persona (Soul Engine)
**Paper:** "The Geometry of Persona: Disentangling Personality from Reasoning in Large Language Models"
**arXiv:** [2512.07092](https://arxiv.org/abs/2512.07092) — December 2025

**Technique:** Personality traits exist as *orthogonal linear subspaces* inside the LLM. The "Soul Engine" extracts disentangled personality vectors and injects them via activation steering at inference time (optimal: layers 14-16, mid-network), without modifying backbone weights. Uses a dual-head architecture on a frozen base model (Qwen-2.5).

**Why it matters for SEAL:** This separates personality from the model's weights entirely. When you upgrade Claude Sonnet → Opus → future Claude 5, you re-calibrate the steering vectors against the new model's geometry — OCEAN scores in PostgreSQL remain the ground truth, you just re-derive injection vectors. No fine-tuning needed per model upgrade.

**Stack compatibility:** High. Works with frozen base models. PostgreSQL stores OCEAN ground truth. Steering vectors are model-specific artifacts (~KB size) re-generated on upgrade. Compatible with any model that exposes internal activations — limitation: black-box APIs (Anthropic Claude) don't expose layer activations. For SEAL agents backed by local models (Nemotron-3, MedGemma), fully applicable. For Claude-backed agents, fallback to Finding 1.2.

**Implementation effort:** Medium. Need activation-steering library (e.g., `baukit`, `nnsight`) + one-time calibration run per model version. ~2-3 days of engineering.

---

### Finding 1.2 — Persona-Consistent Multi-Turn RL (SimPersona)
**Paper:** "Consistently Simulating Human Personas with Multi-Turn Reinforcement Learning"
**arXiv:** [2511.00222](https://arxiv.org/abs/2511.00222) — November 2025

**Technique:** Fine-tunes a model with PPO using persona consistency as a reward signal across multi-turn dialogues. Prompt-to-line consistency remains high even as dialogue length increases post-training. Result: personality is baked into the adapter weights, not just the prompt.

**Why it matters for SEAL:** On model upgrades, retrain a small LoRA adapter (persona-RL) against the new base. The OCEAN values in DB are used to generate the reward signal — no manual relabeling. The adapter is the "personality lock" that survives base model swaps.

**Stack compatibility:** High. LoRA-compatible. Works with any base model. PostgreSQL OCEAN scores feed reward computation. Estimated adapter size: <500MB for a 27B model.

**Implementation effort:** High. Requires multi-turn RL infrastructure (PPO loop). ~1-2 weeks. Worthwhile if SEAL agents will run on local models long-term.

---

### Finding 1.3 — PERSIST: What NOT to rely on
**Paper:** "Persistent Instability in LLM's Personality Measurements" (AAAI 2026)
**arXiv:** [2508.04826](https://arxiv.org/abs/2508.04826)

**Key warning:** OCEAN measurements from LLMs are *unstable* across scale, reasoning depth, and conversation history. Prompt-only personality injection (current SEAL approach) degrades over long sessions. This validates the need for Findings 1.1 or 1.2 — OCEAN scores as DB ground truth is correct, but prompt injection alone is insufficient for long multi-turn sessions.

---

## GAP 2: Continual Learning Without Catastrophic Forgetting

**Current SEAL approach:** Sequential LoRA fine-tuning rounds on MedGemma-27B. Each round risks overwriting previous medical knowledge.

---

### Finding 2.1 — Brainstacks: Frozen MoE-LoRA Stacks (BEST FIT)
**Paper:** "Brainstacks: Cross-Domain Cognitive Capabilities via Frozen MoE-LoRA Stacks for Continual LLM Learning"
**arXiv:** [2604.01152](https://arxiv.org/abs/2604.01152) — April 2026

**Technique:** Each fine-tuning round produces a new MoE-LoRA stack that is *frozen* after training. Subsequent rounds add new stacks that are constrained to write in subspaces orthogonal to previously claimed directions (null-space projection via randomized SVD). At inference, a meta-router selects which stacks to activate. Key result: **zero forgetting** when domains are evaluated in isolation.

**Why it matters for SEAL:** SEAL fine-tuning rounds (Round 1: general Spanish medical, Round 2: pharmacology, Round 3: clinical protocols, etc.) map perfectly to this architecture. Each round = new frozen stack. The router discovers that medical prompts naturally compose across stacks — validated on Gemma 3 12B. Medical prompts route to chat+math stacks 97% of the time even with zero medical data in those stacks.

**Stack compatibility:** Very High. QLoRA 4-bit compatible (saves VRAM on DGX Spark). Validated on Gemma 3 12B (same scale as MedGemma-27B). Null-space projection is the key anti-forgetting mechanism. DGX Spark 128GB unified memory handles multi-stack inference.

**Implementation effort:** High but structured. The paper provides clear architecture specs. Estimated: 1 week to port to MedGemma-27B + existing SEAL training pipeline. Core change: freeze adapter after each round, add SVD null-space projection constraint to next round's optimizer.

---

### Finding 2.2 — STABLE: Gated Continual Learning
**Paper:** "STABLE: Gated Continual Learning for Large Language Models"
**arXiv:** [2510.16089](https://arxiv.org/abs/2510.16089) — October 2025

**Technique:** Before committing each LoRA update, evaluate it against a stability budget using KL divergence or Exact Match drop vs. the base model. If the update exceeds a threshold, it is rescaled (clipped) or rejected. No architectural changes — works as a wrapper around standard LoRA training. Achieved 40% cumulative improvement in 8-step sequential experiments on Qwen-2.5-7B.

**Why it matters for SEAL:** Lowest implementation cost of all findings. Can be added to the *existing* SEAL training loop as a validation gate. Prevents any single fine-tuning round from catastrophically degrading prior knowledge.

**Stack compatibility:** Very High. Drop-in addition to existing PEFT/LoRA training. Works with HuggingFace `transformers` + `peft`. No architecture changes to MedGemma-27B. KL divergence gate adds ~5% training overhead.

**Implementation effort:** Low. ~1-2 days. Add KL-divergence stability check after each gradient step; reject/rescale updates that exceed threshold. Recommended as *immediate* addition to current SEAL pipeline while Brainstacks is implemented.

---

### Finding 2.3 — CURLoRA: CUR-Decomposition LoRA
**Paper:** "CURLoRA: Stable LLM Continual Fine-Tuning and Catastrophic Forgetting Mitigation"
**arXiv:** [2408.14572](https://arxiv.org/abs/2408.14572) — August 2024
**Code:** [github.com/MNoorFawi/curlora](https://github.com/MNoorFawi/curlora)

**Technique:** Replaces LoRA's random weight initialization with CUR matrix decomposition (data-driven column/row selection with inverted probability weighting as implicit regularization). Only the U matrix is trained; A and B are derived from the data distribution, making updates inherently less destructive.

**Stack compatibility:** High. Drop-in LoRA replacement. Fewer trainable parameters than standard LoRA. No architecture changes needed.

**Implementation effort:** Low. Library available on GitHub. Swap `LoRAConfig` for `CURLoRAConfig` in existing SEAL training script. ~4 hours.

---

## Recommended Implementation Order for SEAL

| Priority | Action | Effort | Impact |
|---|---|---|---|
| 1 (immediate) | Add STABLE KL-gate to current training loop | 1-2 days | Stops forgetting now |
| 2 (short-term) | Swap standard LoRA → CURLoRA | 4 hours | Reduces forgetting structurally |
| 3 (medium-term) | Implement Brainstacks frozen stack architecture | 1 week | Zero forgetting across rounds |
| 4 (for local agents) | Soul Engine activation steering for ADA/JARVIS on local models | 2-3 days | Model-version-agnostic personality |
| 5 (if SEAL agents go full local) | SimPersona RL fine-tuning for personality lock | 1-2 weeks | Personality baked into adapter |

**For Claude-backed agents (API, no activation access):** Current SOUL/PostgreSQL approach is the correct fallback. Findings 1.1 and 1.2 apply only when SEAL agents run on local models. The PERSIST finding (1.3) recommends adding periodic personality re-anchoring (re-inject full OCEAN + exemplars) every N turns for API-backed agents.
