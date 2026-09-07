#!/usr/bin/env python3
"""
Merge LoRA adapter into base MedGemma 27B → create standalone model.
Output: medgemma-27b-seal-v1 (~52GB, ready to use without adapter)
"""
import time
import torch
from peft import PeftModel
from transformers import AutoProcessor, AutoModelForImageTextToText

BASE_PATH = "/home/dadito/IA/modelos/llm/medgemma-27b-it"
ADAPTER_PATH = "/home/dadito/IA/proyecto-seal/results/medgemma_spanish_ft/lora_adapter"
OUTPUT_PATH = "/home/dadito/IA/modelos/llm/medgemma-27b-seal-v1"

def main():
    t0 = time.time()

    print("=" * 60)
    print("MERGE: MedGemma 27B + LoRA SEAL adapter → seal-v1")
    print("=" * 60)

    # 1. Load processor
    print("\n[1/5] Loading processor...")
    processor = AutoProcessor.from_pretrained(BASE_PATH, trust_remote_code=True)

    # 2. Load base model
    print("[2/5] Loading base MedGemma 27B (BF16)...")
    model = AutoModelForImageTextToText.from_pretrained(
        BASE_PATH, torch_dtype=torch.bfloat16, device_map="cpu", trust_remote_code=True,
    )
    print(f"       Base loaded in {time.time() - t0:.0f}s")

    # 3. Load LoRA adapter
    print(f"[3/5] Loading LoRA adapter from {ADAPTER_PATH}...")
    model = PeftModel.from_pretrained(model, ADAPTER_PATH)
    print(f"       Adapter loaded in {time.time() - t0:.0f}s")

    # 4. Merge
    print("[4/5] Merging adapter into base model...")
    model = model.merge_and_unload()
    print(f"       Merged in {time.time() - t0:.0f}s")

    # 5. Save
    print(f"[5/5] Saving merged model to {OUTPUT_PATH}...")
    model.save_pretrained(OUTPUT_PATH, safe_serialization=True)
    processor.save_pretrained(OUTPUT_PATH)
    print(f"       Saved in {time.time() - t0:.0f}s")

    print("\n" + "=" * 60)
    print(f"DONE — {OUTPUT_PATH}")
    print(f"Total time: {(time.time() - t0) / 60:.1f} minutes")
    print("=" * 60)


if __name__ == "__main__":
    main()
