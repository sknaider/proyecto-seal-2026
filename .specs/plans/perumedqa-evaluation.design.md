# PeruMedQA Evaluation Plan — MedGemma-SEAL v2
**Author:** ADA (Team SEAL)  
**Date:** 2026-04-06  
**Status:** Ready to run — Awaiting William's return  
**Reference:** arxiv:2509.11517 (Medical Science Educator, Springer Nature 2026)

---

## Objective

Evaluate MedGemma-SEAL v2 (MedGemma-27B base + Ronda 2 LoRA adapter) against the  
PeruMedQA benchmark and compare against:
1. MedGemma-27B base (no fine-tuning)
2. Published scores from arxiv paper (MedGemma-27b base: 89.29% Psychiatry)

**Expected result:** SEAL v2 > base model on Spanish Peruvian medical questions,  
validating our fine-tuning strategy.

---

## Dataset

- **Path:** `~/IA/datasets/raw/PeruMedQA/01.Datasets/combined_exam_dataset.csv`
- **Size:** 8,380 questions
- **Language:** Spanish
- **Format:** Multiple choice (A-E), 12 specialties

| Specialty | Questions |
|---|---|
| Anestesiología | 700 |
| Cirugía General | 700 |
| Oftalmología | 700 |
| Psiquiatría | 700 |
| Pediatría | 700 |
| Ginecología y Obstetricia | 700 |
| Prueba A (mixed) | 690 |
| Prueba B (mixed) | 690 |
| Cirugía de Tórax y Cardiovascular | 600 |
| Urología | 600 |
| Radiología | 600 |
| Anatomía Patológica & Patología | 600 |
| Neurocirugía | 400 |

---

## Execution Plan

### Phase 1: Quick validation (< 30 min)
```bash
# Sample 200 questions from Psychiatry (MedGemma was best here per paper)
cd ~/IA/proyecto-seal
/home/dadito/IA/seal-spark/.venv/bin/python3 eval_perumedqa.py \
    --mode seal_v2 \
    --limit 200 \
    --output results/eval_perumedqa/quick_test.json
```

### Phase 2: Baseline comparison
```bash
# Base model (no adapter) — 500 questions  
/home/dadito/IA/seal-spark/.venv/bin/python3 eval_perumedqa.py \
    --mode base \
    --limit 500

# SEAL v2 — same 500 questions (random_state=42 ensures same sample)
/home/dadito/IA/seal-spark/.venv/bin/python3 eval_perumedqa.py \
    --mode seal_v2 \
    --limit 500
```

### Phase 3: Full evaluation (~2-3h)
```bash
# Full 8,380 questions — run overnight
/home/dadito/IA/seal-spark/.venv/bin/python3 eval_perumedqa.py \
    --mode seal_v2 \
    --limit 0 \
    --output results/eval_perumedqa/full_eval_$(date +%Y%m%d).json
```

---

## Success Criteria

| Metric | Minimum (acceptable) | Target (good) | Excellent |
|---|---|---|---|
| Overall accuracy | > 70% | > 80% | > 89.29% (beats base) |
| Psychiatry | > 80% | > 89.29% | > 90% |
| Spanish specialties | > 65% | > 75% | > 85% |
| Improvement vs base | +1% | +5% | +10% |

**Key question:** Does SEAL v2 adapter improve over MedGemma-27b base on Peruvian Spanish medical questions?

---

## Resources Required

- **Hardware:** DGX Spark (128GB unified memory, CUDA 12.8)
- **Time:** ~30min (200q), ~1h (500q), ~2-3h (8380q)
- **VRAM:** ~60-70GB for BF16 inference + adapter
- **Script:** `~/IA/proyecto-seal/eval_perumedqa.py` ✅ ready

---

## Expected Publication Impact

If SEAL v2 outperforms base MedGemma-27b on PeruMedQA:
- Comparison against peer-reviewed Peruvian medical benchmark
- Published in Medical Science Educator (Springer Nature) — citable reference
- Validates Spanish fine-tuning approach for Latin American clinical AI
- Directly supports AXION Medical deployment positioning
