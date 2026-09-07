#!/usr/bin/env bash
# Delivery POR EFECTO del corte de bucles.
#
# NO es la suite otra vez: JARVIS refuto ese patron esta tarde y tenia razon.
# Esto usa el ESCRITOR REAL y demuestra las dos mitades del contrato.
#
# ARM1  siete envios IDENTICOS   -> los primeros salen, del 6to en adelante se suprimen
# ARM2  un mensaje DISTINTO      -> pasa IGUAL, aunque el otro este cortado
#
# El ARM2 es el que vale y por eso esta: sin el, un guard que suprimiera TODO
# pasaria el ARM1 igual, y habriamos construido justo lo que el 1-sep dejo mudos
# a ALICE, a JARVIS y dos veces a NEXUS.
#
# Los envios van a la propia ADA, no a un canal humano, para no ensuciar a nadie.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${SEAL_PY:-/home/dadito/IA/seal-spark/.venv/bin/python3}"
AG="${SEAL_DELIVERY_AGENT:-${SEAL_AGENT:-ADA}}"
EST="$(mktemp -d)/estado.json"
SUF="$(date +%s)-$$"
CUERPO="delivery del corte de bucles $SUF"
enviados=0; suprimidos=0; fail=0

for i in 1 2 3 4 5 6 7; do
  OUT="$(SEAL_AGENT="$AG" SEAL_LOOP_GUARD_STATE="$EST" \
        "$VENV" scripts/seal_send.py "$AG" "$AG" "$CUERPO" \
        --channel web_chat --type conversation \
        --idempotency-key "delivery-bucle-$SUF-$i" 2>/dev/null || true)"
  case "$OUT" in
    *'"suppressed": true'*|*'"suppressed":true'*) suprimidos=$((suprimidos+1)) ;;
    *'"ok":true'*|*'"ok": true'*)                 enviados=$((enviados+1)) ;;
  esac
done
echo "  ARM1  7 identicos -> enviados=$enviados  suprimidos=$suprimidos"
[ "$enviados" -eq 5 ] || { echo "  ARM1 FALLO: esperaba 5 enviados"; fail=1; }
[ "$suprimidos" -eq 2 ] || { echo "  ARM1 FALLO: esperaba 2 suprimidos"; fail=1; }

OUT2="$(SEAL_AGENT="$AG" SEAL_LOOP_GUARD_STATE="$EST" \
       "$VENV" scripts/seal_send.py "$AG" "$AG" "mensaje DISTINTO $SUF" \
       --channel web_chat --type conversation \
       --idempotency-key "delivery-bucle-distinto-$SUF" 2>/dev/null || true)"
case "$OUT2" in
  *'"suppressed"'*) echo "  ARM2 FALLO: silencio contenido NUEVO"; fail=1 ;;
  *'"ok":true'*|*'"ok": true'*) echo "  ARM2  mensaje distinto -> PASA" ;;
  *) echo "  ARM2 FALLO: no publico ni suprimio -> $OUT2"; fail=1 ;;
esac

if [ "$fail" -eq 0 ]; then echo "delivery: passed"; else echo "delivery: FAILED"; exit 1; fi
