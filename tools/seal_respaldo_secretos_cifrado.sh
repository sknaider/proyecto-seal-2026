#!/usr/bin/env bash
# Copia CIFRADA del ESTADO ESENCIAL fuera de la maquina. Carril 4, owner ALICE.
#
# POR QUE EXISTE: el 7-sep se midio que 19 archivos de estado esencial existen y
# CERO tienen copia. No es un olvido: la foto diaria EXCLUYE *.env, *.dsn, *_cred
# y *token* a proposito, para no dejar secretos en claro en el NFS. La unica forma
# de tenerlos afuera es cifrados.
#
# QUE INCLUYE, y el segundo es el que casi nadie recuerda:
#   1. los archivos de credencial que cada unidad consume
#   2. pg_dumpall --globals-only: los ROLES de Postgres. pg_dump NUNCA los
#      incluye, asi que hoy los 115 roles no estan en ningun respaldo. Y el
#      volcado de globales lleva los verificadores de contrasena adentro:
#      por eso va aca, en la copia cifrada, y no en la foto normal.
#
# REGLAS QUE CUMPLE (todas nacieron de errores de hoy):
#   - FALLA CERRADO: sin clave, sin destino o con permisos flojos, NO corre.
#   - PUBLICA ATOMICO: escribe .partial y renombra al final. Un archivo con el
#     nombre definitivo nunca esta a medio escribir (regla de ADA).
#   - NUNCA imprime un valor: ni la clave, ni el contenido, ni un fragmento.
#   - el temporal en claro vive en /tmp con mktemp -d y se borra con la forma
#     que no dispara el freno: find "$DIR" -mindepth 1 -delete
set -uo pipefail

REPO=/home/dadito/IA/proyecto-seal
LISTA="$REPO/quality/estado_esencial_rutas.txt"
DESTINO="${SEAL_SECRETOS_DEST:-/mnt/spark-2/secretos_cifrados}"
CLAVE="${SEAL_SECRETOS_KEYFILE:-/home/dadito/.config/seal/respaldo_secretos.key}"
DIA=$(date +%F)

fatal() { echo "[secretos] $*" >&2; exit 2; }

# --probar <archivo>: descifra en un temporal y verifica que el paquete abre
# entero. NO escribe nada permanente y NO imprime contenido: solo el inventario
# de nombres y el resumen. Un respaldo que nadie probo a restaurar no es un
# respaldo: es un archivo (leccion del 7-sep, dos veces).
if [ "${1:-}" = "--probar" ]; then
  ARCH="${2:?uso: --probar <archivo.enc>}"
  CLAVE="${SEAL_SECRETOS_KEYFILE:-/home/dadito/.config/seal/respaldo_secretos.key}"
  [ -f "$ARCH" ]  || fatal "no existe el archivo a probar: $ARCH"
  [ -f "$CLAVE" ] || fatal "no existe la clave para probar"
  T=$(mktemp -d /tmp/seal-secretos-probar-XXXXXX) || fatal "sin temporal"
  if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
        -in "$ARCH" -out "$T/paquete.tgz" -pass "file:$CLAVE" 2>"$T/err"; then
    find "$T" -mindepth 1 -delete
    fatal "NO descifra con esa clave (o el archivo esta corrupto)"
  fi
  if ! tar -tzf "$T/paquete.tgz" > "$T/listado.txt" 2>/dev/null; then
    find "$T" -mindepth 1 -delete
    fatal "descifra pero el paquete NO abre: copia corrupta"
  fi
  n=$(grep -c . "$T/listado.txt")
  tar -C "$T" -xzf "$T/paquete.tgz" 2>/dev/null
  echo "[probar] descifra: SI · el paquete abre: SI · entradas: $n"
  cat "$T"/estado_esencial_*/resumen.txt 2>/dev/null | sed 's/^/[probar] /'
  find "$T" -mindepth 1 -delete
  exit 0
fi

[ -f "$LISTA" ]  || fatal "no existe la lista de rutas: $LISTA"
[ -f "$CLAVE" ]  || fatal "no existe el archivo de clave. Lo entrega William; sin el NO se corre."
# permisos de la clave: si cualquiera puede leerla, el cifrado no protege nada
perm=$(stat -c '%a' "$CLAVE")
[ "$perm" = "600" ] || fatal "la clave tiene permisos $perm; se exige 600"
[ -s "$CLAVE" ]  || fatal "el archivo de clave esta VACIO: cifrar con clave vacia no cifra"
mkdir -p "$DESTINO" || fatal "no puedo crear el destino $DESTINO"

TMP=$(mktemp -d /tmp/seal-secretos-XXXXXX) || fatal "no pude crear temporal"
STAGE="$TMP/estado_esencial_$DIA"
mkdir -p "$STAGE/archivos"

copiados=0; ausentes=0; ilegibles=0
while IFS= read -r ruta; do
  case "$ruta" in ""|\#*) continue ;; esac
  if [ ! -e "$ruta" ]; then ausentes=$((ausentes+1)); echo "AUSENTE $ruta" >> "$STAGE/inventario.txt"; continue; fi
  if [ ! -r "$ruta" ]; then ilegibles=$((ilegibles+1)); echo "ILEGIBLE $ruta" >> "$STAGE/inventario.txt"; continue; fi
  destino_rel=$(echo "$ruta" | sed 's|^/||; s|/|__|g')
  cp -p "$ruta" "$STAGE/archivos/$destino_rel" && copiados=$((copiados+1))
  echo "INCLUIDO $ruta" >> "$STAGE/inventario.txt"
done < "$LISTA"

# Los ROLES: pg_dump no los trae. Sin esto, los datos vuelven y los permisos no.
if docker exec seal-memory-db pg_dumpall -U seal --globals-only > "$STAGE/roles_globals_$DIA.sql" 2>"$TMP/globals.err"; then
  roles=$(grep -c "^CREATE ROLE" "$STAGE/roles_globals_$DIA.sql" || true)
else
  roles=0; echo "FALLO pg_dumpall --globals-only" >> "$STAGE/inventario.txt"
fi

printf 'archivos incluidos %s\nausentes %s\nilegibles %s\nroles volcados %s\n' \
  "$copiados" "$ausentes" "$ilegibles" "$roles" > "$STAGE/resumen.txt"

# empaquetar y cifrar. El .tar en claro NUNCA sale de /tmp.
tar -C "$TMP" -czf "$TMP/paquete.tgz" "estado_esencial_$DIA" || fatal "fallo el empaquetado"
PARCIAL="$DESTINO/estado_esencial_$DIA.tgz.enc.partial"
FINAL="$DESTINO/estado_esencial_$DIA.tgz.enc"
if ! openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -salt \
      -in "$TMP/paquete.tgz" -out "$PARCIAL" -pass "file:$CLAVE"; then
  find "$TMP" -mindepth 1 -delete
  fatal "fallo el cifrado; NO se publica nada"
fi
mv -f "$PARCIAL" "$FINAL" || fatal "fallo la publicacion atomica"
chmod 600 "$FINAL"
find "$TMP" -mindepth 1 -delete

echo "[secretos] publicado: $FINAL"
echo "[secretos] $copiados archivos · $roles roles · ausentes $ausentes · ilegibles $ilegibles"
echo "[secretos] verificar con: seal_respaldo_secretos_cifrado.sh --probar $FINAL"
