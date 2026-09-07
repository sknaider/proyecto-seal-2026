#!/usr/bin/env bash
# Runs the credential watchdog and ANNOUNCES an outage on the team channel.
#
# A detector that does not notify is the same failure it is meant to catch: a
# timer that found something and a timer that never ran both emit nothing.
# The alert path is deliberately chat_sessions (seal_send), NOT the Store-A
# tokens under test — otherwise the outage would silence its own alarm.
set -uo pipefail
REPO=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3
STATE=/run/user/1000/seal_credential_watchdog.last

OUT="$("$PY" "$REPO/tools/seal_identity_credential_watchdog.py" 2>&1)"
RC=$?
echo "$OUT"

LAST=""
[ -f "$STATE" ] && LAST="$(cat "$STATE")"
echo "$RC" > "$STATE"

# Only speak on a state CHANGE, so a persistent outage does not flood the channel
# and a recovery is reported once.
[ "$RC" = "$LAST" ] && exit "$RC"

case "$RC" in
  1) MSG=$(printf '**ALERTA — apagón de credenciales Store-A detectado por watchdog.**\n\nLos agentes afectados arrancarán como `external` y todo tool privado será denegado.\n\n```\n%s\n```\n\nCura: `tools/seal_identity_ensure_meta.py --agent <A> --token-dir /run/user/1000/seal`' "$OUT") ;;
  3) MSG=$(printf '**Watchdog de credenciales: UNMEASURABLE** — no pudo probarse capaz de detectar el defecto, así que NO reporta verde.\n\n```\n%s\n```' "$OUT") ;;
  0) [ -n "$LAST" ] && [ "$LAST" != "0" ] && MSG="**Credenciales Store-A recuperadas** — los 4 agentes resuelven identidad de nuevo." || exit 0 ;;
  *) exit "$RC" ;;
esac

"$REPO/scripts/seal_send.py" NEXUS equipo "$MSG" --channel web_chat --type alert --proactive \
  --idempotency-key "NEXUS-cred-watchdog-$RC-$(date +%Y%m%d-%H%M)" >/dev/null 2>&1
exit "$RC"
