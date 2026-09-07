#!/bin/bash
# ═══════════════════════════════════════════════
#  ADA — Monitor de entrenamiento MedGemma v2
#  Revisa cada 5 min, auto-fix si hay fallas
# ═══════════════════════════════════════════════

LOG=/home/dadito/IA/proyecto-seal/seal_overnight.log
MONITOR_LOG=/home/dadito/IA/proyecto-seal/monitor_ada.log
VENV=/home/dadito/IA/seal-spark/.venv/bin/python3
SCRIPT=/home/dadito/IA/seal-spark
TRAIN_CMD="$VENV -m finetune_spanish --config models/medgemma_27b.yaml --train_data data/unified_train.json --eval_data data/unified_eval.json"

RESTARTS=0
MAX_RESTARTS=3

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$MONITOR_LOG"
}

check_training() {
    # Estado del proceso
    PID=$(pgrep -f "finetune_spanish" | head -1)
    GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null | head -1 | tr -d ' %')
    GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ')

    # Último step del log
    LAST_STEP=$(grep -oP "Step \K[0-9]+(?=/700)" "$LOG" 2>/dev/null | tail -1)
    LAST_LOSS=$(grep -oP "Loss: \K[0-9.]+" "$LOG" 2>/dev/null | tail -1)
    LAST_LOG_TIME=$(stat -c %Y "$LOG" 2>/dev/null)
    NOW=$(date +%s)
    LOG_AGE=$(( (NOW - LAST_LOG_TIME) / 60 ))

    if [ -n "$PID" ]; then
        log "✅ OK | PID:$PID | Step:${LAST_STEP:-?}/700 | Loss:${LAST_LOSS:-?} | GPU:${GPU_UTIL}% ${GPU_TEMP}°C | Log actualizado hace ${LOG_AGE}min"

        # Alerta si GPU baja mucho (posible hang)
        if [ -n "$GPU_UTIL" ] && [ "$GPU_UTIL" -lt 20 ]; then
            log "⚠️  GPU utilización baja ($GPU_UTIL%) — posible hang"
        fi

        # Alerta si log no se actualiza en 30 min
        if [ "$LOG_AGE" -gt 30 ]; then
            log "⚠️  Log sin actualizar hace ${LOG_AGE}min — revisando proceso..."
        fi

        # Temperatura crítica
        if [ -n "$GPU_TEMP" ] && [ "$GPU_TEMP" -gt 85 ]; then
            log "🔥 TEMPERATURA CRÍTICA: ${GPU_TEMP}°C — alertando"
        fi
    else
        log "❌ PROCESO CAÍDO — Step último conocido: ${LAST_STEP:-desconocido}/700"

        if [ "$RESTARTS" -lt "$MAX_RESTARTS" ]; then
            log "🔄 AUTO-RESTART ($((RESTARTS+1))/$MAX_RESTARTS)..."
            cd "$SCRIPT" && nohup $TRAIN_CMD >> "$LOG" 2>&1 &
            NEW_PID=$!
            RESTARTS=$((RESTARTS+1))
            sleep 10
            if kill -0 "$NEW_PID" 2>/dev/null; then
                log "✅ Restart exitoso — nuevo PID: $NEW_PID"
            else
                log "❌ Restart falló — revisar manualmente"
            fi
        else
            log "🛑 MAX RESTARTS alcanzado ($MAX_RESTARTS) — William debe intervenir"
        fi
    fi
}

log "════════════════════════════════════════"
log "ADA Monitor iniciado — intervalo: 5 min"
log "════════════════════════════════════════"

# Primera verificación inmediata
check_training

# Loop cada 5 minutos
while true; do
    sleep 300
    check_training
done
