#!/usr/bin/env python3
"""
PeruMedQA Evaluation Script for MedGemma-SEAL v2
================================================
Evaluates MedGemma-27B + LoRA adapter against PeruMedQA benchmark.
Reference: arxiv 2509.11517 (Medical Science Educator, Springer Nature 2026)

Usage:
    # Base model (no adapter):
    python3 eval_perumedqa.py --mode base --limit 500

    # SEAL v2 adapter:
    python3 eval_perumedqa.py --mode seal_v2 --limit 500

    # Full evaluation (8380 questions, ~2-3h):
    python3 eval_perumedqa.py --mode seal_v2 --limit 0

Author: ADA (Team SEAL) — 2026-04-06
"""

import argparse
import json
import re
import time
from pathlib import Path

import pandas as pd
import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

# ── Paths ──────────────────────────────────────────────────────────────────
DATASET_PATH = Path("/home/dadito/IA/datasets/raw/PeruMedQA/01.Datasets/combined_exam_dataset.csv")
BASE_MODEL_PATH = Path("/home/dadito/IA/modelos/llm/medgemma-27b-it")
ADAPTER_V2_PATH = Path("/home/dadito/IA/proyecto-seal/results/medgemma_ronda2")
RESULTS_DIR = Path("/home/dadito/IA/proyecto-seal/results/eval_perumedqa")

RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Prompt template ────────────────────────────────────────────────────────
PROMPT_TEMPLATE = """Eres un médico experto. Responde la siguiente pregunta de medicina eligiendo la opción correcta.
Responde SOLO con la letra de la respuesta correcta (A, B, C, D o E).

Pregunta: {question}

Opciones:
A) {option_a}
B) {option_b}
C) {option_c}
D) {option_d}
{option_e_line}

Respuesta correcta (solo la letra):"""


def format_prompt(row) -> str:
    """Format a single question as a prompt."""
    option_e_line = f"E) {row['option_E']}" if pd.notna(row.get('option_E')) and row.get('option_E') else ""
    return PROMPT_TEMPLATE.format(
        question=row['questions'],
        option_a=row['option_A'],
        option_b=row['option_B'],
        option_c=row['option_C'],
        option_d=row['option_D'],
        option_e_line=option_e_line,
    )


def extract_answer(response: str) -> str:
    """Extract single letter answer from model response."""
    response = response.strip().upper()
    # Try first char
    if response and response[0] in 'ABCDE':
        return response[0]
    # Look for pattern like "La respuesta es B" or "(B)"
    match = re.search(r'\b([A-E])\b', response)
    if match:
        return match.group(1)
    return "X"  # Could not parse


def load_model(mode: str, device: str = "cuda"):
    """Load base model or base + SEAL v2 adapter."""
    print(f"Loading tokenizer from {BASE_MODEL_PATH}...")
    tokenizer = AutoTokenizer.from_pretrained(str(BASE_MODEL_PATH))
    tokenizer.pad_token = tokenizer.eos_token

    print(f"Loading base model ({BASE_MODEL_PATH})...")
    model = AutoModelForCausalLM.from_pretrained(
        str(BASE_MODEL_PATH),
        torch_dtype=torch.bfloat16,
        device_map="auto",
    )

    if mode == "seal_v2":
        print(f"Loading SEAL v2 adapter from {ADAPTER_V2_PATH}...")
        model = PeftModel.from_pretrained(model, str(ADAPTER_V2_PATH))
        print("Adapter loaded successfully.")

    model.eval()
    return tokenizer, model


