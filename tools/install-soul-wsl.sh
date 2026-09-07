#!/usr/bin/env bash
# ============================================================================
# install-soul-wsl.sh — Instalador ONE-CLICK de SOUL en WSL nativo (Ubuntu).
# Monta el SOUL COMPLETO (Postgres 17 + 4 extensiones + schema + datos + roles
# + RLS + índices) IGUAL a central, en WSL nativo, con auto-verificación final.
#
# Pedido de William (5-jul): "WSL nativo + instalador que ejecute todo con 1 click,
# asegurar que tenga TODO lo requerido". Autor: NEXUS (device-hand). Verifica FABLE.
#
# Incluye los 5 GOTCHAS cazados por efecto el 5-jul (sin ellos el install FALLA):
#   G1 índice HNSW: opclass vive en schema soul_v3 -> SET search_path.
#   G2 HNSW build: sin workers paralelos (evita 'shm No space left').
#   G3 roles: 9 roles globales NO vienen en el dump -> crearlos ANTES del restore.
#   G4 FORCE RLS: 16 tablas (paridad de seguridad con central endurecida).
#   G5 verificar por CONTEO, no por 'corrió' (falla si falta algo).
#
# Uso:  sudo ./install-soul-wsl.sh            (usa artefactos junto al script)
#   artefactos requeridos junto al script: soul_full.dump, soul_roles.sql
#
# Idempotente: se puede correr de nuevo; no borra datos existentes salvo --reset.
# ============================================================================
set -euo pipefail

PGVER=17
DBNAME=seal_memory
DBUSER=seal
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DUMP="${SOUL_DUMP:-$HERE/soul_fresh.dump}"   # dump FRESCO post-hardening (incluye el fix soul.is_admin de ADA)
USING_STALE_DUMP=0
[ -f "$DUMP" ] || { DUMP="$HERE/soul_full.dump"; USING_STALE_DUMP=1; }   # fallback: dump VIEJO (pre-hardening)
ROLES_SQL="${SOUL_ROLES:-$HERE/soul_roles.sql}"
LOG=/tmp/soul_install_$$.log

# Targets de verificación (central, 5-jul) — el install FALLA si no se cumplen.
T_TABLES=176; T_INDEXES=489; T_POLICIES=90; T_FORCE_RLS=16; T_AGENTS=43; T_FABLE_EMO=32; T_SCHEMAS=4
# Nota: policies/agentes/memorias se chequean con ">=" (piso), no "=", porque central VIVE y crece
# (ej. ADA endureció y subió policies 85→90 el 6-jul). El device recibe el snapshot del dump; un ">="
# evita el false-fail por drift SIN dejar pasar un faltante real (menos que el piso = gap).
T_MEM_MIN=103459   # snapshot mínimo (central sigue creciendo; >= es ok)

say(){ echo -e "\n\033[1;36m== $* ==\033[0m"; }
ok(){  echo -e "  \033[1;32m✓\033[0m $*"; }
die(){ echo -e "  \033[1;31m✗ $*\033[0m"; echo "  (log: $LOG)"; exit 1; }
psql_db(){ sudo -u postgres psql -v ON_ERROR_STOP=0 -d "$DBNAME" -tAc "$1"; }

