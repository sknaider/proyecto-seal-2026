#!/usr/bin/env bash
# preflight_clon.sh — ExecStartPre del gate §11 para los clones por usuario.
#
# EXISTE PORQUE LAS DOS PLANTILLAS PASAN `%i` CON FORMAS DISTINTAS:
#
#   seal-user-clone@ALICE-u103    %i = "ALICE-u103"   (agente y usuario juntos)
#   seal-ada-user-clone@103       %i = "103"          (solo el id de usuario)
#
# Un ExecStartPre no puede ramificar, y meter la logica en la unidad la vuelve
# ilegible. Peor: obligaria a mantener dos invocaciones distintas del gate y a
# acordarse de cual va en cual. Aca se normaliza una sola vez.
#
# Uso:  preflight_clon.sh <AGENTE|auto> <%i>
#
# FAIL-CLOSED: cualquier salida distinta de 0 del gate aborta el arranque.
# Incluido el 3 de "no pude medir" — un brazo sin medir no es un brazo cumplido.
#
# NEXUS, 31-jul-2026. Orden de William: "cierra todo, cablea lo que tengas".
set -euo pipefail

PY=/home/dadito/IA/seal-spark/.venv/bin/python3
GATE=/home/dadito/IA/proyecto-seal/agents/NEXUS/gate_arranque_clon.py

MODO="${1:?uso: $0 <AGENTE|auto> <instancia>}"
INSTANCIA="${2:?uso: $0 <AGENTE|auto> <instancia>}"

if [ "$MODO" = "auto" ]; then
    # %i = AGENTE-uID
    AGENTE="${INSTANCIA%%-*}"
    USUARIO="${INSTANCIA#*-}"
    if [ "$AGENTE" = "$INSTANCIA" ] || [ -z "$USUARIO" ]; then
        echo "[preflight] instancia mal formada: '$INSTANCIA' (esperado AGENTE-uID)" >&2
        exit 64
    fi
else
    # %i = solo el id de usuario; el agente viene fijo en la unidad
    AGENTE="$MODO"
    USUARIO="u${INSTANCIA#u}"
fi

DIR_ESTADO="/home/dadito/.local/share/seal/clones/${AGENTE}-${USUARIO}"
mkdir -p "$DIR_ESTADO"

GATE_ARGS=(
    --agente "$AGENTE"
    --usuario "$USUARIO"
    --preflight-docker
    --recibo "$DIR_ESTADO/preflight.json"
)
SPEC="/home/dadito/IA/proyecto-seal/config/user_clone_projections/${AGENTE}-${USUARIO}.json"
if [[ -f "$SPEC" ]]; then
    EXPECTED_DIGEST="$($PY -c '
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
if data.get("instance") != sys.argv[2]:
    raise SystemExit("projection spec instance mismatch")
digest = str(data.get("expected_source_digest") or "")
if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
    raise SystemExit("invalid expected_source_digest")
print(digest)
' "$SPEC" "${AGENTE}-${USUARIO}")"
    GATE_ARGS+=(--digest-fuente "$EXPECTED_DIGEST")
fi

# Sin `| tail` y sin nada entre el comando y el `$?`: el codigo que se lee tiene
# que ser el del gate y no el de otra cosa. Es el error que tres de nosotros
# cometimos el mismo dia (un `echo` intermedio pisa PIPESTATUS y da 0 falso).
set +e
"$PY" "$GATE" "${GATE_ARGS[@]}"
RC=$?
set -e

# CODIGO 79 PARA TODA DENEGACION, y no es cosmetica.
#
# Las dos plantillas traen `Restart=on-failure`. Medido al cablear: una instancia
# denegada acumulo NRestarts=4 en seis segundos. O sea que el gate convertia un
# RECHAZO en un CRASHLOOP, con dos consecuencias malas:
#
#   1. la denegacion se pierde entre los reintentos, en vez de quedar a la vista
#   2. si alguien arregla la causa de fondo, el clon arranca SOLO y nadie se
#      entera de que estuvo denegado
#
# Con un codigo propio + RestartPreventExitStatus=79 en el drop-in, una
# denegacion del gate detiene la unidad para siempre, mientras que un crash real
# del contenedor sigue reintentando como antes. Un codigo generico (1) no servia:
# es el que devuelve cualquier fallo del runtime, y suprimirlo habria apagado
# reintentos legitimos.
#
# El GATE conserva sus codigos 0/1/3 — el §13 de FABLE lo invoca directo y no
# debe ver este remapeo. La traduccion vive aca, que es donde systemd mira.
DENEGADO=79

case "$RC" in
    0) echo "[preflight] ${AGENTE}-${USUARIO}: precondiciones verificadas — arranca"
       exit 0 ;;
    1) echo "[preflight] ABORTO ${AGENTE}-${USUARIO}: brazo en FALLA. Recibo: $DIR_ESTADO/preflight.json" >&2
       exit "$DENEGADO" ;;
    3) echo "[preflight] ABORTO ${AGENTE}-${USUARIO}: brazos SIN MEDIR. Un brazo sin medir no es un brazo cumplido. Recibo: $DIR_ESTADO/preflight.json" >&2
       exit "$DENEGADO" ;;
    *) echo "[preflight] ABORTO ${AGENTE}-${USUARIO}: codigo inesperado $RC del gate" >&2
       # Un codigo que no reconozco NO se traduce a denegacion silenciosa: se
       # propaga tal cual para que se note que el gate hizo algo inesperado.
       exit "$RC" ;;
esac
