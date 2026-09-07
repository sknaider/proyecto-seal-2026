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
CONSOLIDATE="/home/dadito/IA/proyecto-seal/memory/consolidate.py"
cd /home/dadito/IA/proyecto-seal/memory
"$VENV_PYTHON" "$CONSOLIDATE" --quick 2>/dev/null || true