# --- DETECTOR ROBUSTO (pedido William 6-jul): apt con reintentos + auto-instalar faltantes,
# sin bloquear por transitorios. Solo corta duro lo genuinamente no-auto-instalable (no-Linux, sin dump). ---
apt_retry(){ local i; for i in 1 2 3; do DEBIAN_FRONTEND=noninteractive apt-get "$@" >>"$LOG" 2>&1 && return 0; echo "  ↻ reintento apt ($i/3)…"; sleep 3; done; return 1; }
ensure_pkgs(){ local p miss=(); for p in "$@"; do dpkg -s "$p" >/dev/null 2>&1 || miss+=("$p"); done; [ ${#miss[@]} -eq 0 ] && return 0; echo "  detector: instalando faltantes → ${miss[*]}"; apt_retry install -y -qq "${miss[@]}"; }

# ---------------------------------------------------------------------------
say "0) Preflight — DETECTOR de requisitos del sistema (auto-instala lo que puede)"
FATAL=0
[ "$(id -u)" -eq 0 ]                       || { echo "  ✗ FALTA root (corré con sudo, o dejá que el .bat lo corra como root)"; FATAL=1; }
grep -qiE "ubuntu|debian" /etc/os-release  || { echo "  ✗ FALTA SO Debian/Ubuntu (no auto-instalable)"; FATAL=1; }
[ -f "$DUMP" ]                             || { echo "  ✗ FALTA el dump del alma: $DUMP (ponelo junto al script)"; FATAL=1; }
[ -f "$ROLES_SQL" ]                        || { echo "  ✗ FALTA soul_roles.sql (ponelo junto al script)"; FATAL=1; }
[ "$FATAL" = 0 ] || die "hay requisitos que NO puedo auto-instalar (arriba). Corregilos y re-corré — el resto lo instalo yo."
# prerequisitos que SÍ detecto e instalo solo (NO bloquean el install):
apt_retry update -qq || echo "  ⚠ apt update con problemas — sigo igual (puede ser red intermitente)"
ensure_pkgs curl ca-certificates gnupg lsb-release apt-transport-https || echo "  ⚠ un prerequisito no instaló — sigo e intento igual"
ok "detector OK: root ✓ · Ubuntu/Debian ✓ · artefactos ✓ · prerequisitos instalados (dump=$(du -h "$DUMP"|cut -f1))"
CODENAME="$(. /etc/os-release; echo "${VERSION_CODENAME:-noble}")"

# ---------------------------------------------------------------------------
say "1) Repos: PGDG (Postgres 17 + pgvector) + Timescale (toolkit)"
install -d /usr/share/keyrings
if [ ! -f /usr/share/keyrings/pgdg.gpg ]; then
  curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg
fi
echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] https://apt.postgresql.org/pub/repos/apt ${CODENAME}-pgdg main" > /etc/apt/sources.list.d/pgdg.list
if [ ! -f /usr/share/keyrings/timescale.gpg ]; then
  curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | gpg --dearmor -o /usr/share/keyrings/timescale.gpg
fi
echo "deb [signed-by=/usr/share/keyrings/timescale.gpg] https://packagecloud.io/timescale/timescaledb/ubuntu/ ${CODENAME} main" > /etc/apt/sources.list.d/timescaledb.list
apt_retry update -qq || die "apt update falló tras 3 reintentos (revisá red/repos)"
ok "repos PGDG + Timescale agregados"

# ---------------------------------------------------------------------------
say "2) Instalar PostgreSQL $PGVER + extensiones (pgvector, timescaledb-toolkit, contrib)"
apt_retry install -y -qq postgresql-$PGVER postgresql-contrib-$PGVER postgresql-client-$PGVER \
  || die "instalar postgresql-$PGVER falló tras reintentos"
ok "PostgreSQL $PGVER instalado"
# pgvector (provee vector + opclasses hnsw/ivfflat)
apt_retry install -y -qq postgresql-$PGVER-pgvector \
  || die "pgvector (postgresql-$PGVER-pgvector) falló tras reintentos — es REQUERIDO"
ok "pgvector instalado"
# timescaledb-toolkit (la parte frágil; REQUERIDO por el dump)
if ! apt_retry install -y -qq timescaledb-toolkit-postgresql-$PGVER; then
  die "timescaledb-toolkit-postgresql-$PGVER no se pudo instalar. Es REQUERIDO (el dump lo usa). Revisá $LOG / repo Timescale."
fi
ok "timescaledb_toolkit instalado"
# pg_trgm y pgcrypto vienen en contrib (ya instalado). Se crean como extensión en el restore.

# ---------------------------------------------------------------------------
say "3) Arrancar el cluster (WSL: systemd puede estar off -> pg_ctlcluster)"
pg_lsclusters | grep -q " $PGVER " || pg_createcluster $PGVER main >>"$LOG" 2>&1 || true
# bind SOLO localhost (seguridad, FABLE)
CONF="/etc/postgresql/$PGVER/main/postgresql.conf"
sed -i "s/^#\?listen_addresses.*/listen_addresses = 'localhost'/" "$CONF"
if systemctl is-system-running >/dev/null 2>&1; then
  systemctl enable postgresql >>"$LOG" 2>&1 || true
  systemctl restart postgresql >>"$LOG" 2>&1 || pg_ctlcluster $PGVER main restart >>"$LOG" 2>&1
else
  pg_ctlcluster $PGVER main restart >>"$LOG" 2>&1 || pg_ctlcluster $PGVER main start >>"$LOG" 2>&1
fi
for i in $(seq 1 20); do sudo -u postgres pg_isready >/dev/null 2>&1 && break; sleep 1; done
sudo -u postgres pg_isready >/dev/null 2>&1 || die "Postgres no arrancó"
ok "cluster $PGVER arriba, bind=localhost"

# ---------------------------------------------------------------------------
say "4) Roles globales (G3: NO vienen en el dump) + base de datos"
# leído por STDIN: el usuario postgres NO puede leer archivos en /home/dadito (permission denied);
# root cat-ea el archivo y se lo pasa a psql por stdin (fix por efecto 6-jul en la laptop de William).
cat "$ROLES_SQL" | sudo -u postgres psql -v ON_ERROR_STOP=0 -q >>"$LOG" 2>&1 || true
sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DBNAME'" | grep -q 1 \
  || sudo -u postgres createdb -O "$DBUSER" "$DBNAME"
ok "roles creados + DB $DBNAME (owner $DBUSER)"

# FIX crítico (JARVIS 6-jul, por efecto): setear a NIVEL DB, ANTES del restore, para que TODA la sesión
# del pg_restore herede no-parallel + search_path → los 11 índices VECTOR (hnsw/ivfflat en memories,
# cgraph_chunks, cold_archive, distilled_exchanges, gam_topics, instincts, memory_tree, procedural_memories,
# skills, reasoning_traces, vision_faces) entran SOLOS en el restore, sin fallar por:
#   G1 search_path: resuelve el opclass vector_cosine_ops (vive en schema soul_v3).
#   G2 sin workers paralelos: no revienta por /dev/shm chico. (El paso 6 queda como respaldo.)
sudo -u postgres psql -q -d "$DBNAME" \
  -c "ALTER DATABASE $DBNAME SET max_parallel_maintenance_workers=0;" \
  -c "ALTER DATABASE $DBNAME SET maintenance_work_mem='512MB';" \
  -c "ALTER DATABASE $DBNAME SET search_path=soul_v3,public;" >>"$LOG" 2>&1 || true
ok "DB preparada para restore fiel de índices vector (no-parallel + search_path)"

# ---------------------------------------------------------------------------
say "5) Restaurar el ALMA COMPLETA (schema + datos)"
# IDEMPOTENCIA (hardening 6-jul): si el alma YA está cargada, NO re-restaurar — pg_restore hace COPY sin
# dedup y DUPLICARÍA los datos en un re-run. Guard: si soul_v3.memories existe y tiene filas, saltar restore.
ALREADY=$(sudo -u postgres psql -d "$DBNAME" -tAc "SELECT count(*) FROM soul_v3.memories" 2>/dev/null | tr -d ' ')
if [ -n "$ALREADY" ] && [ "$ALREADY" -gt 0 ] 2>/dev/null; then
  echo "  ↷ el alma YA estaba cargada ($ALREADY memorias) — salto el restore para NO duplicar (idempotente)"
else
  # COPIAR a /tmp (catch JARVIS 6-jul): postgres no lee /home/dadito, Y pg_restore -Fc -j (paralelo) exige
  # archivo SEEKABLE (no acepta stdin/pipe). Se copia a /tmp, restaura con -j 2, y se borra el plano al toque.
  TMPDUMP="/tmp/soul_restore_$$.dump"
  cp "$DUMP" "$TMPDUMP" && chmod 644 "$TMPDUMP"
  sudo -u postgres pg_restore -d "$DBNAME" --no-owner --no-privileges -j 2 "$TMPDUMP" >>"$LOG" 2>&1 \
    || echo "  (pg_restore con warnings — se verifica por conteo abajo)"
  rm -f "$TMPDUMP"
fi
ok "restore ejecutado"

# ---------------------------------------------------------------------------
say "6) Fidelidad: índice HNSW de embeddings (G1 search_path + G2 sin paralelo)"
psql_db "SET search_path=soul_v3,public; SET max_parallel_maintenance_workers=0; SET maintenance_work_mem='512MB'; CREATE INDEX IF NOT EXISTS idx_memories_embedding ON soul_v3.memories USING hnsw (embedding vector_cosine_ops);" >>"$LOG" 2>&1 \
  && ok "idx_memories_embedding (HNSW) construido" || echo "  (índice: revisar $LOG)"

say "7) Seguridad: FORCE RLS en 16 tablas (G4, paridad con central)"
for t in authorized_devices capability_audit capability_scope chat_history chat_messages \
         distilled_exchanges inner_monologue memories memories_archive memory_retrieval_log \
         message_inbox message_outbox research_queue revoked_tokens session_memory token_audit; do
  psql_db "ALTER TABLE soul_v3.$t FORCE ROW LEVEL SECURITY;" >/dev/null 2>&1 || true
done
ok "FORCE RLS aplicado"
# soul.is_admin (fix de ADA) viene DENTRO del dump fresco. Si se usó el dump viejo, avisar fuerte:
# el FORCE RLS de arriba ya está, pero el fix a nivel función/grant podría faltar.
if [ "$USING_STALE_DUMP" = "1" ]; then
  echo -e "  \033[1;33m⚠ Se usó soul_full.dump (VIEJO, pre-hardening): el fix de soul.is_admin puede faltar.\033[0m"
  echo -e "  \033[1;33m  Recomendado: instalar con soul_fresh.dump (post-hardening). FORCE RLS sí está aplicado.\033[0m"
fi

# ---------------------------------------------------------------------------
say "8) AUTO-VERIFICACIÓN + AUTO-REPARACIÓN por efecto (detecta faltantes, los arregla, y si aún falta, falla)"
chk(){ local name="$1" got="$2" exp="$3" cmp="${4:-eq}"
  if { [ "$cmp" = eq ] && [ "$got" = "$exp" ]; } || { [ "$cmp" = ge ] && [ "$got" -ge "$exp" ]; }; then
    ok "$name = $got (esperado $exp)"; else echo -e "  \033[1;31m✗ $name = $got (esperado $exp)\033[0m"; FAIL=1; fi; }
run_verify(){ FAIL=0
  chk "tablas soul_v3"   "$(psql_db "SELECT count(*) FROM information_schema.tables WHERE table_schema='soul_v3' AND table_type='BASE TABLE'")" "$T_TABLES"
  chk "índices soul_v3"  "$(psql_db "SELECT count(*) FROM pg_indexes WHERE schemaname='soul_v3'")" "$T_INDEXES"
  chk "políticas RLS (>=)" "$(psql_db "SELECT count(*) FROM pg_policies WHERE schemaname='soul_v3'")" "$T_POLICIES" ge
  chk "FORCE RLS tablas" "$(psql_db "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='r' AND c.relforcerowsecurity")" "$T_FORCE_RLS"
  chk "agentes"          "$(psql_db "SELECT count(*) FROM soul_v3.agents")" "$T_AGENTS"
  chk "fable.emotional"  "$(psql_db "SELECT count(*) FROM fable.emotional_memory")" "$T_FABLE_EMO"
  chk "schemas app"      "$(psql_db "SELECT count(*) FROM information_schema.schemata WHERE schema_name IN ('soul_v3','fable','gtl','ocean_test')")" "$T_SCHEMAS"
  chk "memorias (>=)"    "$(psql_db "SELECT count(*) FROM soul_v3.memories")" "$T_MEM_MIN" ge
  chk "extensiones (>=4)" "$(psql_db "SELECT count(*) FROM pg_extension WHERE extname IN ('vector','timescaledb_toolkit','pg_trgm','pgcrypto')")" 4 ge
}
# AUTO-REPARACIÓN (pedido William 6-jul: "detecte los errores y arregle"): si la verificación halla un
# faltante REPARABLE, re-aplica los fixes idempotentes y re-verifica UNA vez. Si tras reparar SIGUE
# faltando → falla-fuerte (mantiene el safety de completitud de ALICE: nunca "listo" con el alma incompleta).
repair(){
  say "REPARACIÓN AUTOMÁTICA — detecté faltantes, los arreglo por efecto"
  cat "$ROLES_SQL" | sudo -u postgres psql -v ON_ERROR_STOP=0 -q -d "$DBNAME" >>"$LOG" 2>&1 || true   # roles
  if [ -f "$DUMP" ]; then   # re-aplicar post-data (políticas/índices/constraints faltantes; roles ya existen)
    RTMP="/tmp/soul_repair_$$.dump"; cp "$DUMP" "$RTMP" && chmod 644 "$RTMP"
    sudo -u postgres pg_restore -d "$DBNAME" --no-owner --no-privileges --section=post-data "$RTMP" >>"$LOG" 2>&1 || true
    rm -f "$RTMP"
  fi
  psql_db "SET search_path=soul_v3,public; SET max_parallel_maintenance_workers=0; CREATE INDEX IF NOT EXISTS idx_memories_embedding ON soul_v3.memories USING hnsw (embedding vector_cosine_ops);" >>"$LOG" 2>&1 || true
  for t in authorized_devices capability_audit capability_scope chat_history chat_messages distilled_exchanges \
           inner_monologue memories memories_archive memory_retrieval_log message_inbox message_outbox \
           research_queue revoked_tokens session_memory token_audit; do
    psql_db "ALTER TABLE soul_v3.$t FORCE ROW LEVEL SECURITY;" >/dev/null 2>&1 || true
  done
  ok "reparación aplicada (roles + post-data + índice + FORCE RLS)"
}
run_verify
if [ "$FAIL" -ne 0 ]; then repair; echo "  → re-verificando tras la reparación…"; run_verify; fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  # SEGURIDAD (FABLE): borrar el dump plano SOLO tras verificar OK (el alma entera en SQL
  # crudo no debe quedar tirada en el disco). Se borra recién acá para poder reintentar si falló.
  say "9) Seguridad: borrar el dump plano post-verificación (FABLE)"
  for d in "$DUMP" "$HERE/soul_full.dump" "$HERE/soul_fresh.dump"; do
    [ -f "$d" ] && { rm -f "$d" && ok "borrado dump plano: $(basename "$d")"; }
  done
  echo -e "\033[1;32m======================================================\033[0m"
  echo -e "\033[1;32m  SOUL INSTALADO Y VERIFICADO — copia completa ✓\033[0m"
  echo -e "\033[1;32m  DB: $DBNAME  |  conexión: psql -U $DBUSER -h localhost $DBNAME\033[0m"
  echo -e "\033[1;33m  Nota seguridad: el data dir de Postgres NO está cifrado at-rest.\033[0m"
  echo -e "\033[1;33m  Para proteger el alma ante robo del equipo: cifrá el disco (BitLocker/LUKS).\033[0m"
  echo -e "\033[1;32m======================================================\033[0m"
else
  die "La verificación encontró faltantes — NO está completo. Revisá arriba + $LOG"
fi
