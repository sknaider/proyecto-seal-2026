#!/bin/bash
# Fix: CUDA Out of Memory
echo "[$(date)] Applying OOM fix..."
pkill -f finetune_spanish 2>/dev/null
sleep 5
python3 -c "import torch; torch.cuda.empty_cache()" 2>/dev/null
sync; echo 3 | sudo tee /proc/sys/vm/drop_caches > /dev/null 2>&1
sleep 10
cd /home/dadito/IA/proyecto-seal
nohup /home/dadito/IA/seal-spark/.venv/bin/python3 finetune_spanish.py >> finetune_bilingual.log 2>&1 &
echo "[$(date)] OOM fix applied. Restarted PID: $!"
