#!/bin/bash
SEAL_DIR="/home/dadito/IA/proyecto-seal"
PYTHON="/home/dadito/IA/seal-spark/.venv/bin/python3"
LOG="$SEAL_DIR/finetune_bilingual.log"
MAX_RESTARTS=5
COUNT=0
cd "$SEAL_DIR"
echo "[$(date)] Watchdog FT iniciado"
while true; do
    if ! pgrep -f "finetune_spanish" > /dev/null 2>&1; then
        if grep -q "COMPLETE" "$LOG" 2>/dev/null; then
            echo "[$(date)] ✅ Completado."; exit 0
        fi
        COUNT=$((COUNT + 1))
        if [ $COUNT -gt $MAX_RESTARTS ]; then
            echo "[$(date)] ❌ Max reinicios."; exit 1
        fi
        echo "[$(date)] ⚠️ Muerto. Reiniciando ($COUNT/$MAX_RESTARTS)..."
        $PYTHON -c "import torch; torch.cuda.empty_cache()" 2>/dev/null
        sleep 10
        nohup $PYTHON finetune_spanish.py >> "$LOG" 2>&1 &
        echo "[$(date)] ✅ Relanzado PID: $!"
        sleep 60
    else
        TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null)
        STEP=$(tail -1 "$LOG" 2>/dev/null | grep -oP '\d+/36120' | tail -1)
        echo "[$(date)] 🟢 OK | GPU: ${TEMP}°C | Step: $STEP"
    fi
    sleep 120
done
