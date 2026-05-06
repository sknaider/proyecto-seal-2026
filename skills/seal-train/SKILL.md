---
name: seal-train
description: "Monitor and manage MedGemma fine-tuning on DGX Spark. Check training progress, loss curves, GPU temp, and estimated completion time."
tags: [seal, training, fine-tuning, medgemma, medical]
---

# /seal-train — Training Monitor

Monitor active fine-tuning jobs on DGX Spark.

## Usage

```
/seal-train              # Check status of active training
/seal-train start        # Start new fine-tuning run (requires config)
/seal-train history      # Show past training runs
```

## What it monitors

1. **Process** — Is training running? PID, uptime
2. **Progress** — Current step / total steps, % complete
3. **Loss** — Current loss, best loss, trend
4. **GPU** — Temperature, utilization, VRAM usage
5. **ETA** — Estimated time remaining
6. **Config** — Model, dataset, hyperparameters

## How to execute (status)

```bash
# Check if training process is running
ps aux | grep -E "finetune|train" | grep python | grep -v grep

# Check GPU
nvidia-smi --query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits

# Check latest training log
LOGDIR="~/IA/proyecto-seal/results/medgemma_spanish_ft"
if [ -f "$LOGDIR/trainer_log.jsonl" ]; then
    tail -5 "$LOGDIR/trainer_log.jsonl" | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line.strip())
    if 'loss' in d:
        print(f'Step {d.get(\"step\",\"?\")}: loss={d[\"loss\"]:.4f}')
"
fi

# Check monitor log
tail -20 ~/IA/proyecto-seal/agent_monitor.log 2>/dev/null
```

## How to execute (start)

```bash
cd ~/IA/proyecto-seal
/home/dadito/IA/seal-spark/.venv/bin/python3 finetune_spanish.py \
  --model /home/dadito/IA/modelos/llm/medgemma-27b-it \
  --dataset data/mixed_es_en_train.json \
  --output results/medgemma_spanish_ft_v2 \
  --lora-r 64 --lora-alpha 16 \
  --batch-size 1 --accum 8 \
  --lr 2e-4 --scheduler cosine \
  --epochs 1
```

## Output format

```
SEAL TRAIN — {timestamp}
Status: {RUNNING/IDLE/COMPLETED/FAILED}
Model: {model_name}
Progress: {step}/{total} ({pct}%)
Loss: {current} (best: {best} at step {best_step})
GPU: {temp}°C / {util}% / {vram_used}/{vram_total} MB
ETA: {estimated_remaining}
```

## Safety

- Never start training without checking VRAM availability first
- Monitor GPU temp — alert if > 80°C
- Training on DGX Spark only (128GB unified memory)
- Always use LoRA, never full fine-tune on 27B
