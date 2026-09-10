#!/usr/bin/env bash
# Efecto de entrega del carril "lanzadores-cargan-identidad".
#
# QUE COMPRUEBA, y hasta donde llega: que cada lanzador, corrido en un entorno limpio,
# exporta un SEAL_SESSION_TOKEN que el SERVIDOR resuelve al agente correcto. Usa
# `token_owner`, la misma funcion que llama `_get_caller_agent()` en mcp_server_v4.py,
# contra el mismo almacen de tokens que lee el servidor.
#
# QUE **NO** COMPRUEBA, y va dicho porque es la mitad que falta: que una sesion viva
# deje de ser "external". Eso no se puede verificar desde aca -- el header
# `Authorization` se interpola cuando el cliente MCP ABRE la conexion, asi que el efecto
# en produccion solo aparece al RELANZAR el agente. Medido por ALICE: exportar la
# variable en caliente no cambia nada.
#
# No imprime ningun secreto: solo longitudes y el nombre que el servidor resuelve.
set -uo pipefail

RAIZ="/home/dadito/IA/proyecto-seal"
cd "$RAIZ" || { echo "delivery: no puedo entrar al repo"; exit 3; }

PY="$RAIZ/.venv-quality/bin/python3"
[ -x "$PY" ] || PY="$(command -v python3)"

fallos=0
for AG in JARVIS ALICE NEXUS; do
  salida="$(env -i HOME="$HOME" PATH="$PATH" \
      ${XDG_RUNTIME_DIR:+XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"} \
      SEAL_AGENT="$AG" bash -c "source $RAIZ/seal_identity_env.sh >/dev/null 2>&1; \
        printf '%s' \"\${SEAL_SESSION_TOKEN:-}\"" )"
  if [ -z "$salida" ]; then
    echo "  $AG: NO exporta token -> el MCP lo veria como external"
    fallos=$((fallos+1))
    continue
  fi
  duenio="$(SEAL_TOKEN_SONDA="$salida" "$PY" - <<'PY'
import os, sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from seal_identity_tokens import token_owner
runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
print(token_owner([os.path.join(runtime, "seal"), "/tmp/seal_tokens"],
                  {"JARVIS", "ALICE", "NEXUS", "ADA", "FABLE", "DUM"},
                  os.environ["SEAL_TOKEN_SONDA"]) or "")
PY
)"
  if [ "$duenio" = "$AG" ]; then
    echo "  $AG: token de ${#salida} chars, el servidor lo resuelve como $duenio"
  else
    echo "  $AG: el servidor resuelve el token como '${duenio:-nadie}', no como $AG"
    fallos=$((fallos+1))
  fi
done

# Control negativo: si esto resolviera a alguien, todo lo de arriba no probaria nada.
inventado="$("$PY" - <<'PY'
import os, sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from seal_identity_tokens import token_owner
runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
print(token_owner([os.path.join(runtime, "seal"), "/tmp/seal_tokens"],
                  {"JARVIS", "ALICE", "NEXUS", "ADA", "FABLE", "DUM"}, "0" * 64) or "")
PY
)"
if [ -n "$inventado" ]; then
  echo "  CONTROL ROTO: un token inventado resuelve como '$inventado'"
  fallos=$((fallos+1))
else
  echo "  control: un token inventado no resuelve a nadie"
fi

if [ "$fallos" -eq 0 ]; then
  echo "delivery: passed (falta el paso de RELANZAR para el efecto en produccion)"
  exit 0
fi
echo "delivery: FALLA en $fallos comprobacion(es)"
exit 1
