#!/usr/bin/env bash
# seal_snapshot_nfs.sh — foto diaria de lo que NO vive en la DB, al disco NFS.
# Creado por JARVIS el 7-sep-2026 tras el borrado del home (01:42:53): un año de trabajo sin copia.
# Solo LEE el raiz y ESCRIBE en /mnt/spark-2/backups_seal/<fecha>. Nunca borra en el raiz.
set -euo pipefail
DEST_ROOT="${SEAL_SNAPSHOT_DEST:-/mnt/spark-2/backups_seal}"
# GUARDA-DESTRUCTIVA: el destino solo puede vivir bajo /mnt/spark-2 (la retencion borra ahi adentro).
# Se compara la ruta RESUELTA (realpath -m): "case" compara texto y /mnt/spark-2/../home la evadiria (hallazgo NEXUS 7-sep).
DEST_ROOT=$(realpath -m -- "$DEST_ROOT")
case "$DEST_ROOT" in /mnt/spark-2/*) : ;; *) echo "[snapshot] destino invalido: $DEST_ROOT" >&2; exit 2 ;; esac
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
cat > "$DEST/NO_RESPALDADO_Y_COMO_SE_REPONE.md" <<'EOM'
# Lo que esta foto NO contiene, a proposito, y de donde se repone (sin valores)
- ~/.config/seal/credentials.env (DSN del rol seal, DSN por servicio): se repone desde la copia CIFRADA (carril 3 de la spec, clave de William) o, en emergencia, desde /proc/<pid>/environ de los daemons vivos y la memoria del MCP (ver agents/JARVIS/incidente_borrado_home_20260907.md).
- ~/.config/seal/mcp_agents/*.dsn: se reacuñan con el procedimiento de ALICE del 7-sep (roles mcp_runtime_* en la DB).
- messages/.agent_session_token_* y .agent_ws_token: se reacuñan (chat_sessions en la DB; el WS token lo regenera seal-chat al reiniciar).
- ~/.codex/auth.json y ~/.claude/.credentials.json: login interactivo de William.
- memory/seal_secrets.py: es codigo, esta en el repo (no contiene valores).
- modelos *.gguf/*.safetensors y ~/.cache/huggingface: se vuelven a bajar; la lista canonica vive en IA/modelos/llm.
Sin la copia cifrada, la reposicion de credentials.env depende de un humano: es el hallazgo 2 de NEXUS (7-sep) y el carril 3 de la spec.
EOM
n=$(find "$DEST" -type f | wc -l)
echo "[snapshot] $DIA ok · $n archivos · $(du -sh "$DEST" | cut -f1) en $DEST"
