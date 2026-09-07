#!/usr/bin/env bash
# SIMULACRO DE RESTAURACIÓN (carril 6 de SPEC_SEAL_RESILIENTE): reconstruye la casa desde la foto del NFS en un
# directorio VACÍO y restaura el pg_dump en un contenedor Postgres DESECHABLE. No toca /home/dadito ni la DB viva.
# Uso: seal_restaurar_desde_nfs.sh <FECHA AAAA-MM-DD> <DESTINO>   (DESTINO: bajo /tmp/seal-restauracion-* y vacío)
# Criterio de la spec §8: la casa vuelve en < 1 h desde NFS + pg_dump, sin rescatar nada de la memoria de un proceso.
set -u
FECHA="${1:-}"; DEST="${2:-}"
ORIGEN_ROOT="${SEAL_SNAPSHOT_DEST:-/mnt/spark-2/backups_seal}"
[ -n "$FECHA" ] && [ -n "$DEST" ] || { echo "[restaurar] uso: $0 <FECHA> <DESTINO>" >&2; exit 2; }
ORIGEN="$ORIGEN_ROOT/$FECHA"
[ -d "$ORIGEN" ] || { echo "[restaurar] no existe la foto $ORIGEN" >&2; exit 2; }
DEST=$(realpath -m -- "$DEST")
# GUARDA-DESTRUCTIVA: el destino sólo puede ser un directorio bajo /tmp/seal-restauracion-* y debe estar vacío.
case "$DEST" in /tmp/seal-restauracion-*) ;; *) echo "[restaurar] destino invalido: $DEST (solo /tmp/seal-restauracion-*)" >&2; exit 2;; esac
mkdir -p "$DEST" || exit 2
[ -z "$(ls -A "$DEST")" ] || { echo "[restaurar] destino no vacio: $DEST" >&2; exit 2; }
T0=$(date +%s)
echo "[restaurar] $(date -Is) foto=$ORIGEN destino=$DEST"
# rsync no crea los padres del destino; rc 23/24 (parciales por permisos/vanished) se toleran, como en el snapshot
rs() { local dst="${@: -1}"; mkdir -p "$dst"; rsync -a --no-perms --no-owner --no-group --chmod=u+rwX "$@"; local rc=$?; [ $rc -eq 0 ] || [ $rc -eq 23 ] || [ $rc -eq 24 ] || return $rc; return 0; }
rs "$ORIGEN/proyecto-seal/" "$DEST/IA/proyecto-seal/" || { echo "[restaurar] rsync repo fallo" >&2; exit 3; }
rs "$ORIGEN/systemd_user/" "$DEST/.config/systemd/user/" || exit 3
rs "$ORIGEN/claude_memory/" "$DEST/.claude/projects/-home-dadito-IA-proyecto-seal/memory/" || exit 3
[ -f "$ORIGEN/CLAUDE_global.md" ] && mkdir -p "$DEST/.claude" && cp --no-preserve=mode "$ORIGEN/CLAUDE_global.md" "$DEST/.claude/CLAUDE.md"
[ -d "$ORIGEN/config_seal" ] && rs "$ORIGEN/config_seal/" "$DEST/.config/seal/"
T1=$(date +%s)
# --- verificación por efecto de los archivos críticos ---
FALTAN=0
for f in IA/proyecto-seal/CLAUDE.md IA/proyecto-seal/messages/chat_server.py IA/proyecto-seal/memory/mcp_server_v4.py IA/proyecto-seal/scripts/seal_send.py IA/proyecto-seal/tools/seal_snapshot_nfs.sh .claude/projects/-home-dadito-IA-proyecto-seal/memory/MEMORY.md .claude/CLAUDE.md; do
  [ -f "$DEST/$f" ] || { echo "[restaurar] FALTA $f" >&2; FALTAN=$((FALTAN+1)); }
