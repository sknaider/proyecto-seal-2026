#!/usr/bin/env python3
"""
Fine-tune MedGemma 27B — RONDA 2
Continúa desde adapter v1. Dataset: 187K items (41% ES / 59% EN).
Velocidad real: ~56s/step → ~631 steps en 10h → ~5K items procesados.
El dataset completo NO se recorre en 10h — el Trainer shufflea y vemos
una muestra aleatoria de ~2.7% por sesión. Múltiples sesiones = más cobertura.
"""
from __future__ import annotations

import gc
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
from datasets import Dataset
from peft import LoraConfig, PeftModel
from transformers import (
    AutoProcessor,
    AutoModelForImageTextToText,
    TrainingArguments,
    Trainer,
    TrainerCallback,
)

LOG = logging.getLogger("finetune_es")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)

MODEL_PATH = "/home/dadito/IA/modelos/llm/medgemma-27b-it"
MAX_HOURS = 10.0


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


class ThermalCallback(TrainerCallback):
    def on_step_end(self, args, state, control, **kwargs):
        if state.global_step % 50 != 0:
            return
        temp = get_gpu_temp()
        if temp >= 85:
            LOG.warning("GPU at %d°C — CRITICAL. Pausing 60s...", temp)
            gc.collect()
            torch.cuda.empty_cache()
            time.sleep(60)
            if get_gpu_temp() >= 85:
                control.should_training_stop = True
        elif temp >= 75:
            LOG.warning("GPU at %d°C — warm.", temp)
            time.sleep(10)


class TimeoutCallback(TrainerCallback):
    def __init__(self, deadline: float):
        self.deadline = deadline

    def on_step_end(self, args, state, control, **kwargs):
        if time.time() > self.deadline:
            LOG.info("Time limit reached. Stopping.")
            control.should_training_stop = True


class ProgressCallback(TrainerCallback):
    def __init__(self, start_time: float):
        self.start_time = start_time

    def on_log(self, args, state, control, logs=None, **kwargs):
        if state.global_step % 50 == 0 and state.global_step > 0:
            elapsed = time.time() - self.start_time
            speed = state.global_step / elapsed
            remaining = state.max_steps - state.global_step
            eta = remaining / speed if speed > 0 else 0
            LOG.info(
                "Step %d/%d (%.1f%%) | Loss: %.4f | Speed: %.2f steps/s | ETA: %s | GPU: %d°C",
                state.global_step, state.max_steps,
                100.0 * state.global_step / state.max_steps,
                logs.get("loss", 0), speed,
                str(timedelta(seconds=int(eta))), get_gpu_temp(),
            )


def format_as_chat(item: dict, processor) -> dict:
    """Format a QA item as a chat conversation and tokenize."""
    question = item["question"]
    answer = item["answer"]
    title = item.get("title", "")

    messages = [
        {"role": "system", "content": [{"type": "text", "text":
            "You are an expert medical assistant. Respond in the same language "
            "as the question — Spanish or English. Be clear, precise, and direct. "
            "No filler phrases. Go straight to the medical answer."}]},
        {"role": "user", "content": [{"type": "text", "text":
            f"Tema: {title}\nPregunta: {question}" if title else question}]},
        {"role": "assistant", "content": [{"type": "text", "text": answer}]},
    ]

    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False,
    )
    tokenizer = processor.tokenizer
    tokenized = tokenizer(text, truncation=True, max_length=1024, padding=False)

    # MedGemma (Gemma 3) requires token_type_ids for training
    token_type_ids = [0] * len(tokenized["input_ids"])

    return {
        "input_ids": tokenized["input_ids"],
        "attention_mask": tokenized["attention_mask"],
        "token_type_ids": token_type_ids,
        "labels": tokenized["input_ids"].copy(),
    }


class PaddingCollator:
    def __init__(self, pad_token_id: int):
        self.pad_token_id = pad_token_id

    def __call__(self, examples):
        max_len = max(len(e["input_ids"]) for e in examples)
        input_ids, attention_mask, token_type_ids, labels = [], [], [], []
        for e in examples:
            pad_len = max_len - len(e["input_ids"])
            input_ids.append(e["input_ids"] + [self.pad_token_id] * pad_len)
            attention_mask.append(e["attention_mask"] + [0] * pad_len)
            token_type_ids.append(e.get("token_type_ids", [0] * len(e["input_ids"])) + [0] * pad_len)
            labels.append(e["labels"] + [-100] * pad_len)
        return {
            "input_ids": torch.tensor(input_ids),
            "attention_mask": torch.tensor(attention_mask),
            "token_type_ids": torch.tensor(token_type_ids),
            "labels": torch.tensor(labels),
        }


