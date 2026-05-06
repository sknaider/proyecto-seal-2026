---
name: seal-eval
description: "Benchmark evaluation of medical AI models (MedGemma SEAL). Runs 5-question medical benchmark and reports accuracy, refusals, and keyword scores."
tags: [seal, medical, evaluation, benchmark, medgemma]
---

# /seal-eval — Medical Model Evaluation

Runs the SEAL medical benchmark against loaded models.

## Usage

```
/seal-eval                    # Default: eval merged model
/seal-eval adapter            # Eval LoRA adapter
/seal-eval --model <path>     # Eval specific model
```

## What it evaluates

5 medical questions (Spanish + English):
1. Neumonía - diagnóstico y tratamiento
2. IAMCEST - manejo emergencia cardíaca
3. DM2 con HbA1c - ajuste terapéutico
4. Hyperthyroidism (EN) - diagnosis and management
5. Ibuprofeno - mecanismo y contraindicaciones

## Metrics

- **Accuracy** — keyword score per question (0-100%)
- **Refusals** — count of refused medical responses
- **Speed** — tokens/second
- **Overall** — weighted average

## How to execute

```bash
cd ~/IA/proyecto-seal
/home/dadito/IA/seal-spark/.venv/bin/python3 eval_benchmark.py \
  --model /home/dadito/IA/modelos/llm/medgemma-27b-seal-v1 \
  --output results/benchmark_$(date +%Y%m%d_%H%M).json
```

## Output

```
SEAL EVAL — {model} — {timestamp}
Q1 Neumonía:     {score}% {PASS/FAIL}
Q2 IAMCEST:      {score}% {PASS/FAIL}
Q3 DM2-HbA1c:    {score}% {PASS/FAIL}
Q4 Hyperthyroid:  {score}% {PASS/FAIL}
Q5 Ibuprofeno:    {score}% {PASS/FAIL}
Overall: {avg}% | Refusals: {count} | Speed: {t/s} t/s
```
