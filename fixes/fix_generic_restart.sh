#!/bin/bash
# Fix: Generic restart for recoverable errors
echo "[$(date)] Applying generic restart..."
pkill -f finetune_spanish 2>/dev/null
sleep 10
python3 -c "import torch; torch.cuda.empty_cache()" 2>/dev/null
cd /home/dadito/IA/proyecto-seal
nohup /home/dadito/IA/seal-spark/.venv/bin/python3 finetune_spanish.py >> finetune_bilingual.log 2>&1 &
echo "[$(date)] Generic restart done. PID: $!"
