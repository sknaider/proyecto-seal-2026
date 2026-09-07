#!/usr/bin/env bash
# Delivery por EFECTO del aviso de unidad caída (ADA, 5-sep-2026).
#
# Qué prueba, y por qué no alcanza la suite: pytest verifica que el mensaje se ARMA bien
# con un `subprocess.run` falso. Lo que hay que demostrar acá es el cable entero:
# systemd detecta el fallo -> dispara `OnFailure=` -> el notificador corre -> el mensaje
# APARECE en el chat. Ninguna de esas cuatro flechas la toca un test unitario.
#
# ARM2 ES OBLIGATORIO. Sin él, un notificador que avisara SIEMPRE --incluso de unidades
# sanas-- pasaría el ARM1 y el delivery diría "verificado" sobre algo inservible: un
# aviso que suena siempre no distingue nada.
#
# DIFERENCIA DECLARADA CON PRODUCCIÓN: el simulacro usa su propia unidad OnFailure, que
# fija `SEAL_UNIT_FAILED_TO=ADA` para no mandar una alerta falsa al canal de los cinco.
# El mecanismo ejercitado es el mismo (OnFailure -> notificador -> chat); lo único que
# cambia es el destinatario. Un simulacro que grita como un incendio real entrena a
# ignorar los dos.
set -euo pipefail

REPO=/home/dadito/IA/proyecto-seal
UNIDADES=/home/dadito/.config/systemd/user
# Los nombres llevan el PID de ESTA corrida. Con nombres fijos, dos ensayos simultaneos
# comparten unidad y el `trap` de uno le borra el archivo al otro a mitad de camino --
# me paso literalmente: mis dos cuerpos corrieron este script a la vez y el ARM2 murio
# con "no existe el archivo". Un banco de pruebas con recursos globales no es aislado.
DRILL="seal-drill-unit-failed-$$.service"
AVISADOR="seal-drill-avisador-$$.service"
MARCA="ensayo_$(date -u +%s)_$$"

limpiar() {
  systemctl --user reset-failed "$DRILL" >/dev/null 2>&1 || true
  # `find` con directorio LITERAL y patron acotado al PID de esta corrida: no toca las
  # unidades de otro ensayo en curso ni acepta una ruta que venga de una variable suelta.
  find /home/dadito/.config/systemd/user -maxdepth 1 -name "seal-drill-*-$$.service" -delete 2>/dev/null || true
  systemctl --user daemon-reload >/dev/null 2>&1 || true
}
trap limpiar EXIT

cat > "$UNIDADES/$AVISADOR" <<EOF
[Unit]
Description=SEAL — avisador del ENSAYO (no es la unidad de produccion)
[Service]
Type=oneshot
WorkingDirectory=$REPO
Environment=SEAL_UNIT_FAILED_TO=ADA
ExecStart=/home/dadito/IA/seal-spark/.venv/bin/python3 $REPO/tools/seal_unit_failed_notify.py $DRILL-$MARCA
EOF

echo "== ARM1: una unidad que FALLA dispara el aviso =="
cat > "$UNIDADES/$DRILL" <<EOF
[Unit]
Description=SEAL — unidad de ENSAYO que falla a proposito
OnFailure=$AVISADOR
[Service]
Type=oneshot
ExecStart=/bin/false
EOF
systemctl --user daemon-reload
systemctl --user start "$DRILL" >/dev/null 2>&1 || true
sleep 12
if grep -q "$MARCA" "$REPO/messages/ada_messages.jsonl" 2>/dev/null; then
  echo "ARM1 OK: el aviso con la marca $MARCA llego al chat"
else
  echo "ARM1 FALLO: la unidad cayo y el aviso NO aparecio"
  exit 1
fi