done
UNIDADES=$(find "$DEST/.config/systemd/user" -maxdepth 1 -name 'seal-*.service' | wc -l)
TIMERS=$(find "$DEST/.config/systemd/user" -maxdepth 1 -name 'seal-*.timer' | wc -l)
ARCHIVOS=$(find "$DEST/IA/proyecto-seal" -type f | wc -l)
SECRETOS=$(grep -rlE 'postgres(ql)?://[A-Za-z0-9_]+:[^@{}<> $]{8,}@' "$DEST/.config" 2>/dev/null | xargs -r grep -L REDACTADO | wc -l)
# --- restauración del dump en un Postgres desechable ---
DUMP=$(ls "$ORIGEN"/seal_memory_completa_*.dump "$ORIGEN"/soul_v3_*.dump 2>/dev/null | head -1)
TABLAS=-1; MEMORIAS=-1; ESQUEMAS=-1; ORION=-1; T2=$T1
if [ -n "$DUMP" ] && command -v docker >/dev/null; then
  IMG=$(docker inspect seal-memory-db --format '{{.Config.Image}}' 2>/dev/null || echo pgvector/pgvector:pg16)
  C="seal-restauracion-$$"
  docker run -d --name "$C" -e POSTGRES_PASSWORD=restauracion -e POSTGRES_USER=seal -e POSTGRES_DB=seal_memory "$IMG" >/dev/null || { echo "[restaurar] no pude crear el contenedor" >&2; exit 3; }
  # la imagen oficial arranca, se apaga y vuelve a arrancar durante el init: esperar a que esté lista 3 veces seguidas
  OKS=0; for i in $(seq 1 120); do if docker exec "$C" pg_isready -U seal -d seal_memory >/dev/null 2>&1; then OKS=$((OKS+1)); [ $OKS -ge 3 ] && break; else OKS=0; fi; sleep 2; done
  # las extensiones viven en el esquema soul_v3 en producción (medido: vector 0.8.2 y pg_trgm 1.6 en soul_v3);
  # se crean ahí ANTES del restore para que las tablas con columnas vector no fallen con -j
  docker exec "$C" psql -U seal -d seal_memory -qc 'CREATE SCHEMA IF NOT EXISTS soul_v3; CREATE EXTENSION IF NOT EXISTS vector SCHEMA soul_v3; CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA soul_v3' >/dev/null 2>&1
  docker cp "$DUMP" "$C:/tmp/soul_v3.dump"
  docker exec "$C" pg_restore -U seal -d seal_memory --no-owner --no-privileges -j 4 /tmp/soul_v3.dump >"$DEST/pg_restore.log" 2>&1
  TABLAS=$(docker exec "$C" psql -U seal -d seal_memory -Atc "select count(*) from information_schema.tables where table_schema='soul_v3'" 2>/dev/null || echo -1)
  MEMORIAS=$(docker exec "$C" psql -U seal -d seal_memory -Atc "select count(*) from soul_v3.memories" 2>/dev/null || echo -1)
  ESQUEMAS=$(docker exec "$C" psql -U seal -d seal_memory -Atc "select count(distinct table_schema) from information_schema.tables where table_schema not in ('pg_catalog','information_schema')" 2>/dev/null || echo -1)
  ORION=$(docker exec "$C" psql -U seal -d seal_memory -Atc "select count(*) from information_schema.tables where table_schema='orion_exam'" 2>/dev/null || echo -1)
  T2=$(date +%s)
  case "$C" in seal-restauracion-*) docker rm -f "$C" >/dev/null 2>&1;; esac
fi
cat <<R
[restaurar] RESULTADO
  archivos repo restaurados   $ARCHIVOS
  unidades seal-*.service     $UNIDADES   timers $TIMERS
  criticos faltantes          $FALTAN
  secretos en config copiada  $SECRETOS   (debe ser 0)
  tablas soul_v3 restauradas  $TABLAS
  memorias restauradas        $MEMORIAS
  esquemas restaurados        $ESQUEMAS   tablas orion_exam $ORION (0 = la foto era solo soul_v3)
  tiempo archivos             $((T1-T0)) s   tiempo dump $((T2-T1)) s   total $((T2-T0)) s
R
[ "$FALTAN" -eq 0 ] && [ "$SECRETOS" -eq 0 ] && [ "$TABLAS" -gt 100 ] && [ "$MEMORIAS" -gt 1000 ] && [ $((T2-T0)) -lt 3600 ] && { echo "[restaurar] OK: la casa vuelve en $((T2-T0)) s"; exit 0; }
echo "[restaurar] FALLO: revisar los conteos" >&2; exit 4
