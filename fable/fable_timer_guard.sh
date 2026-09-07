#!/usr/bin/env bash
# fable_timer_guard.sh — mis timers no tienen quien los vigile (2-sep-2026:
# seal-fable-checkpoint.timer estuvo Stopped 42 h y bootee con memoria rancia;
# fable-nerves.timer disparaba un servicio roto 2 dias). El guard de ADA mira
# unidades de otros. Este mira las mias.
#
# v2 (2-sep, tarde) — dos defectos MIOS que arregla, ambos cazados por hallazgos ajenos:
#   1. DENOMINADOR: v1 traia una lista HARDCODEADA de 4 timers y yo tengo 7.
#      Los 3 invisibles incluian al guard MISMO. Ahora el denominador se DESCUBRE
#      por patron (fable-*, seal-fable-*), asi que un timer nuevo entra solo.
#   2. is-active MIENTE (hallazgo de ALICE, 2-sep): un timer con anclas solo
#      RELATIVAS (OnBootSec/OnUnitActiveSec) queda 'active' con NextElapse=infinity
#      tras un reboot -> vigilante MUERTO que se reporta sano. v1 solo relanzaba
#      si !=active, asi que ese caso le pasaba por debajo. Ahora la salud se mide
#      por la PROPIEDAD que discrimina: 'tiene proximo disparo?'.
#      sano  <=> NextElapseUSecRealtime no vacio  O  Monotonic fuera de {0, infinity}
# Salida: una linea JSON por timer (journal) + estado en $STATE.
set -u
STATE="${FABLE_TIMER_GUARD_STATE:-$HOME/.local/state/seal/fable_timer_guard.json}"
PATTERNS=(${FABLE_TIMER_GUARD_PATTERNS:-'fable-*.timer' 'seal-fable-*.timer'})
if [ -n "${FABLE_TIMER_GUARD_TIMERS:-}" ]; then
  TIMERS=(${FABLE_TIMER_GUARD_TIMERS})
else
  mapfile -t TIMERS < <(systemctl --user list-unit-files "${PATTERNS[@]}" --no-legend 2>/dev/null | awk '{print $1}' | sort -u)
fi
mkdir -p "$(dirname "$STATE")"

# Devuelve "armed" si el timer tiene un proximo disparo real en alguno de los dos relojes.
next_fire() {
  local rt mono
  rt=$(systemctl --user show "$1" -p NextElapseUSecRealtime --value 2>/dev/null)
  mono=$(systemctl --user show "$1" -p NextElapseUSecMonotonic --value 2>/dev/null)
  case "$rt" in ""|"n/a"|"0") ;; *) echo armed; return;; esac
  case "$mono" in ""|"n/a"|"0"|"infinity") echo dead;; *) echo armed;; esac
}

rc=0; findings=0; out="["
for t in "${TIMERS[@]}"; do
  en=$(systemctl --user is-enabled "$t" 2>/dev/null | head -n1 || echo unknown); [ -z "$en" ] && en=unknown
  act=$(systemctl --user is-active "$t" 2>/dev/null | head -n1); [ -z "$act" ] && act=inactive
  svc="${t%.timer}.service"
  res=$(systemctl --user show "$svc" -p Result --value 2>/dev/null | head -n1)
  fire=$(next_fire "$t")
  action="none"
  # v3 (ALICE, 2-sep): el discriminador NO es la forma (anclas) ni el estado (active),
  # es si la unidad DISPARO alguna vez. LastTrigger vacio + sin proximo disparo = nunca
  # corrio = defecto REAL. LastTrigger puesto + sin proximo + no recurrente = cumplida.
  # Lo encodeo porque su barrido lo separo por efecto, no porque me suene bien.
  recurrente=no
  systemctl --user cat "$t" 2>/dev/null | grep -qE 'OnUnitActiveSec=|OnCalendar=.*[*/]' && recurrente=si
  # v4 (adjudicado ALICE+NEXUS+FABLE, 2-sep, 124 timers). UN campo, DOS preguntas
  # — confundirlas nos costo 5 criterios en 40 min:
  #   "puedo LEER el campo?"        -> is-active   (inactive => VACIO, 26/26,
  #                                    tenga sello o no: vacio NO significa "nunca")
  #   "el valor CRUZO el reboot?"   -> Persistent  (29/29 de los que cruzaron son
  #                                    yes; 0 de los `no` cruzaron)
  # DOMINIO, que es la parte que yo publique de menos y por poco se encodea mal:
  # esto vale para unidades ACTIVAS. En una detenida el campo no dice nada y la
  # unica fuente de historia es el sello en disco o el journal.
  # Por eso este guard NO afirma historia y NO acciona por ella: solo actua donde
  # reiniciar puede ayudar (recurrente activo sin proximo disparo).
  lt=$(systemctl --user show "$t" -p LastTriggerUSec --value 2>/dev/null | head -n1)
  pers=$(systemctl --user show "$t" -p Persistent --value 2>/dev/null | head -n1)
  if [ -n "$lt" ]; then hist=si
  elif [ "$pers" = "yes" ]; then hist=NUNCA
  else hist=desconocido_manager_nuevo; fi
  if [ "$en" = "enabled" ] && [ "$act" != "active" ]; then
    if systemctl --user start "$t" 2>/dev/null; then action="restarted"
    elif [ "$recurrente" = "no" ]; then action="single_shot_sin_fecha_futura"
    else action="restart_failed"; rc=2; fi
  elif [ "$en" = "enabled" ] && [ "$act" = "active" ] && [ "$fire" = "dead" ] && [ "$recurrente" = "no" ]; then
    action="single_shot_sin_fecha_futura"   # sin fecha futura: reiniciar NO puede ayudar
  elif [ "$en" = "enabled" ] && [ "$act" = "active" ] && [ "$fire" = "dead" ]; then
    # activo pero sin proximo disparo: re-anclar. Es el caso que v1 no veia.
    if systemctl --user restart "$t" 2>/dev/null; then action="rearmed_no_next_elapse"; else action="rearm_failed"; rc=2; fi
  fi
  act=$(systemctl --user is-active "$t" 2>/dev/null | head -n1); [ -z "$act" ] && act=inactive
  fire=$(next_fire "$t")
  # rojo SOLO donde reiniciar puede arreglarlo: recurrente sin proximo disparo.
  # Un one-shot sin fecha futura no es un defecto accionable: no lo pinto de rojo.
  [ "$en" = "enabled" ] && [ "$act" = "active" ] && [ "$fire" = "dead" ] && [ "$recurrente" = "si" ] && findings=$((findings+1))
  [ "$res" != "success" ] && [ -n "$res" ] && findings=$((findings+1))
  line=$(printf '{"timer":"%s","enabled":"%s","active":"%s","next_fire":"%s","ever_fired":"%s","last_result":"%s","action":"%s"}' \
    "$t" "$en" "$act" "$fire" "$hist" "$res" "$action")
  echo "$line"; out="$out$line,"
done
printf '{"ts":"%s","rc":%d,"findings":%d,"watched":%d,"timers":%s]}\n' "$(date -u +%FT%TZ)" "$rc" "$findings" "${#TIMERS[@]}" "${out%,}" > "$STATE"
exit $rc
