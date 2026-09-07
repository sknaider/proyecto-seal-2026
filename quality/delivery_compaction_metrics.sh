#!/usr/bin/env bash
# Delivery POR EFECTO de compaction-metrics-v1.
#
# POR QUE EXISTE: JARVIS refuto mi primera version, y con razon. Mi `delivery` era
# la misma suite de pytest, o sea que probaba lo mismo dos veces y no demostraba
# NINGUN efecto. El deliverable no es que los tests pasen: es que una compactacion
# degradada DEJE DE SER INVISIBLE.
#
# ARM1 (sano)      el hook post con su credencial real  -> degraded=false
# ARM2 (degradado) una COPIA con la credencial muerta   -> degraded=true
#
# El ARM2 es el que vale: sin el, una metrica que escribiera `false` siempre
# pasaria el ARM1 igual y seguiriamos ciegos, que es exactamente el defecto que
# esto vino a cerrar.
#
# El arbol vivo NUNCA se muta: la credencial muerta se inyecta en una copia.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${SEAL_PY:-/home/dadito/IA/seal-spark/.venv/bin/python3}"
AG="${SEAL_DELIVERY_AGENT:-${SEAL_AGENT:-ADA}}"
TMP="$(mktemp -d)"                      # bajo /tmp: el sistema lo limpia solo
fail=0

leer_ultimo() {   # $1 = archivo de metricas ; imprime "fase degraded chars"
  "$VENV" - "$1" <<'PY'
import json, sys, pathlib
p = pathlib.Path(sys.argv[1])
if not p.exists() or not p.read_text(encoding="utf-8").strip():
    print("<sin metrica>"); raise SystemExit
d = json.loads(p.read_text(encoding="utf-8").strip().splitlines()[-1])
print(f"{d.get('fase')} degraded={d.get('degraded')} chars={d.get('reinjected_chars')}")
PY
}

# ---- ARM1: el hook real, con su credencial real
M1="$TMP/sano.jsonl"
echo '{}' | SEAL_AGENT="$AG" SEAL_COMPACTION_METRICS="$M1" \
  "$VENV" memory/post_compact_session_start_hook.py >/dev/null 2>&1 || true
R1="$(leer_ultimo "$M1")"
echo "  ARM1 sano       -> $R1"
case "$R1" in *"degraded=False"*) : ;; *) echo "  ARM1 FALLO: esperaba degraded=False"; fail=1 ;; esac

# ---- ARM2: copia con la credencial MUERTA de julio
cp memory/post_compact_session_start_hook.py "$TMP/hook_degradado.py"
sed -i 's|^DB_URL = settings.pg_dsn|DB_URL = "postgresql://seal:credencial_muerta@localhost:5433/seal_memory"|' \
  "$TMP/hook_degradado.py"
M2="$TMP/degradado.jsonl"
echo '{}' | SEAL_AGENT="$AG" SEAL_COMPACTION_METRICS="$M2" PYTHONPATH=memory \
  "$VENV" "$TMP/hook_degradado.py" >/dev/null 2>&1 || true
R2="$(leer_ultimo "$M2")"
echo "  ARM2 degradado  -> $R2"
case "$R2" in *"degraded=True"*) : ;; *) echo "  ARM2 FALLO: esperaba degraded=True"; fail=1 ;; esac

# el arbol vivo quedo intacto
if ! grep -q "^DB_URL = settings.pg_dsn" memory/post_compact_session_start_hook.py; then
  echo "  FALLO: el hook VIVO quedo modificado"; fail=1
fi

if [ "$fail" -eq 0 ]; then echo "delivery: passed"; else echo "delivery: FAILED"; exit 1; fi
