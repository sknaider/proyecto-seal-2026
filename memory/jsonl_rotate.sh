#!/bin/bash
# JSONL rotation — archiva archivos >5MB en messages/ a checkpoints/
# Owner: NEXUS | Cadencia: semanal (domingos 03:00)
MESSAGES_DIR="/home/dadito/IA/proyecto-seal/messages"
CHECKPOINTS_DIR="$MESSAGES_DIR/checkpoints"
MAX_BYTES=$((5 * 1024 * 1024))
TS=$(date +%Y%m%d_%H%M%S)

mkdir -p "$CHECKPOINTS_DIR"

find "$MESSAGES_DIR" -maxdepth 1 -name "*.jsonl" -size +5M | while read -r f; do
    base=$(basename "$f" .jsonl)
    archive="$CHECKPOINTS_DIR/${base}_${TS}.jsonl"
    cp "$f" "$archive"
    truncate -s 0 "$f"
    echo "[jsonl_rotate] $f → $archive ($(du -h "$archive" | cut -f1))"
done
