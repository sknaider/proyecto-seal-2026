#!/usr/bin/env python3
"""
SEAL Engine — Motor principal del Proyecto SEAL.
Auto-mejora continua de modelos médicos via self-edit + LoRA + continual learning.

Uso:
    python -m core.seal_engine --config models/medgemma_27b.yaml
"""
from __future__ import annotations

import gc
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml
import torch
from datasets import Dataset
from peft import LoraConfig, get_peft_model, TaskType
from transformers import (
    AutoProcessor,
    AutoModelForImageTextToText,
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
)

from .continual_utils import (
    ContinualLoRATrainer,
    FisherEMA,
    ReplayBuffer,
    FormatTracker,
)
from .self_edit_gen import generate_self_edit, build_context_from_qa, FORMAT_PROMPTS
from .evaluator import evaluate_qa

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

LOG = logging.getLogger("seal_engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)


# ─────────────────────────────────────────────────────────────────────────────
# Thermal protection
# ─────────────────────────────────────────────────────────────────────────────

def get_gpu_temp() -> int:
    try:
        import subprocess
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader"],
            text=True
        ).strip()
        return int(out.split("\n")[0])
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Model loading
# ─────────────────────────────────────────────────────────────────────────────

def load_model_and_processor(cfg: dict):
    """Load model and processor based on config."""
    model_class = cfg.get("model_class", "AutoModelForCausalLM")
    processor_class = cfg.get("processor_class", "AutoProcessor")
    model_path = cfg["model_path"]
    dtype = getattr(torch, cfg.get("dtype", "bfloat16"))

    LOG.info("Loading processor (%s) from %s...", processor_class, model_path)
    if processor_class == "AutoProcessor":
        processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    else:
        processor = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)

    # Ensure pad token
    tokenizer = processor.tokenizer if hasattr(processor, 'tokenizer') else processor
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    LOG.info("Loading model (%s) from %s...", model_class, model_path)
    if model_class == "AutoModelForImageTextToText":
        model = AutoModelForImageTextToText.from_pretrained(
            model_path, torch_dtype=dtype, device_map="auto", trust_remote_code=True,
        )
    else:
        model = AutoModelForCausalLM.from_pretrained(
            model_path, torch_dtype=dtype, device_map="auto", trust_remote_code=True,
        )

    LOG.info("Model loaded. GPU mem: %.1f GB", torch.cuda.max_memory_allocated() / 1e9)
    return model, processor


# ─────────────────────────────────────────────────────────────────────────────
# Training helpers
# ─────────────────────────────────────────────────────────────────────────────

def tokenize_self_edit(processor, self_edit_text: str, item: dict,
                       system_prompt: str, max_len: int = 1024) -> dict:
    """Tokenize a self-edit as a training example."""
    messages = [
        {"role": "system", "content": [{"type": "text", "text": system_prompt}]},
        {"role": "user", "content": [{"type": "text", "text": build_context_from_qa(item)}]},
        {"role": "assistant", "content": [{"type": "text", "text": self_edit_text}]},
    ]

    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)
    tokenizer = processor.tokenizer if hasattr(processor, 'tokenizer') else processor
    tokenized = tokenizer(text, truncation=True, max_length=max_len, padding=False)

    return {
        "input_ids": tokenized["input_ids"],
        "attention_mask": tokenized["attention_mask"],
        "labels": tokenized["input_ids"].copy(),
    }


