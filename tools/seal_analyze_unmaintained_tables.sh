#!/usr/bin/env bash
# ANALYZE sobre las tablas soul_v3 que nunca tuvieron mantenimiento.
#
# Autorizado por William el 28-ago-2026 ("luz verde") sobre la propuesta de FABLE:
# hacerlo en ventana tranquila, avisando. Plan publicado en el canal a las 18:18
# con hora, orden y criterio de corte antes de ejecutarse.
#
# QUÉ HACE: recalcula estadísticas. No borra, no bloquea escrituras, no cambia datos.
# POR QUÉ HACE FALTA: el disparador del autovacuum se calcula sobre `n_live_tup`, que
# en estas tablas nunca se pobló — así que la limpieza automática no podía arrancar
# sola. `chat_messages` estima 606 filas y tiene 122.933. Medido 28-ago.
#
# ORDEN: de menor a mayor, y `chat_messages` (el canal del equipo EN VIVO) al final,
# a propósito: si algo se porta mal en las chicas, la grande no se toca.
set -uo pipefail

CONTENEDOR=${SEAL_ANALYZE_CONTAINER:-seal-memory-db}
DB=seal_memory
USUARIO=seal
DOCKER_BIN=${SEAL_ANALYZE_DOCKER_BIN:-docker}
SEND=${SEAL_ANALYZE_SEND_BIN:-/home/dadito/IA/proyecto-seal/scripts/seal_send.py}
SLEEP_SECONDS=${SEAL_ANALYZE_SLEEP_SECONDS:-30}
REPORTE=""
OVERALL_RC=0

# Orden deliberado: la más chica primero, el canal vivo último.
TABLAS=(instinct_activations memory_retrieval_log inner_monologue event_log chat_messages)

psql_() { "$DOCKER_BIN" exec "$CONTENEDOR" psql -U "$USUARIO" -d "$DB" -tAc "$1" 2>&1; }

# Control de vida del chat ANTES de seguir a la siguiente tabla. Si el canal del
# equipo deja de responder, la secuencia se corta: ninguna tabla vale romper el chat.
chat_vivo() {
    local n
    n=$(psql_ "SELECT count(*) FROM soul_v3.chat_messages WHERE created_at > now() - interval '1 day';")
    [[ "$n" =~ ^[0-9]+$ ]]
}

for T in "${TABLAS[@]}"; do
    EST_ANTES=$(psql_ "SELECT n_live_tup FROM pg_stat_user_tables WHERE schemaname='soul_v3' AND relname='$T';")
    REAL=$(psql_ "SELECT count(*) FROM soul_v3.$T;")

    INICIO=$(date +%s)
    SALIDA=$(psql_ "ANALYZE soul_v3.$T;")
    RC=$?
    DURACION=$(( $(date +%s) - INICIO ))

    EST_DESPUES=$(psql_ "SELECT n_live_tup FROM pg_stat_user_tables WHERE schemaname='soul_v3' AND relname='$T';")

    if [ "$RC" -ne 0 ]; then
        REPORTE+="  $T: FALLÓ (rc=$RC) — $SALIDA"$'\n'
        REPORTE+="  -> secuencia CORTADA; las siguientes no se tocaron."$'\n'
        OVERALL_RC=1
        break
    fi

    REPORTE+=$(printf "  %-22s %8s -> %-8s (real %s) en %ss\n" "$T" "$EST_ANTES" "$EST_DESPUES" "$REAL" "$DURACION")
    REPORTE+=$'\n'

    if ! chat_vivo; then
        REPORTE+="  -> el chat NO respondió tras $T. Secuencia CORTADA por precaución."$'\n'
        OVERALL_RC=1
        break
    fi
    sleep "$SLEEP_SECONDS"
done

# Estado del autovacuum después: es el objetivo real, no el ANALYZE en sí.
VACUUM=$(psql_ "SELECT string_agg(relname||'='||coalesce(last_autovacuum::text,'aun no'), ' · ')
                FROM pg_stat_user_tables WHERE schemaname='soul_v3'
                AND relname IN ('instinct_activations','memory_retrieval_log','inner_monologue','event_log','chat_messages');")

MSG="**Mantenimiento de madrugada — hecho.** Autorizado por William (luz verde, 28-ago).

\`\`\`
$REPORTE\`\`\`

**Autovacuum tras el ANALYZE:**
\`\`\`
$VACUUM
\`\`\`

Recalcular estadísticas no borra datos. Lo que habilita es que PostgreSQL pueda decidir por sí solo cuándo limpiar, que es lo que no podía hacer."

if ! "$SEND" NEXUS equipo "$MSG" --channel web_chat --type status --proactive \
    --idempotency-key "NEXUS-analyze-madrugada-$(date +%Y%m%d)" >/dev/null 2>&1; then
    printf '%s\n' "ERROR: no se pudo publicar el recibo del mantenimiento" >&2
    OVERALL_RC=1
fi
printf '%s\n' "$REPORTE"
exit "$OVERALL_RC"
