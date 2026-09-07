#!/usr/bin/env bash
# Corre tools/arena_remutar_revisor.py DENTRO de la arena aprobada por FABLE (7-sep 14:48), igual que seal_recorrido_arnes.sh:
# copia del índice por git archive en /tmp/seal-arena-*, contenedor sin privilegios (uid 65534), venv montado SOLO LECTURA.
# Uso: tools/arena_remutar_run.sh quality/mutantes/<caso>.spec.json quality/mutation-<caso>.v2.json
set -eu
SPEC="$1"; SALIDA="$2"; REPO=/home/dadito/IA/proyecto-seal
cd "$REPO"
ARENA=$(mktemp -d /tmp/seal-arena-remut-XXXXXX); chmod 755 "$ARENA"
git archive "$(git write-tree)" | tar -x -C "$ARENA"
# el driver y el spec pueden no estar en el índice todavía: se copian explícitamente (solo lectura para el uid 65534)
install -m 644 tools/arena_remutar_revisor.py "$ARENA/tools/arena_remutar_revisor.py"
install -m 644 tools/seal_mutacion_segura.py "$ARENA/tools/seal_mutacion_segura.py"
mkdir -p "$ARENA/quality/mutantes"; install -m 644 "$SPEC" "$ARENA/quality/mutantes/spec.json"
# memory/config.py exige pg_password (sin default desde el 7-sep 12:54) y lo lee de ./.env, que NO está en git ni debe estar:
# la arena recibe un .env SEÑUELO (no es una credencial: la DB no es alcanzable desde la arena) para que los imports no revienten.
printf 'PG_PASSWORD=arena-senuelo-sin-credencial-real\nPG_HOST=127.0.0.1\nPG_PORT=1\n' > "$ARENA/.env"
find "$ARENA" -type d -exec chmod 777 {} + 2>/dev/null; find "$ARENA" -type f -exec chmod 666 {} + 2>/dev/null   # el uid 65534 debe poder escribir el sujeto mutado y la salida
# HOME señuelo de SOLO LECTURA: messages/chat_auth.py exige $HOME/.seal_chat_jwt_secret al importar. Es un secreto FICTICIO
# (64 caracteres fijos, no el real) y el directorio queda 555 para que la guarda del arnés no vea un HOME escribible.
mkdir -p "$ARENA/.home"; printf '%s' "arena-senuelo-jwt-no-es-el-secreto-real-0123456789abcdef0123456789abcdef" > "$ARENA/.home/.seal_chat_jwt_secret"
chmod 444 "$ARENA/.home/.seal_chat_jwt_secret"; chmod 555 "$ARENA/.home"
docker run --rm -i --user 65534:65534 -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/venv/lib/python3.12/site-packages -e HOME=/trabajo/.home \
  -v "$ARENA":/trabajo -v /home/dadito/IA/seal-spark/.venv:/venv:ro -w /trabajo python:3.12-slim \
  python3 tools/arena_remutar_revisor.py quality/mutantes/spec.json quality/mutantes/salida.json
RC=$?
cp "$ARENA/quality/mutantes/salida.json" "$SALIDA"
echo "[arena] salida copiada a $SALIDA (arena $ARENA queda para inspección; /tmp la limpia)"
exit $RC
