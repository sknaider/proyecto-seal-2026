#!/usr/bin/env python3
"""
SEAL Benchmark: 5 medical questions to evaluate fine-tuned MedGemma 27B.
Tests bilingual medical competence (Spanish + English).
"""
import json
import time
import torch
from peft import PeftModel
from transformers import AutoProcessor, AutoModelForImageTextToText

import argparse
import os

MODEL_PATH = "/home/dadito/IA/modelos/llm/medgemma-27b-it"
# Default to ronda 2 adapter, fallback to ronda 1
ADAPTER_PATH_R2 = "results/medgemma_ronda2/lora_adapter"
ADAPTER_PATH_R1 = "results/medgemma_spanish_ft/lora_adapter"
ADAPTER_PATH = ADAPTER_PATH_R2 if os.path.exists(ADAPTER_PATH_R2) else ADAPTER_PATH_R1

BENCHMARK_QUESTIONS = [
    {
        "id": 1,
        "lang": "es",
        "question": "¿Cuál es el tratamiento de primera línea para la neumonía adquirida en la comunidad en un paciente adulto ambulatorio sin comorbilidades?",
        "expected_keywords": ["amoxicilina", "macrólido", "azitromicina", "doxiciclina"],
    },
    {
        "id": 2,
        "lang": "es",
        "question": "Un paciente de 55 años presenta dolor torácico opresivo irradiado al brazo izquierdo, diaforesis y disnea. El ECG muestra elevación del segmento ST en V1-V4. ¿Cuál es el diagnóstico más probable y el manejo inmediato?",
        "expected_keywords": ["infarto", "IAMCEST", "cateterismo", "angioplastia", "aspirina", "heparina"],
    },
    {
        "id": 3,
        "lang": "es",
        "question": "¿Cuáles son los criterios diagnósticos de la diabetes mellitus tipo 2 según la ADA?",
        "expected_keywords": ["glucosa", "hemoglobina", "HbA1c", "ayunas", "126", "6.5"],
    },
    {
        "id": 4,
        "lang": "en",
        "question": "What are the classic signs and symptoms of hyperthyroidism, and what is the most common cause?",
        "expected_keywords": ["Graves", "tachycardia", "weight loss", "tremor", "thyroid"],
    },
    {
        "id": 5,
        "lang": "es",
        "question": "Explica el mecanismo de acción de los inhibidores de la bomba de protones (IBP) como el omeprazol y sus principales indicaciones clínicas.",
        "expected_keywords": ["H+/K+ ATPasa", "ácido", "gástrico", "úlcera", "reflujo", "ERGE"],
    },
]


def evaluate():
    print("=" * 70)
    print("SEAL BENCHMARK — MedGemma 27B + LoRA Adapter")
    print("=" * 70)

    print("Loading processor...")
    processor = AutoProcessor.from_pretrained(MODEL_PATH, trust_remote_code=True)

    print("Loading MedGemma 27B (BF16)...")
    base_model = AutoModelForImageTextToText.from_pretrained(
        MODEL_PATH, torch_dtype=torch.bfloat16, device_map="auto", trust_remote_code=True,
    )

    print(f"Loading LoRA adapter from {ADAPTER_PATH}...")
    model = PeftModel.from_pretrained(base_model, ADAPTER_PATH)
    model.eval()
    print("Model ready.\n")

    results = []
    for q in BENCHMARK_QUESTIONS:
        print(f"--- Question {q['id']} ({q['lang'].upper()}) ---")
        print(f"Q: {q['question'][:120]}...")

        system_msg = (
            "You are an expert medical assistant. Respond in the same language "
            "as the question — Spanish or English. Be clear, precise, and direct. "
            "No filler phrases. Go straight to the medical answer."
        )

        messages = [
            {"role": "system", "content": [{"type": "text", "text": system_msg}]},
            {"role": "user", "content": [{"type": "text", "text": q["question"]}]},
        ]

        inputs = processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=True,
            return_dict=True, return_tensors="pt",
        ).to(model.device, dtype=torch.bfloat16)

        input_len = inputs["input_ids"].shape[-1]

        t0 = time.time()
        with torch.inference_mode():
            out = model.generate(
                **inputs,
                max_new_tokens=512,
                temperature=0.3,
                do_sample=True,
            )
        gen_time = time.time() - t0
        tokens_generated = out.shape[-1] - input_len

        response = processor.decode(out[0][input_len:], skip_special_tokens=True)
        # Strip thinking tags if present
        import re
        response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()

        # Check keywords
        response_lower = response.lower()
        matched = [kw for kw in q["expected_keywords"] if kw.lower() in response_lower]
        keyword_score = len(matched) / len(q["expected_keywords"])

        # Check for refusals
        refusal_markers = ["no puedo", "lo siento", "i cannot", "i can't", "disclaimer", "as an ai"]
        is_refusal = any(m in response_lower for m in refusal_markers)

        result = {
            "id": q["id"],
            "lang": q["lang"],
            "response": response,
            "tokens": tokens_generated,
            "time_s": round(gen_time, 1),
            "speed_tps": round(tokens_generated / gen_time, 1) if gen_time > 0 else 0,
            "keyword_score": round(keyword_score, 2),
            "matched_keywords": matched,
            "is_refusal": is_refusal,
            "accepted": not is_refusal and len(response) > 50,
        }
        results.append(result)

        print(f"A: {response[:300]}...")
        print(f"   Keywords: {len(matched)}/{len(q['expected_keywords'])} | "
              f"Refusal: {is_refusal} | {tokens_generated} tokens in {gen_time:.1f}s "
              f"({result['speed_tps']} t/s)")
        print()

    # Summary
    print("=" * 70)
    print("BENCHMARK SUMMARY")
    print("=" * 70)
    accepted = sum(1 for r in results if r["accepted"])
    avg_kw = sum(r["keyword_score"] for r in results) / len(results)
    avg_speed = sum(r["speed_tps"] for r in results) / len(results)

    print(f"Accepted responses: {accepted}/5")
    print(f"Average keyword score: {avg_kw:.0%}")
    print(f"Average speed: {avg_speed:.1f} tokens/s")
    print(f"Refusals: {sum(1 for r in results if r['is_refusal'])}/5")

    for r in results:
        status = "PASS" if r["accepted"] else "FAIL"
        print(f"  Q{r['id']} ({r['lang'].upper()}): {status} | kw={r['keyword_score']:.0%} | "
              f"{r['tokens']} tok @ {r['speed_tps']} t/s")

    # Save — next to the adapter used
    output_dir = os.path.dirname(ADAPTER_PATH) or "results"
    os.makedirs(output_dir, exist_ok=True)
    output_file = os.path.join(output_dir, "benchmark_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({"adapter": ADAPTER_PATH, "summary": {"accepted": accepted,
                   "avg_keyword_score": avg_kw, "avg_speed_tps": avg_speed},
                   "results": results}, f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SEAL Benchmark")
    parser.add_argument("--adapter", help="Path to LoRA adapter (default: auto-detect ronda2 or ronda1)")
    args = parser.parse_args()
    if args.adapter:
        ADAPTER_PATH = args.adapter
    evaluate()
