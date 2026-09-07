#!/usr/bin/env bash
# seal_snapshot_nfs.sh — foto diaria de lo que NO vive en la DB, al disco NFS.
# Creado por JARVIS el 7-sep-2026 tras el borrado del home (01:42:53): un año de trabajo sin copia.
# Solo LEE el raiz y ESCRIBE en /mnt/spark-2/backups_seal/<fecha>. Nunca borra en el raiz.
set -euo pipefail
DEST_ROOT=/mnt/spark-2/backups_seal
case "$DEST_ROOT" in /mnt/spark-2/*) : ;; *) echo "destino invalido" >&2; exit 2 ;; esac
mountpoint -q /mnt/spark-2 || { echo "[snapshot] /mnt/spark-2 no montado" >&2; exit 3; }
DIA=$(date +%F)
DEST="$DEST_ROOT/$DIA"
mkdir -p "$DEST"
# rsync sin preservar modo (NFS) y sin borrar en destino salvo dentro de la foto del dia
RS="rsync -a --no-perms --no-owner --no-group --delete-excluded"
rs(){ "$@" || { rc=$?; [ $rc -eq 23 ] || [ $rc -eq 24 ] || return $rc; }; }
rs $RS --exclude='.agent_session_token_*' --exclude='.agent_ws_token' --exclude='seal_secrets.py' --exclude='*.pem' --exclude='*.key' --exclude='mattermost/volumes' --exclude='matrix/*/data' --exclude='qdrant_storage' --exclude='*/volumes/*' --exclude='node_modules' --exclude='*.gguf' --exclude='*.safetensors' --exclude='*.bin' --exclude='.venv' --exclude='__pycache__' --exclude='minimax-m2.5' \
    /home/dadito/IA/proyecto-seal/ "$DEST/proyecto-seal/"
rs $RS --exclude=".venv" --exclude="__pycache__" /home/dadito/IA/soul-v2-lab/ "$DEST/soul-v2-lab/"
rs $RS --exclude="node_modules" /home/dadito/IA/soul-infra/ "$DEST/soul-infra/"
rs $RS /home/dadito/.config/systemd/user/ "$DEST/systemd_user/"
rs $RS /home/dadito/.claude/projects/-home-dadito-IA-proyecto-seal/memory/ "$DEST/claude_memory/"
[ -f /home/dadito/.claude/CLAUDE.md ] && cp --no-preserve=mode /home/dadito/.claude/CLAUDE.md "$DEST/CLAUDE_global.md"
[ -d /home/dadito/.codex ] && rs $RS --exclude='*.log' --exclude='auth.json' /home/dadito/.codex/ "$DEST/codex/"
# SECRETOS NUNCA al NFS (no preserva permisos): se excluyen credentials.env, *.dsn, tokens
[ -d /home/dadito/.config/seal ] && rs $RS --exclude='credentials.env*' --exclude='*.dsn' --exclude='*token*' --exclude='*secret*' /home/dadito/.config/seal/ "$DEST/config_seal/"
# volcado diario de la DB (esquema soul_v3, formato custom)
docker exec seal-memory-db pg_dump -U seal -d seal_memory -n soul_v3 -Fc > "$DEST/soul_v3_$DIA.dump" 2>>"$DEST/pg_dump.err" || echo "[snapshot] pg_dump fallo, ver $DEST/pg_dump.err" >&2
# retencion: conservar 14 fotos (solo dentro de DEST_ROOT, ruta literal por construccion)
ls -1d "$DEST_ROOT"/20* 2>/dev/null | sort | head -n -14 | while read -r old; do case "$old" in /mnt/spark-2/backups_seal/20*) rm -rf "$old" ;; esac; done
n=$(find "$DEST" -type f | wc -l)
echo "[snapshot] $DIA ok · $n archivos · $(du -sh "$DEST" | cut -f1) en $DEST"
