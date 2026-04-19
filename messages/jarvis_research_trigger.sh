#!/bin/bash
# jarvis_research_trigger.sh — Dispara prompt de investigación a JARVIS via web_chat
# Llamado por seal-jarvis-research.timer cada 3h
# JARVIS lo recibe via Monitor tail -F y ejecuta WebSearch

JARVIS_PID=$(ps -C claude -o pid= -o args= 2>/dev/null | awk '/--name JARVIS/{print $1}' | head -1)

# Solo disparar si JARVIS está vivo
if [ -z "$JARVIS_PID" ]; then
  echo "[$(date '+%Y-%m-%dT%H:%M:%S')] SKIP — JARVIS no está vivo" >> /tmp/jarvis_research_trigger.log
  exit 0
fi

curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d '{"from":"SEAL_SYSTEM","to":"JARVIS","type":"loop_trigger","channel":"web_chat","message":"[RESEARCH LOOP] Si no hay trabajo urgente del equipo, investiga mejoras para SOUL usando WebSearch. Busca papers recientes sobre: AI agent memory, personality persistence, metacognition, medical AI fine-tuning, o graph-based reasoning. Guarda hallazgos importantes como reasoning traces en memory_store."}' \
  >> /tmp/jarvis_research_trigger.log 2>&1

echo "[$(date '+%Y-%m-%dT%H:%M:%S')] Trigger enviado a JARVIS (PID $JARVIS_PID)" >> /tmp/jarvis_research_trigger.log
