#!/bin/bash
# SEAL File Changed Hook (Fase 6 — Signal Accelerator)
# Claude Code FileChanged hook. Updates signal files when message .jsonl files change.
# Observability only — output is ignored for FileChanged hooks.

EVENT=$(cat)

FILE_PATH=$(echo "$EVENT" | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('file_path',''))" 2>/dev/null || echo "")

BASENAME=$(basename "$FILE_PATH" 2>/dev/null || echo "")
SIGNALS_DIR="$HOME/IA/proyecto-seal/messages"
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

case "$BASENAME" in
    ada_messages.jsonl)
        echo "$TS" > "$SIGNALS_DIR/.ada_signal"
        ;;
    jarvis_messages.jsonl)
        echo "$TS" > "$SIGNALS_DIR/.jarvis_signal"
        ;;
esac

exit 0
