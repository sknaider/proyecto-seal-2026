#!/bin/bash
# william_say.sh — William escribe al chat SEAL
# Uso: bash william_say.sh "tu mensaje aquí"

MSG="$*"
if [ -z "$MSG" ]; then
    echo "Uso: bash william_say.sh \"tu mensaje\""
    exit 1
fi

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
ID="william_$(date +%s)"
DIR="$(dirname "$0")"

echo "{\"id\": \"$ID\", \"from\": \"William\", \"to\": \"ADA+JARVIS\", \"timestamp\": \"$TS\", \"type\": \"chat\", \"message\": \"$MSG\"}" >> "$DIR/william_chat.jsonl"

echo "✅ Mensaje enviado al chat"