def main():
    start_time = time.time()
    deadline = start_time + MAX_HOURS * 3600
    output_dir = Path("results/medgemma_ronda2")
    output_dir.mkdir(parents=True, exist_ok=True)

    LOG.info("=" * 70)
    LOG.info("MEDGEMMA 27B — RONDA 2: ESPAÑOL MASIVO (Opción B)")
    LOG.info("=" * 70)
    LOG.info("Misión: Dominar español médico con 76K items ES + 111K EN refuerzo")
    LOG.info("Max hours: %.1f", MAX_HOURS)
    LOG.info("Deadline: %s", datetime.fromtimestamp(deadline).strftime("%H:%M:%S"))

    # Backup adapter v1 antes de entrenar (regla cmd_005 de JARVIS)
    adapter_v1_backup = Path("results/adapters/v1_backup")
    if not adapter_v1_backup.exists():
        import shutil
        LOG.info("Backing up adapter v1 to %s...", adapter_v1_backup)
        adapter_v1_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree("results/medgemma_spanish_ft/lora_adapter", str(adapter_v1_backup))
        LOG.info("Backup complete.")
    else:
        LOG.info("Adapter v1 backup already exists at %s", adapter_v1_backup)

    # Load processor
    LOG.info("Loading processor...")
    processor = AutoProcessor.from_pretrained(MODEL_PATH, trust_remote_code=True)
    tokenizer = processor.tokenizer
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load model
    LOG.info("Loading MedGemma 27B (BF16)...")
    model = AutoModelForImageTextToText.from_pretrained(
        MODEL_PATH, torch_dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
    )
    LOG.info("Model loaded. GPU mem: %.1f GB", torch.cuda.max_memory_allocated() / 1e9)

    # Load adapter v1 as starting point (NOT from scratch)
    ADAPTER_V1 = "results/medgemma_spanish_ft/lora_adapter"
    if not Path(ADAPTER_V1).exists():
        LOG.error("Adapter v1 NOT FOUND at %s — ABORTING", ADAPTER_V1)
        raise FileNotFoundError(f"Adapter v1 no encontrado: {ADAPTER_V1}")
    LOG.info("Loading LoRA adapter v1 from %s...", ADAPTER_V1)
    model = PeftModel.from_pretrained(model, ADAPTER_V1, is_trainable=True)
    model.gradient_checkpointing_enable(
        gradient_checkpointing_kwargs={"use_reentrant": False}
    )
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    LOG.info("LoRA: %.1fM trainable / %.1fB total (%.3f%%)",
             trainable / 1e6, total / 1e9, 100 * trainable / total)

    # Load and tokenize Spanish data
    LOG.info("Loading ronda 2 dataset (41%% ES, 59%% EN)...")
    with open("data/ronda2_train.json", encoding="utf-8") as f:
        raw_items = json.load(f)
    LOG.info("Raw items: %d (ES: %d, EN: %d)", len(raw_items),
             sum(1 for i in raw_items if i.get('lang')=='es'),
             sum(1 for i in raw_items if i.get('lang')=='en'))

    LOG.info("Tokenizing...")
    tokenized = []
    skipped = 0
    for item in raw_items:
        try:
            t = format_as_chat(item, processor)
            if len(t["input_ids"]) > 10:
                tokenized.append(t)
            else:
                skipped += 1
        except Exception:
            skipped += 1
    LOG.info("Tokenized: %d items (%d skipped)", len(tokenized), skipped)

    dataset = Dataset.from_list(tokenized)

    # Training config
    # Realidad: ~56s/step en DGX Spark con 27B. En 10h ≈ 631 steps.
    # Con accum=8, cada step = 8 items → ~5,048 items en 10h.
    # Dataset de 187K: 1 epoch = 23,410 steps ≈ 363 horas. IMPOSIBLE en 10h.
    # Estrategia: 1 epoch (el TimeoutCallback cortará), max_steps como safety.
    EFFECTIVE_BATCH = 8  # batch=1 * accum=8
    steps_per_epoch = len(dataset) // EFFECTIVE_BATCH
    REALISTIC_STEPS = 700  # ~10h con margen para save
    LOG.info("Dataset: %d | Steps/epoch: %d (TEÓRICO) | Realista: ~%d steps en 10h",
             len(dataset), steps_per_epoch, REALISTIC_STEPS)
    LOG.info("Cobertura estimada: %.1f%% del dataset por sesión",
             100.0 * REALISTIC_STEPS * EFFECTIVE_BATCH / len(dataset))

    training_args = TrainingArguments(
        output_dir=str(output_dir),
        num_train_epochs=1,  # FIX: 2 epochs era imposible (necesitaría ~726h)
        max_steps=REALISTIC_STEPS,  # FIX: safety limit basado en velocidad real
        per_device_train_batch_size=1,
        gradient_accumulation_steps=8,
        learning_rate=1e-4,  # Ronda 2: más bajo para refinar, no destruir
        weight_decay=0.01,
        lr_scheduler_type="cosine",
        warmup_steps=50,  # FIX: 100 era 14% de 700 steps — muy alto. 50 = 7%
        bf16=True,
        logging_steps=10,
        save_strategy="steps",
        save_steps=200,  # FIX: guardar más seguido (cada ~3h en vez de ~8h)
        save_total_limit=3,
        gradient_checkpointing=True,
        report_to="none",
        seed=42,
        dataloader_pin_memory=False,
        dataloader_num_workers=0,
        remove_unused_columns=False,
        max_grad_norm=1.0,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
        data_collator=PaddingCollator(tokenizer.pad_token_id),
        callbacks=[
            ThermalCallback(),
            TimeoutCallback(deadline),
            ProgressCallback(start_time),
        ],
    )

    # Train
    LOG.info("=" * 70)
    LOG.info("STARTING — %d items, 1 epoch, max %d steps (~10h)", len(dataset), REALISTIC_STEPS)
    LOG.info("=" * 70)

    train_start = time.time()
    trainer.train(resume_from_checkpoint="results/medgemma_ronda2/checkpoint-400")
    train_time = time.time() - train_start
    LOG.info("Training finished in %.1f hours", train_time / 3600)

    # Save LoRA adapter ONLY (not merged)
    adapter_path = output_dir / "lora_adapter"
    LOG.info("Saving LoRA adapter to %s...", adapter_path)
    model.save_pretrained(str(adapter_path))
    processor.save_pretrained(str(adapter_path))
    LOG.info("Adapter saved (~100MB)")

    # Results + checksum (sugerencia ADA rpt_006)
    total_time = time.time() - start_time
    final_loss = trainer.state.log_history[-1].get("train_loss", 0) if trainer.state.log_history else 0

    # SHA256 del adapter para detectar corrupción
    import hashlib
    adapter_files = sorted(adapter_path.glob("*.safetensors")) + sorted(adapter_path.glob("*.bin"))
    sha256 = ""
    if adapter_files:
        h = hashlib.sha256()
        for f in adapter_files:
            h.update(f.read_bytes())
        sha256 = h.hexdigest()
        LOG.info("Adapter SHA256: %s", sha256)

    results = {
        "mission": "MedGemma ronda 2 — español masivo",
        "ronda": 2,
        "base_adapter": "results/medgemma_spanish_ft/lora_adapter (v1)",
        "total_time_hours": round(total_time / 3600, 2),
        "train_time_hours": round(train_time / 3600, 2),
        "items_total": len(dataset),
        "items_es": sum(1 for i in raw_items if i.get('lang') == 'es'),
        "items_en": sum(1 for i in raw_items if i.get('lang') == 'en'),
        "steps": trainer.state.global_step,
        "final_loss": final_loss,
        "adapter_path": str(adapter_path),
        "adapter_sha256": sha256,
    }
    with open(output_dir / "results.json", "w") as f:
        json.dump(results, f, indent=2)

    LOG.info("=" * 70)
    LOG.info("COMPLETE — Español médico")
    LOG.info("  Time: %.1f hours", total_time / 3600)
    LOG.info("  Steps: %d", trainer.state.global_step)
    LOG.info("  Final loss: %.4f", final_loss)
    LOG.info("  Adapter: %s", adapter_path)
    LOG.info("=" * 70)

    del model
    gc.collect()
    torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
