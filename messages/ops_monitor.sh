#!/bin/bash
# ops_monitor.sh — OPS: Monitor y reparador automático del equipo SEAL
# Monitorea: seal-chat (HTTP :8765), seal-agent (systemctl), puerto 8765
# Acción: restart automático + alerta a terminal_log.jsonl
# Protección: MAX_RESTARTS=3 por hora por servicio

DIR="$HOME/IA/proyecto-seal/messages"
LOG="$DIR/terminal_log.jsonl"
RESTART_LOG="$DIR/.ops_restarts"
CHECK_INTERVAL=30   # segundos entre checks
MAX_RESTARTS=3      # máximo reinicios por hora por servicio

# ── Helpers ──────────────────────────────────────────────────────────────────

ops_log() {
    local level="$1"
    local msg="$2"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    local id="ops_${RANDOM}_$(date +%s)"
    printf '{"id":"%s","from":"OPS","to":"sistema","timestamp":"%s","type":"alert","message":"[OPS][%s] %s"}\n' \
        "$id" "$ts" "$level" "$msg" >> "$LOG"
    echo "[$(date '+%H:%M:%S')][OPS][$level] $msg"
}

# Contar reinicios en última hora para un servicio
count_restarts_last_hour() {
    local svc="$1"
    local now
    now=$(date +%s)
    local cutoff=$((now - 3600))
    [ ! -f "$RESTART_LOG" ] && echo 0 && return
    grep "^${svc}:" "$RESTART_LOG" | awk -F: -v c="$cutoff" '$2 > c {count++} END {print count+0}'
}

# Registrar un reinicio
record_restart() {
    local svc="$1"
    echo "${svc}:$(date +%s)" >> "$RESTART_LOG"
    # Limpiar entradas antiguas (>2h)
    local cutoff=$(( $(date +%s) - 7200 ))
    if [ -f "$RESTART_LOG" ]; then
        grep -v "^" "$RESTART_LOG" > /dev/null 2>&1  # no-op, solo limpiar si crece
        awk -F: -v c="$cutoff" '$2 > c' "$RESTART_LOG" > "${RESTART_LOG}.tmp" && mv "${RESTART_LOG}.tmp" "$RESTART_LOG"
    fi
}

# Intentar reiniciar un servicio con protección MAX_RESTARTS
try_restart() {
    local svc="$1"
    local restarts
    restarts=$(count_restarts_last_hour "$svc")

    if [ "$restarts" -ge "$MAX_RESTARTS" ]; then
        ops_log "CRITICO" "$svc fallo repetidamente ($restarts reinicios/hora). Intervencion manual requerida."
        return 1
    fi

    ops_log "WARN" "$svc caido — reiniciando (intento $((restarts+1))/$MAX_RESTARTS)"
    systemctl --user restart "$svc"
    sleep 3
    record_restart "$svc"

    if systemctl --user is-active --quiet "$svc"; then
        ops_log "OK" "$svc reiniciado correctamente"
        return 0
    else
        ops_log "ERROR" "$svc no arranco despues del reinicio"
        return 1
    fi
}

# ── Checks ───────────────────────────────────────────────────────────────────

check_seal_chat() {
    # 1. Verificar servicio systemd
    if ! systemctl --user is-active --quiet seal-chat; then
        try_restart "seal-chat"
        return
    fi

    # 2. Verificar HTTP health (puerto 8765 responde)
    if ! curl -sf --max-time 5 http://localhost:8765/ > /dev/null 2>&1; then
        ops_log "WARN" "seal-chat activo en systemd pero HTTP :8765 no responde — reiniciando"
        # Matar proceso zombie en el puerto primero
        local zombie_pid
        zombie_pid=$(lsof -ti :8765 2>/dev/null)
        if [ -n "$zombie_pid" ]; then
            ops_log "INFO" "Matando proceso zombie en :8765 (PID $zombie_pid)"
            kill -9 "$zombie_pid" 2>/dev/null
            sleep 1
        fi
        try_restart "seal-chat"
    fi
}

check_seal_agent() {
    if ! systemctl --user is-active --quiet seal-agent; then
        try_restart "seal-agent"
    fi
}

# ── Main loop ────────────────────────────────────────────────────────────────

ops_log "INFO" "OPS iniciado — monitoreando seal-chat cada ${CHECK_INTERVAL}s (seal-agent deshabilitado por William)"

while true; do
    check_seal_chat
    # seal-agent deshabilitado permanentemente — NO reiniciar
    sleep "$CHECK_INTERVAL"
done