def evaluate(tokenizer, model, df: pd.DataFrame, batch_size: int = 1) -> dict:
    """Run evaluation on dataframe."""
    correct = 0
    total = 0
    results = []
    per_specialty = {}

    # Get specialty from source_folder if available
    start_time = time.time()

    for idx, row in df.iterrows():
        prompt = format_prompt(row)
        correct_letter = str(row['correct_answer']).strip().upper()

        # Tokenize
        inputs = tokenizer(
            prompt,
            return_tensors="pt",
            truncation=True,
            max_length=512,
        ).to(model.device)

        # Generate (max 5 tokens — we just need the letter)
        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=5,
                do_sample=False,
                temperature=None,
                pad_token_id=tokenizer.eos_token_id,
            )

        # Decode only new tokens
        new_tokens = outputs[0][inputs['input_ids'].shape[1]:]
        response = tokenizer.decode(new_tokens, skip_special_tokens=True)
        predicted = extract_answer(response)

        is_correct = predicted == correct_letter
        if is_correct:
            correct += 1
        total += 1

        # Track per specialty (using source_file = specialty name)
        specialty = str(row.get('source_file', 'Unknown'))
        if specialty not in per_specialty:
            per_specialty[specialty] = {'correct': 0, 'total': 0}
        per_specialty[specialty]['total'] += 1
        if is_correct:
            per_specialty[specialty]['correct'] += 1

        results.append({
            'idx': int(idx),
            'question': str(row['questions'])[:100],
            'correct': correct_letter,
            'predicted': predicted,
            'is_correct': is_correct,
            'year': row.get('year'),
            'specialty': specialty,  # source_file = specialty name
        })

        # Progress
        if total % 50 == 0:
            elapsed = time.time() - start_time
            accuracy = correct / total * 100
            rate = total / elapsed
            eta = (len(df) - total) / rate if rate > 0 else 0
            print(f"  [{total}/{len(df)}] Accuracy: {accuracy:.1f}% | {rate:.1f} q/s | ETA: {eta/60:.0f}min")

    # Compute per-specialty accuracy
    specialty_accuracy = {}
    for spec, counts in per_specialty.items():
        specialty_accuracy[spec] = round(counts['correct'] / counts['total'] * 100, 2) if counts['total'] > 0 else 0

    return {
        'total': total,
        'correct': correct,
        'accuracy': round(correct / total * 100, 2) if total > 0 else 0,
        'per_specialty': specialty_accuracy,
        'results': results,
    }


def main():
    parser = argparse.ArgumentParser(description='Evaluate MedGemma-SEAL on PeruMedQA')
    parser.add_argument('--mode', choices=['base', 'seal_v2'], default='seal_v2',
                        help='base = MedGemma-27b only, seal_v2 = base + SEAL LoRA adapter')
    parser.add_argument('--limit', type=int, default=500,
                        help='Number of questions to evaluate (0 = all)')
    parser.add_argument('--year', type=int, default=None,
                        help='Filter by year (e.g., 2025)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output JSON path (auto-generated if not specified)')
    args = parser.parse_args()

    # Load dataset
    print(f"Loading PeruMedQA from {DATASET_PATH}...")
    df = pd.read_csv(DATASET_PATH)
    print(f"Total questions: {len(df)}")

    # Filters
    if args.year:
        df = df[df['year'] == args.year]
        print(f"Filtered to year {args.year}: {len(df)} questions")

    if args.limit and args.limit > 0:
        df = df.sample(n=min(args.limit, len(df)), random_state=42)
        print(f"Sampling {len(df)} questions (random_state=42)")

    # Load model
    tokenizer, model = load_model(args.mode)

    # Evaluate
    print(f"\nStarting evaluation ({args.mode}, {len(df)} questions)...")
    eval_start = time.time()
    results = evaluate(tokenizer, model, df)
    elapsed = time.time() - eval_start

    # Print summary
    print(f"\n{'='*50}")
    print(f"RESULTS: MedGemma-SEAL v2 on PeruMedQA")
    print(f"{'='*50}")
    print(f"Mode:      {args.mode}")
    print(f"Questions: {results['total']}")
    print(f"Correct:   {results['correct']}")
    print(f"Accuracy:  {results['accuracy']}%")
    print(f"Time:      {elapsed/60:.1f}min")
    print()
    print("Per specialty:")
    for spec, acc in sorted(results['per_specialty'].items(), key=lambda x: -x[1]):
        total_spec = sum(1 for r in results['results'] if r['specialty'] == spec)
        print(f"  {spec[:40]:40s}: {acc:.1f}% ({total_spec} q)")

    # Save
    output_path = args.output or str(RESULTS_DIR / f"perumedqa_{args.mode}_{int(time.time())}.json")
    with open(output_path, 'w') as f:
        json.dump({
            'mode': args.mode,
            'benchmark': 'PeruMedQA',
            'reference': 'arxiv:2509.11517',
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'summary': {
                'total': results['total'],
                'correct': results['correct'],
                'accuracy': results['accuracy'],
                'time_minutes': round(elapsed / 60, 1),
            },
            'per_specialty': results['per_specialty'],
            # Don't save full results by default (too large)
        }, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to: {output_path}")

    # Compare against paper baselines
    print(f"\n{'='*50}")
    print("Reference: MedGemma-27b BASE scores from PeruMedQA paper")
    print("  Best specialty: Psychiatry 89.29%")
    print("  Comparison needed: run with --mode base to get baseline")
    print(f"  Your score ({args.mode}): {results['accuracy']}%")


if __name__ == '__main__':
    main()
