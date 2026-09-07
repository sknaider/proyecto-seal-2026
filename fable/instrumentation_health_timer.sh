#!/usr/bin/env bash
# Wrapper de monitoreo del health-check de instrumentación SOUL.
# Dueño: NEXUS (infra de monitoreo + seguridad). Script medido: FABLE (read-only, fable_ltd).
# Política de alerta (NEXUS, como-seguro 12-jun-2026):
#   exit 0 = SILENCIO (solo log; sin spam de heartbeat — regla William).
#   exit 1 = fallo REAL: alerta a web_chat + aviso a DUM (guardia).
set -uo pipefail

SEAL=/home/dadito/IA/proyecto-seal
SCRIPT="$SEAL/fable/instrumentation_health.py"
LOG="$SEAL/fable/instrumentation_health.log"
SEND="$SEAL/messages/send_webchat.py"
TS="$(date '+%Y-%m-%d %H:%M:%S')"

OUT="$(cd "$SEAL/fable" && timeout 60 python3 "$SCRIPT" 2>&1)"
CODE=$?

# Log de cada corrida (rotación simple: mantener últimas ~2000 líneas)
{
  echo "===== $TS exit=$CODE ====="
  echo "$OUT"
} >> "$LOG"
if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 2000 ]; then
  tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

if [ "$CODE" -ne 0 ]; then
  # Resumen de las tablas RANCIAS para la alerta (líneas que no sean VIVA/deprecada/huérfana)
  RANCIAS="$(echo "$OUT" | grep -iE 'RANCIA|STALE|FALLO|ERROR' | head -8)"
  MSG="ALERTA INSTRUMENTACION (health-check exit=$CODE, $TS): una métrica con contrato periódico incumplió su frescura. Esto requiere diagnosticar el escritor; no implica por sí solo que un daemon cayó. Detalle: ${RANCIAS:-ver log $LOG}."
  python3 "$SEND" NEXUS equipo "$MSG" >/dev/null 2>&1
  python3 "$SEND" NEXUS DUM "$MSG" >/dev/null 2>&1
fi

exit "$CODE"
