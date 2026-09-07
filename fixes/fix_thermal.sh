#!/bin/bash
# Fix: GPU too hot (>85°C)
echo "[$(date)] Applying thermal fix..."
pkill -f finetune_spanish 2>/dev/null
echo "[$(date)] Waiting 120s for GPU to cool..."
sleep 120
TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null)
echo "[$(date)] GPU temp now: ${TEMP}°C"
cd /home/dadito/IA/proyecto-seal
nohup /home/dadito/IA/seal-spark/.venv/bin/python3 finetune_spanish.py >> finetune_bilingual.log 2>&1 &
echo "[$(date)] Thermal fix applied. Restarted PID: $!"
