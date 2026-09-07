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
rs $RS --exclude='.agent_session_token_*' --exclude='.agent_ws_token' --exclude='.db_cred' --exclude='*_cred' --exclude='*.dsn' --exclude='credentials.env*' --exclude='seal_secrets.py' --exclude='*.pem' --exclude='*.key' --exclude='mattermost/volumes' --exclude='matrix/*/data' --exclude='qdrant_storage' --exclude='*/volumes/*' --exclude='node_modules' --exclude='*.gguf' --exclude='*.safetensors' --exclude='*.bin' --exclude='.venv' --exclude='__pycache__' --exclude='minimax-m2.5' \
    /home/dadito/IA/proyecto-seal/ "$DEST/proyecto-seal/"
rs $RS --exclude=".venv" --exclude="__pycache__" /home/dadito/IA/soul-v2-lab/ "$DEST/soul-v2-lab/"
rs $RS --exclude="node_modules" /home/dadito/IA/soul-infra/ "$DEST/soul-infra/"
rs $RS /home/dadito/.config/systemd/user/ "$DEST/systemd_user/"
# las unidades reconstruidas el 7-sep llevan DSN embebidos: se redacta la clave en la COPIA (hallazgo NEXUS 11:24)
find "$DEST/systemd_user" -type f -exec sed -i -E 's#(postgres(ql)?://[^:]+:)[^@]+@#\1REDACTADO@#g' {} +
# y cualquier token/clave/secreto/password en linea (hallazgo ALICE 12:15: SEAL_SIDECAR_TOKEN en seal-companion-core.service)
# tres formas (refutadores de NEXUS 12:21): valor entre comillas con espacios, valor suelto, y argumento --token/--api-key/--password
find "$DEST/systemd_user" -type f -exec sed -i -E 's#("[A-Z0-9_]*(TOKEN|KEY|SECRET|PASSWORD|PASS)=)[^"]+#\1REDACTADO#g; s#([A-Z0-9_]*(TOKEN|KEY|SECRET|PASSWORD|PASS)=)[^ "]+#\1REDACTADO#g; s#(--[a-z-]*(token|key|secret|password|pass)[= ])[^ "]+#\1REDACTADO#gI' {} +
rs $RS /home/dadito/.claude/projects/-home-dadito-IA-proyecto-seal/memory/ "$DEST/claude_memory/"
[ -f /home/dadito/.claude/CLAUDE.md ] && cp --no-preserve=mode /home/dadito/.claude/CLAUDE.md "$DEST/CLAUDE_global.md"
[ -d /home/dadito/.codex ] && rs $RS --exclude='*.log' --exclude='auth.json' /home/dadito/.codex/ "$DEST/codex/"
# SECRETOS NUNCA al NFS (no preserva permisos): se excluyen credentials.env, *.dsn, tokens
[ -d /home/dadito/.config/seal ] && rs $RS --exclude='credentials.env*' --exclude='*.dsn' --exclude='*.env' --exclude='env' --exclude='*token*' --exclude='*secret*' --exclude='*_cred' /home/dadito/.config/seal/ "$DEST/config_seal/"
# volcado diario de la DB (esquema soul_v3, formato custom)
# TODA la base (todos los esquemas): el 7-sep 13:05 ALICE encontró que orion_exam (exámenes de Henry) no tenía copia
# porque este volcado era sólo -n soul_v3. Un esquema que no está en el dump no existe tras un borrado.
# se escribe como .partial y sólo con rc=0 de pg_dump se publica el nombre definitivo (mv atómico, mismo filesystem):
# un lector nunca ve un dump a medias con nombre final (ADA, 13:09: tamaño estable o lsof no prueban que terminó)
if docker exec seal-memory-db pg_dump -U seal -d seal_memory -Fc > "$DEST/seal_memory_completa_$DIA.dump.partial" 2>>"$DEST/pg_dump.err"; then
  mv "$DEST/seal_memory_completa_$DIA.dump.partial" "$DEST/seal_memory_completa_$DIA.dump"
else
  echo "[snapshot] pg_dump fallo (rc distinto de 0), queda .partial sin publicar; ver $DEST/pg_dump.err" >&2
