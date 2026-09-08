#!/bin/bash
# SEAL Soul — Session Capture Hook for Claude Code
# Runs at end of conversation via Stop hook.
# Reads event JSON from stdin, determines agent from cwd, and captures session.
#
# Only captures if transcript has 20+ messages (skip trivial sessions).

set -euo pipefail

VENV_PYTHON="/home/dadito/IA/seal-spark/.venv/bin/python3"
CAPTURE_SCRIPT="/home/dadito/IA/proyecto-seal/memory/session_capture.py"
MIN_LINES=40  # ~20 messages = ~40 lines minimum in transcript

# Read event data from stdin
EVENT=$(cat)

# Determine agent from cwd
CWD=$(echo "$EVENT" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('cwd',''))" 2>/dev/null || echo "")

if [[ "$CWD" == *"proyecto-seal/memory"* ]]; then
    AGENT="JARVIS"
    TDIR="$HOME/.claude/projects/-home-dadito-IA-proyecto-seal-memory"
elif [[ "$CWD" == *"proyecto-seal"* ]]; then
    AGENT="ADA"
    TDIR="$HOME/.claude/projects/-home-dadito-IA-proyecto-seal"
elif [[ "$CWD" == *"/IA"* ]]; then
    AGENT="JARVIS"
    TDIR="$HOME/.claude/projects/-home-dadito-IA"
else
    AGENT="UNKNOWN"
    exit 0  # Don't capture for unknown agents
fi

# ── GUARDA DE CONCURRENCIA Y CADENCIA (NEXUS, 8-sep-2026) ────────────────────
# Tres emergencias termicas hoy (SoC 90-93 C) por el mismo ciclo: cada turno de
# cada agente dispara este hook, que lee el transcript ENTERO y llama al modelo
# para clasificar emocion. Cuanto mas hablamos, mas calor. Se midieron 3
# capturas simultaneas; nada impedia 10.
#
# POR QUE SALTEAR ES SEGURO Y NO PIERDE MEMORIA: `session_capture.py` reprocesa
# el transcript COMPLETO en cada corrida. Si se saltea un turno, la corrida
# siguiente cubre exactamente lo mismo. El defecto que encarece cada turno es
# justo el que hace inofensivo el salteo. Si algun dia la captura pasa a ser
# incremental, esta guarda hay que rehacerla: ahi si se perderia.
LOCK="$HOME/.local/state/seal/session_capture.lock"
ULTIMA="$HOME/.local/state/seal/session_capture.last.$AGENT"
INTERVALO_MIN_SEG="${SEAL_CAPTURE_MIN_INTERVAL:-180}"
mkdir -p "$(dirname "$LOCK")" 2>/dev/null || true

# 1) Cadencia por agente: si la ultima corrida de ESTE agente fue hace poco, salir.
ahora=$(date +%s)
if [[ -f "$ULTIMA" ]]; then
    ultima=$(cat "$ULTIMA" 2>/dev/null || echo 0)
    [[ "$ultima" =~ ^[0-9]+$ ]] || ultima=0
    if (( ahora - ultima < INTERVALO_MIN_SEG )); then
        exit 0
    fi
fi

# 2) Concurrencia: UNA captura a la vez en toda la maquina. `flock -n` no espera:
#    si hay otra corriendo, este turno se saltea (ver por que es seguro, arriba).
exec 9>"$LOCK"
if ! flock -n 9; then
    exit 0
fi
echo "$ahora" > "$ULTIMA"

LATEST=$(ls -t "$TDIR"/*.jsonl 2>/dev/null | head -1)
if [[ -z "$LATEST" ]]; then
    exit 0
fi

# Only capture if transcript is substantial
LINE_COUNT=$(wc -l < "$LATEST")
if [[ "$LINE_COUNT" -lt "$MIN_LINES" ]]; then
    exit 0
fi

# Run capture (async-safe, no blocking)
"$VENV_PYTHON" "$CAPTURE_SCRIPT" --agent "$AGENT" --file "$LATEST" 2>/dev/null || true

# Mini-sleep consolidation (dedup + OCEAN, ~2-3 min)
# Queda DENTRO del candado a proposito: es el proceso mas caro de los dos y
# corria una vez por turno por agente, sin limite.
CONSOLIDATE="/home/dadito/IA/proyecto-seal/memory/consolidate.py"
cd /home/dadito/IA/proyecto-seal/memory
"$VENV_PYTHON" "$CONSOLIDATE" --quick 2>/dev/null || true
