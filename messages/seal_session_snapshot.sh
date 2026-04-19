#!/bin/bash
# seal_session_snapshot.sh — Captura session_id activo de cada agente cada 30s
# Para Bug 2/Fase A: permitir claude --resume <SID> al resucitar
# Detecta el JSONL más reciente (mtime) en cada dir de proyecto claude

PROJECTS_DIR="$HOME/.claude/projects"
OUT_DIR="/tmp"

declare -A AGENT_DIRS=(
  [ada]="$PROJECTS_DIR/-home-dadito-IA-proyecto-seal"
  [jarvis]="$PROJECTS_DIR/-home-dadito-IA-proyecto-seal-memory"
  [alice]="$PROJECTS_DIR/-home-dadito-IA-proyecto-seal-alice"
)

for AGENT in "${!AGENT_DIRS[@]}"; do
  DIR="${AGENT_DIRS[$AGENT]}"
  [ -d "$DIR" ] || continue

  LATEST=$(ls -1t "$DIR"/*.jsonl 2>/dev/null | head -1)
  [ -z "$LATEST" ] && continue

  # Solo guardar si el JSONL fue tocado en los últimos 15min (sesión activa)
  AGE=$(( $(date +%s) - $(stat -c %Y "$LATEST" 2>/dev/null || echo 0) ))
  [ "$AGE" -gt 900 ] && continue

  SID=$(basename "$LATEST" .jsonl)
  OUT_FILE="$OUT_DIR/${AGENT}_session.id"

  # Solo escribir si cambió (evita touch innecesario)
  if [ ! -f "$OUT_FILE" ] || [ "$(cat "$OUT_FILE" 2>/dev/null)" != "$SID" ]; then
    echo "$SID" > "$OUT_FILE"
  fi
done
