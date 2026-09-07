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

# El repo se deriva de la UBICACION DEL PROPIO SCRIPT, no se cablea.
# Medido el 7-sep 15:41 en la arena: con la ruta fija, el script muere en el
# primer chequeo -"no existe la lista"- ANTES de llegar a las guardas de clave,
# asi que 4 brazos negativos "fallaban por el entorno" cuando en realidad el
# sujeto no era reubicable. Un script de RECUPERACION que solo funciona desde
# su ruta original es inutil el dia que hay que recuperarlo en otro lado.
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAVE_DESIGNADA=/home/dadito/.config/seal/respaldo_secretos.key
LISTA_REAL="$REPO/quality/estado_esencial_rutas.txt"
LISTA="${SEAL_SECRETOS_LISTA:-$LISTA_REAL}"
DESTINO="${SEAL_SECRETOS_DEST:-/mnt/spark-2/secretos_cifrados}"
CLAVE="${SEAL_SECRETOS_KEYFILE:-$CLAVE_DESIGNADA}"
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

# FRENO DE MECANISMO (NEXUS, 7-sep 14:37). El 7-sep a las 14:23 ALICE corrio
# este script con una clave improvisada y la LISTA REAL: empaqueto credenciales
# reales y los verificadores de 100 roles, y el paquete quedo junto a su clave
# en /tmp. Nada en el codigo lo impidio: el unico freno era acordarse.
#
# "Una regla que deba cumplirse aunque el agente se equivoque va en una capa de
# MECANISMO, no en un archivo." Esta es esa capa:
#
#   clave designada  -> puede empaquetar lo REAL
#   otra clave       -> SOLO con una lista distinta de la real, y SIN roles
#
# Asi, una prueba no puede tocar material real ni queriendo, y la corrida de
# produccion sigue igual de simple.
ENSAYO=0
if [ "$CLAVE" != "$CLAVE_DESIGNADA" ]; then
  ENSAYO=1
  # Se mira el CONTENIDO de la lista, no su RUTA. NEXUS evadio la version
  # anterior con un solo `cp`: una copia de la lista real esta en otra ruta y
  # lleva adentro los mismos 20 secretos. Comparar rutas es comparar el nombre;
  # lo que decide es lo que la lista APUNTA.
  # Se resuelve CADA ruta antes de mirarla: un enlace BAJO /tmp que apunta
  # afuera pasaba el filtro de texto y `cp -p` seguia el enlace, asi que el
  # contenido real viajaba igual (cuarta evasion de NEXUS, 7-sep 14:42).
  # El texto de la ruta no es la ruta: lo que importa es a donde RESUELVE.
  fuera=$(while IFS= read -r _r; do
            case "$_r" in ""|\#*) continue ;; esac
            _real=$(readlink -f -- "$_r" 2>/dev/null || printf '%s' "$_r")
            # ENLACE DURO: no tiene destino, ES el archivo, asi que readlink no
            # lo delata. Se mira st_nlink: un archivo con mas de un nombre puede
            # ser el mismo inodo que un secreto real bajo otra ruta.
            # Condicion de FABLE (7-sep 15:34): "una condicion de una linea que
            # cierra la clase entera". NEXUS la habia declarado como residual.
            _nl=$(stat -c '%h' -- "$_real" 2>/dev/null || echo 1)
            if [ "${_nl:-1}" -gt 1 ]; then
              printf '%s -> enlace DURO (%s nombres): puede ser un secreto real\n' "$_r" "$_nl"
              continue
            fi
            # cada entrada dice SU motivo: un negativo tiene que poder afirmar
            # la guarda que verifica, no un rechazo cualquiera (ADA, 15:48).
            case "$_real" in
              /tmp/*) : ;;
              *) printf '%s -> FUERA de /tmp (%s)\n' "$_r" "$_real" ;;
            esac
          done < "$LISTA" | head -3)
  if [ -n "$fuera" ]; then
    # $fuera va ENTRE COMILLAS: sin ellas bash parte por palabras y el motivo
    # sale despedazado en varias lineas -medido 15:49-. Un mensaje de guarda
    # que no se entiende obliga a leer el codigo para saber por que freno.
    fatal "clave NO designada y la lista tiene entradas NO PERMITIDAS:
$(printf '        %s\n' "$fuera")
        Con una clave improvisada solo se empaquetan rutas señuelo bajo /tmp.
        Copiar la lista real a otra ruta NO la vuelve señuelo (evasion medida
        por NEXUS el 7-sep 14:40)."
  fi
fi
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
if [ "$ENSAYO" = 1 ]; then
  # En ensayo NUNCA se vuelcan los roles reales: sus verificadores son secretos
  # y no tienen por que existir en un paquete cifrado con clave improvisada.
  roles=0; echo "OMITIDO pg_dumpall (modo ensayo: clave no designada)" >> "$STAGE/inventario.txt"
elif docker exec seal-memory-db pg_dumpall -U seal --globals-only > "$STAGE/roles_globals_$DIA.sql" 2>"$TMP/globals.err"; then
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