class PaddingCollator:
    def __init__(self, pad_token_id: int):
        self.pad_token_id = pad_token_id

    def __call__(self, examples):
        max_len = max(len(e["input_ids"]) for e in examples)
        input_ids, attention_mask, labels = [], [], []
        for e in examples:
            pad_len = max_len - len(e["input_ids"])
            input_ids.append(e["input_ids"] + [self.pad_token_id] * pad_len)
            attention_mask.append(e["attention_mask"] + [0] * pad_len)
            labels.append(e["labels"] + [-100] * pad_len)
        return {
            "input_ids": torch.tensor(input_ids),
            "attention_mask": torch.tensor(attention_mask),
            "labels": torch.tensor(labels),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Main SEAL loop
# ─────────────────────────────────────────────────────────────────────────────

def run_seal(config_path: str):
    """Main SEAL autonomous training loop."""
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    seal_cfg = cfg.get("seal", {})
    lora_cfg = cfg.get("lora", {})
    train_cfg = cfg.get("training", {})

    start_time = time.time()
    max_hours = seal_cfg.get("max_hours", 10.0)
    deadline = start_time + max_hours * 3600
    formats = seal_cfg.get("formats", ["implications", "rewrite", "self-qa"])
    edits_per_context = seal_cfg.get("self_edits_per_context", 3)
    checkpoint_every = seal_cfg.get("checkpoint_every", 50)
    system_prompt = cfg.get("system_prompt", "Eres un asistente médico experto.")

    output_dir = Path(f"results/{cfg['name']}_{int(start_time)}")
    output_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("=" * 70)
    LOG.info("PROYECTO SEAL — Autonomous Medical Model Training")
    LOG.info("=" * 70)
    LOG.info("Model: %s", cfg["name"])
    LOG.info("Formats: %s", formats)
    LOG.info("Self-edits per context: %d", edits_per_context)
    LOG.info("Max hours: %.1f", max_hours)
    LOG.info("Deadline: %s", datetime.fromtimestamp(deadline).strftime("%H:%M:%S"))
    LOG.info("Output: %s", output_dir)

    # ── Load model ──
    model, processor = load_model_and_processor(cfg)
    tokenizer = processor.tokenizer if hasattr(processor, 'tokenizer') else processor

    # ── Initialize continual learning components ──
    replay_buffer = ReplayBuffer(maxlen=seal_cfg.get("replay_maxlen", 500))
    fisher_ema = FisherEMA(alpha=seal_cfg.get("fisher_alpha", 0.1))
    format_tracker = FormatTracker()

    # ── Load data ──
    train_data_path = cfg.get("train_data", "data/medical_train.json")
    eval_data_path = cfg.get("eval_data", "data/medical_eval.json")

    with open(train_data_path) as f:
        train_items = json.load(f)
    eval_items = []
    if os.path.exists(eval_data_path):
        with open(eval_data_path) as f:
            eval_items = json.load(f)

    LOG.info("Train items: %d, Eval items: %d", len(train_items), len(eval_items))

    # ── Baseline evaluation ──
    if eval_items:
        LOG.info("Running baseline evaluation...")
        baseline = evaluate_qa(model, processor, eval_items)
        LOG.info("Baseline: answer_rate=%.1f%%, avg_len=%.0f",
                 baseline["answer_rate"] * 100, baseline["avg_length"])
    else:
        baseline = {"answer_rate": 0, "avg_length": 0}

    # ── SEAL Loop ──
    stats = {
        "total_edits": 0, "kept": 0, "discarded": 0,
        "items_processed": 0, "format_wins": {},
    }
    log_history = []

    for idx, item in enumerate(train_items):
        if time.time() > deadline:
            LOG.info("Time limit reached. Stopping.")
            break

        try:

            # Thermal check
            temp = get_gpu_temp()
            if temp >= 85:
                LOG.warning("GPU at %d°C — pausing 60s", temp)
                gc.collect()
                torch.cuda.empty_cache()
                time.sleep(60)

            LOG.info("─" * 40)
            LOG.info("Item %d/%d: %s", idx + 1, len(train_items), item.get("title", "")[:50])

            # Select formats
            if sum(format_tracker._counts.values()) > 0 and len(formats) > 1:
                best_fmt, _ = format_tracker.best_format(min_samples=3)
                selected_formats = [best_fmt] + [f for f in formats if f != best_fmt][:edits_per_context - 1]
            else:
                selected_formats = formats[:edits_per_context]

            best_gain = -1.0
            best_edit = None
            best_fmt_name = None

            for fmt in selected_formats:
                stats["total_edits"] += 1
                LOG.info("  Generating self-edit (%s)...", fmt)
                self_edit_text = generate_self_edit(model, processor, item, fmt)

                if len(self_edit_text.strip()) < 50:
                    LOG.warning("  Self-edit too short (%d chars), skipping", len(self_edit_text))
                    continue

                train_example = tokenize_self_edit(
                    processor, self_edit_text, item, system_prompt,
                    train_cfg.get("max_seq_length", 1024),
                )

                replay_examples = []
                if replay_buffer.has_data():
                    for replay_text in replay_buffer.sample(seal_cfg.get("replay_k", 10)):
                        replay_ex = tokenizer(
                            replay_text, truncation=True,
                            max_length=train_cfg.get("max_seq_length", 1024), padding=False,
                        )
                        replay_examples.append({
                            "input_ids": replay_ex["input_ids"],
                            "attention_mask": replay_ex["attention_mask"],
                            "labels": replay_ex["input_ids"].copy(),
                        })

                all_examples = [train_example] + replay_examples
                dataset = Dataset.from_list(all_examples)

                lora_config = LoraConfig(
                    r=lora_cfg.get("rank", 64),
                    lora_alpha=lora_cfg.get("alpha", 16),
                    target_modules=lora_cfg.get("target_modules", ["q_proj", "v_proj"]),
                    lora_dropout=lora_cfg.get("dropout", 0.05),
                    bias="none",
                    task_type=TaskType.CAUSAL_LM,
                )

                model_with_lora = get_peft_model(model, lora_config)
                model_with_lora.gradient_checkpointing_enable(
                    gradient_checkpointing_kwargs={"use_reentrant": False}
                )

                training_args = TrainingArguments(
                    output_dir=str(output_dir / "tmp_lora"),
                    num_train_epochs=3,
                    per_device_train_batch_size=train_cfg.get("per_device_batch_size", 1),
                    gradient_accumulation_steps=train_cfg.get("gradient_accumulation", 8),
                    learning_rate=train_cfg.get("learning_rate", 2e-4),
                    weight_decay=train_cfg.get("weight_decay", 0.01),
                    bf16=train_cfg.get("bf16", True),
                    logging_steps=1,
                    save_strategy="no",
                    gradient_checkpointing=True,
                    report_to="none",
                    dataloader_pin_memory=False,
                    dataloader_num_workers=0,
                    remove_unused_columns=False,
                    max_grad_norm=1.0,
                )

                trainer = ContinualLoRATrainer(
                    model=model_with_lora,
                    args=training_args,
                    train_dataset=dataset,
                    data_collator=PaddingCollator(tokenizer.pad_token_id),
                    fisher_ema=fisher_ema,
                    kl_weight=seal_cfg.get("kl_weight", 0.1),
                    null_percentile=seal_cfg.get("null_percentile", 95.0),
                )

                LOG.info("  Training LoRA (%d examples, %d replay)...", 1, len(replay_examples))
                trainer.train()

                final_loss = trainer.state.log_history[-1].get("train_loss", 999) if trainer.state.log_history else 999

                if eval_items:
                    eval_result = evaluate_qa(model_with_lora, processor, eval_items, max_eval=10)
                    gain = eval_result["answer_rate"] - baseline["answer_rate"]
                else:
                    gain = -final_loss

                LOG.info("  Result: loss=%.4f, gain=%.4f, format=%s", final_loss, gain, fmt)

                if gain > best_gain:
                    best_gain = gain
                    best_edit = self_edit_text
                    best_fmt_name = fmt

                del trainer
                model_with_lora = model_with_lora.merge_and_unload() if gain > 0 else model
                if gain <= 0:
                    del model_with_lora
                    model_with_lora = None
                gc.collect()
                torch.cuda.empty_cache()

            if best_gain > 0 and best_edit:
                stats["kept"] += 1
                replay_buffer.add([best_edit])
                format_tracker.record(best_fmt_name, best_gain)
                LOG.info("  ✅ KEPT: format=%s, gain=%.4f, replay_size=%d",
                         best_fmt_name, best_gain, len(replay_buffer))
            else:
                stats["discarded"] += 1
                LOG.info("  ❌ DISCARDED: best_gain=%.4f", best_gain)

            stats["items_processed"] = idx + 1

            log_history.append({
                "item_idx": idx,
                "title": item.get("title", "")[:50],
                "best_format": best_fmt_name,
                "best_gain": best_gain,
                "kept": best_gain > 0,
                "replay_size": len(replay_buffer),
                "gpu_temp": get_gpu_temp(),
                "timestamp": datetime.now().isoformat(),
            })

            if (idx + 1) % checkpoint_every == 0:
                ckpt_path = output_dir / f"checkpoint_{idx + 1}"
                LOG.info("Saving checkpoint to %s...", ckpt_path)
                model.save_pretrained(str(ckpt_path))
                processor.save_pretrained(str(ckpt_path))
                with open(output_dir / "log_history.json", "w") as f:
                    json.dump(log_history, f, indent=2, default=str)

        except Exception as e:
            LOG.error("❌ Item %d FAILED: %s — skipping to next", idx + 1, str(e)[:300])
            stats["discarded"] += 1
            gc.collect()
            torch.cuda.empty_cache()
            continue

    # ── Final ──
    total_time = time.time() - start_time

    # Final evaluation
    if eval_items:
        LOG.info("Running final evaluation...")
        final_eval = evaluate_qa(model, processor, eval_items)
        LOG.info("Final: answer_rate=%.1f%% (baseline: %.1f%%)",
                 final_eval["answer_rate"] * 100, baseline["answer_rate"] * 100)
    else:
        final_eval = {}

    # Save adapter (NOT merged model)
    adapter_path = output_dir / "lora_adapter"
    LOG.info("Saving LoRA adapter to %s...", adapter_path)
    model.save_pretrained(str(adapter_path))
    processor.save_pretrained(str(adapter_path))

    # Save results
    results = {
        "config": cfg,
        "stats": stats,
        "baseline": baseline,
        "final_eval": final_eval,
        "format_summary": format_tracker.summary(),
        "total_time_hours": round(total_time / 3600, 2),
        "log_history": log_history,
    }
    with open(output_dir / "results_final.json", "w") as f:
        json.dump(results, f, indent=2, default=str, ensure_ascii=False)

    LOG.info("=" * 70)
    LOG.info("PROYECTO SEAL — COMPLETE")
    LOG.info("  Time: %.1f hours", total_time / 3600)
    LOG.info("  Items: %d processed", stats["items_processed"])
    LOG.info("  Kept: %d, Discarded: %d", stats["kept"], stats["discarded"])
    LOG.info("  Best format: %s", format_tracker.best_format()[0] if sum(format_tracker._counts.values()) > 0 else "N/A")
    LOG.info("  Results: %s", output_dir / "results_final.json")
    LOG.info("=" * 70)

    del model
    gc.collect()
    torch.cuda.empty_cache()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Proyecto SEAL — Autonomous Medical Model Training")
    parser.add_argument("--config", type=str, required=True, help="Path to model YAML config")
    parser.add_argument("--train_data", type=str, default=None, help="Override train data path")
    parser.add_argument("--eval_data", type=str, default=None, help="Override eval data path")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    if args.train_data:
        cfg["train_data"] = args.train_data
    if args.eval_data:
        cfg["eval_data"] = args.eval_data

    # Write merged config back for the engine
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        yaml.dump(cfg, tmp)
        tmp_path = tmp.name

    run_seal(tmp_path)
    os.unlink(tmp_path)