fi
# TODAS las demás bases del mismo servidor (hallazgo ALICE 7-sep 14:22: glt_financiero con facturas reales,
# soul_standalone, soul_v3_sandbox, valeria_memory no estaban en ninguna copia) + roles y grants SIN claves
# (pg_dump nunca incluye roles; sin esto, tras un desastre los 115 roles se reconstruyen a mano)
for db in $(docker exec seal-memory-db psql -U seal -d postgres -Atc "select datname from pg_database where not datistemplate and datname<>'seal_memory'" 2>>"$DEST/pg_dump.err"); do
  if docker exec seal-memory-db pg_dump -U seal -d "$db" -Fc > "$DEST/db_${db}_$DIA.dump.partial" 2>>"$DEST/pg_dump.err"; then
    mv "$DEST/db_${db}_$DIA.dump.partial" "$DEST/db_${db}_$DIA.dump"
  else
    echo "[snapshot] pg_dump de $db fallo, queda .partial sin publicar" >&2
  fi
done
if docker exec seal-memory-db pg_dumpall -U seal --globals-only --no-role-passwords > "$DEST/globals_roles_sin_claves_$DIA.sql.partial" 2>>"$DEST/pg_dump.err"; then
  mv "$DEST/globals_roles_sin_claves_$DIA.sql.partial" "$DEST/globals_roles_sin_claves_$DIA.sql"
else
  echo "[snapshot] pg_dumpall --globals-only fallo" >&2
fi

# NEO4J VIVO (hallazgo ALICE 7-sep 14:22): la foto copiaba /home/dadito/IA/soul-infra/neo4j, un directorio muerto desde marzo;
# los grafos vivos (96.196 nodos en soul-neo4j) están en volúmenes docker que nadie copiaba. Community no dumpea en caliente:
# 1) intento consistente: STOP DATABASE -> neo4j-admin database dump -> START DATABASE (ventana ~1 min, a las 03:30)
# 2) si falla, copia CALIENTE del volumen (tar), etiquetada como tal (consistencia no garantizada, mejor que nada)
mkdir -p "$DEST/neo4j"
for C in soul-neo4j soul-portable-neo4j-def879a7f388; do
  docker ps --format '{{.Names}}' | grep -qx "$C" || { echo "[snapshot] neo4j $C no corre; se omite" >&2; continue; }
  PW=$(docker exec "$C" sh -c 'echo "${NEO4J_AUTH#neo4j/}"' 2>/dev/null)
  if [ -n "$PW" ] && docker exec "$C" cypher-shell -d system -u neo4j -p "$PW" "STOP DATABASE neo4j WAIT" >/dev/null 2>&1; then
    docker exec "$C" sh -c 'rm -rf /tmp/neo4j-dump-seal && mkdir -p /tmp/neo4j-dump-seal && neo4j-admin database dump neo4j --to-path=/tmp/neo4j-dump-seal' >>"$DEST/neo4j/dump.log" 2>&1; RC=$?
    docker exec "$C" cypher-shell -d system -u neo4j -p "$PW" "START DATABASE neo4j WAIT" >/dev/null 2>&1 || echo "[snapshot] ALERTA: $C no volvió a START; revisar YA" >&2
    if [ $RC -eq 0 ]; then docker cp "$C:/tmp/neo4j-dump-seal/neo4j.dump" "$DEST/neo4j/${C}_neo4j_$DIA.dump.partial" && mv "$DEST/neo4j/${C}_neo4j_$DIA.dump.partial" "$DEST/neo4j/${C}_neo4j_$DIA.dump" && continue; fi
  fi
  docker run --rm --volumes-from "$C" -v "$DEST/neo4j":/out alpine:3.20 sh -c "tar czf /out/${C}_data_CALIENTE_$DIA.tgz.partial -C / data && mv /out/${C}_data_CALIENTE_$DIA.tgz.partial /out/${C}_data_CALIENTE_$DIA.tgz" >>"$DEST/neo4j/dump.log" 2>&1 || echo "[snapshot] neo4j $C: ni dump ni copia caliente" >&2
done
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