echo
echo "== ARM2 (control no vacuo): una unidad que TERMINA BIEN no avisa =="
MARCA2="control_$(date -u +%s)"
sed -i "s|$DRILL-$MARCA|$DRILL-$MARCA2|" "$UNIDADES/$AVISADOR"
cat > "$UNIDADES/$DRILL" <<EOF
[Unit]
Description=SEAL — unidad de ENSAYO que termina bien
OnFailure=$AVISADOR
[Service]
Type=oneshot
ExecStart=/bin/true
EOF
systemctl --user daemon-reload
systemctl --user start "$DRILL" >/dev/null 2>&1 || true
sleep 8
if grep -q "$MARCA2" "$REPO/messages/ada_messages.jsonl" 2>/dev/null; then
  echo "ARM2 FALLO: aviso sobre una unidad SANA -- el mecanismo no discrimina"
  exit 1
fi
echo "ARM2 OK: sin aviso, como corresponde"

echo
echo "== ARM3: la MISMA unidad cae DOS veces -> DOS avisos =="
# POR QUE EXISTE ESTE BRAZO (JARVIS, al revisar): la clave de idempotencia era fija por
# unidad, el chat deduplica por clave, y la SEGUNDA caida de la misma unidad quedaba
# muda -- el mismo defecto que esta pieza vino a cerrar. Los ARM1/ARM2 no podian verlo
# porque usan una marca UNICA por corrida: cada ensayo estrenaba clave.
# Un banco de pruebas que nunca repite no descubre un defecto que solo aparece al repetir.
# Por eso acá el avisador manda el nombre REAL de la unidad: asi el InvocationID es de
# verdad y cambia entre arranques, que es lo que se esta probando.
cat > "$UNIDADES/$AVISADOR" <<EOF
[Unit]
Description=SEAL — avisador del ENSAYO (no es la unidad de produccion)
[Service]
Type=oneshot
WorkingDirectory=$REPO
Environment=SEAL_UNIT_FAILED_TO=ADA
ExecStart=/home/dadito/IA/seal-spark/.venv/bin/python3 $REPO/tools/seal_unit_failed_notify.py $DRILL
EOF
cat > "$UNIDADES/$DRILL" <<EOF
[Unit]
Description=SEAL — unidad de ENSAYO que falla a proposito
OnFailure=$AVISADOR
[Service]
Type=oneshot
ExecStart=/bin/false
EOF
systemctl --user daemon-reload
# Se cuenta por ID DE MENSAJE sobre las lineas AGREGADAS despues de esta marca, no por
# `grep -c` del nombre: la primera version contaba LINEAS que contenian el nombre y dio 4
# para 2 caidas -- un numero que no supe explicar. Un umbral `>= 2` con lineas duplicadas
# habria pasado con UN SOLO aviso, que es justo el defecto que este brazo debe cazar.
# Se exige EXACTAMENTE 2: ni una menos (la segunda muda) ni mas (avisos que no son de acá).
LINEAS_ANTES=$(wc -l < "$REPO/messages/ada_messages.jsonl")
systemctl --user start "$DRILL" >/dev/null 2>&1 || true
sleep 10
systemctl --user reset-failed "$DRILL" >/dev/null 2>&1 || true
systemctl --user start "$DRILL" >/dev/null 2>&1 || true
sleep 12
NUEVOS=$(tail -n +"$((LINEAS_ANTES + 1))" "$REPO/messages/ada_messages.jsonl" \
  | /home/dadito/IA/seal-spark/.venv/bin/python3 -c '
import json, sys
vistos = set()
for l in sys.stdin:
    try: d = json.loads(l)
    except Exception: continue
    m = str(d.get("message") or d.get("content") or "")
    if "Unidad systemd caída" in m and sys.argv[1] in m:
        vistos.add(d.get("id"))
print(len(vistos))' "$DRILL")
if [ "$NUEVOS" -eq 2 ]; then
  echo "ARM3 OK: dos caidas de la misma unidad produjeron 2 avisos DISTINTOS"
else
  echo "ARM3 FALLO: dos caidas produjeron $NUEVOS aviso(s) distintos; se esperaban 2"
  exit 1
fi

echo
echo "DELIVERY OK: 3/3 brazos"
