#!/usr/bin/env python3
"""
serve_medgemma.py — Servidor de inferencia FastAPI para MedGemma 27B.
Carga el modelo UNA VEZ y expone API REST en localhost:8080.
Cola interna para requests secuenciales (DGX Spark no soporta instancias paralelas de 27B).

Uso:
  python3 serve_medgemma.py --adapter results/medgemma_ronda2/lora_adapter
  python3 serve_medgemma.py --base   # sin adapter (modelo base puro)
"""
import argparse
import asyncio
import concurrent.futures
import json
import logging
import os
import re
import time
from typing import Optional

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from peft import PeftModel
from pydantic import BaseModel
from transformers import AutoModelForImageTextToText, AutoProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("serve_medgemma")

MODEL_PATH = "/home/dadito/IA/modelos/llm/medgemma-27b-it"
ADAPTER_R2 = "results/medgemma_ronda2/lora_adapter"
ADAPTER_R1 = "results/medgemma_spanish_ft/lora_adapter"

app = FastAPI(title="MedGemma Inference Server", version="1.0")

# Global model state
model = None
processor = None
model_lock = asyncio.Lock()
adapter_label = "unknown"
# Thread pool for blocking model.generate() — keeps asyncio event loop responsive
_thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

SYSTEM_PROMPT = (
    "You are an expert medical assistant. Respond in the same language "
    "as the question — Spanish or English. Be clear, precise, and direct. "
    "Provide complete clinical information. No filler phrases."
)

REFUSAL_MARKERS = [
    "no puedo", "lo siento, no", "i cannot", "i can't", "i am not able",
    "disclaimer", "as an ai", "as a language model", "i don't have the ability",
    "no estoy en posición",
]


class GenerateRequest(BaseModel):
    prompt: str
    max_tokens: int = 512
    temperature: float = 0.3
    system_prompt: Optional[str] = None


class GenerateResponse(BaseModel):
    response: str
    tokens_generated: int
    time_s: float
    speed_tps: float
    is_refusal: bool
    adapter: str


def load_model(adapter_path: Optional[str]) -> None:
    global model, processor, adapter_label

    log.info(f"Loading processor from {MODEL_PATH}...")
    processor = AutoProcessor.from_pretrained(MODEL_PATH, trust_remote_code=True)

    log.info("Loading MedGemma 27B (BF16)...")
    # Note: load without device_map="auto" when applying PEFT adapter to avoid
    # accelerate 1.13.0 bug: unhashable type 'set' in get_balanced_memory with Gemma3.
    # DGX Spark has unified memory (128GB) so moving to cuda after load is fine.
    if adapter_path:
        base = AutoModelForImageTextToText.from_pretrained(
            MODEL_PATH,
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )
        log.info("Moving model to CUDA...")
        base = base.to("cuda")
    else:
        # Base-only: device_map="auto" works fine without PEFT
        base = AutoModelForImageTextToText.from_pretrained(
            MODEL_PATH,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )

    if adapter_path:
        if not os.path.exists(adapter_path):
            raise FileNotFoundError(f"Adapter not found: {adapter_path}")
        log.info(f"Loading LoRA adapter from {adapter_path}...")
        model = PeftModel.from_pretrained(base, adapter_path)
        adapter_label = adapter_path
    else:
        model = base
        adapter_label = "base"

    model.eval()
    log.info(f"Model ready. Adapter: {adapter_label}")


@app.get("/health")
async def health():
    return {"status": "ok", "adapter": adapter_label, "model": MODEL_PATH}


@app.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    if model is None:
        raise HTTPException(status_code=503, detail="Model not loaded")

    sys_prompt = req.system_prompt or SYSTEM_PROMPT

    messages = [
        {"role": "system", "content": [{"type": "text", "text": sys_prompt}]},
        {"role": "user", "content": [{"type": "text", "text": req.prompt}]},
    ]

    async with model_lock:
        inputs = processor.apply_chat_template(
            messages,
            add_generation_prompt=True,
            tokenize=True,
            return_dict=True,
            return_tensors="pt",
        ).to(model.device, dtype=torch.bfloat16)

        input_len = inputs["input_ids"].shape[-1]
        max_tokens = req.max_tokens
        temperature = req.temperature

        def _generate_blocking():
            with torch.inference_mode():
                return model.generate(
                    **inputs,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    do_sample=temperature > 0,
                )

        t0 = time.time()
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(_thread_pool, _generate_blocking)
        elapsed = time.time() - t0

    tokens_gen = out.shape[-1] - input_len
    response_text = processor.decode(out[0][input_len:], skip_special_tokens=True)
    response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()

    is_refusal = any(m in response_text.lower() for m in REFUSAL_MARKERS)

    return GenerateResponse(
        response=response_text,
        tokens_generated=tokens_gen,
        time_s=round(elapsed, 2),
        speed_tps=round(tokens_gen / elapsed, 1) if elapsed > 0 else 0,
        is_refusal=is_refusal,
        adapter=adapter_label,
    )


@app.get("/adapter")
async def get_adapter():
    return {"adapter": adapter_label}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MedGemma inference server")
    parser.add_argument("--adapter", default=None, help="Path to LoRA adapter")
    parser.add_argument("--base", action="store_true", help="Load base model only (no adapter)")
    parser.add_argument("--auto", action="store_true", help="Auto-detect adapter (ronda2 > ronda1)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    adapter_path = None
    if not args.base:
        if args.adapter:
            adapter_path = args.adapter
        else:
            # Auto-detect: ronda2 > ronda1
            adapter_path = ADAPTER_R2 if os.path.exists(ADAPTER_R2) else ADAPTER_R1

    load_model(adapter_path)

    log.info(f"Starting server on {args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
