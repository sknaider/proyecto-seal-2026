#!/bin/bash
# Proyecto SEAL — Watchdog
# Monitorea el proceso de entrenamiento. Si muere, lo relanza automáticamente.
# Uso: nohup bash watchdog.sh > watchdog.log 2>&1 &

SEAL_DIR="/home/dadito/IA/proyecto-seal"
PYTHON="/home/dadito/IA/seal-spark/.venv/bin/python3"
CONFIG="models/medgemma_27b.yaml"
TRAIN_DATA="data/unified_train.json"
EVAL_DATA="data/unified_eval.json"
LOG="$SEAL_DIR/seal_overnight.log"
MAX_RESTARTS=5
RESTART_COUNT=0
CHECK_INTERVAL=300  # 5 minutos

cd "$SEAL_DIR"

echo "[$(date)] Watchdog iniciado. Monitoreando Proyecto SEAL..."

while true; do
    # Verificar si el proceso está corriendo
    if ! pgrep -f "core.seal_engine" > /dev/null 2>&1; then
        RESTART_COUNT=$((RESTART_COUNT + 1))

        if [ $RESTART_COUNT -gt $MAX_RESTARTS ]; then
            echo "[$(date)] ❌ Máximo de reinicios ($MAX_RESTARTS) alcanzado. Watchdog detenido."
            exit 1
        fi

        # Verificar si terminó normalmente (COMPLETE en log)
        if grep -q "PROYECTO SEAL — COMPLETE" "$LOG" 2>/dev/null; then
            echo "[$(date)] ✅ Entrenamiento completado normalmente. Watchdog terminado."
            exit 0
        fi

        # Verificar el error
        LAST_ERROR=$(tail -5 "$LOG" 2>/dev/null | grep -i "error\|traceback" | tail -1)
        echo "[$(date)] ⚠️ Proceso muerto. Error: $LAST_ERROR"
        echo "[$(date)] 🔄 Reiniciando (intento $RESTART_COUNT/$MAX_RESTARTS)..."

        # Limpiar GPU
        $PYTHON -c "import torch; torch.cuda.empty_cache()" 2>/dev/null
        sleep 10

        # Relanzar
        nohup $PYTHON -m core.seal_engine \
            --config "$CONFIG" \
            --train_data "$TRAIN_DATA" \
            --eval_data "$EVAL_DATA" \
            >> "$LOG" 2>&1 &

        echo "[$(date)] ✅ Relanzado con PID: $!"
        sleep 60  # Esperar a que cargue
    else
        # Proceso vivo — verificar progreso
        LAST_LINE=$(tail -1 "$LOG" 2>/dev/null)
        GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null)
        echo "[$(date)] 🟢 Corriendo | GPU: ${GPU_TEMP}°C | $LAST_LINE"
    fi

    sleep $CHECK_INTERVAL
done
