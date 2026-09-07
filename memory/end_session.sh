#!/bin/bash
# SEAL Soul — End of Session Hook
# Run this when closing an ADA/JARVIS session to capture final state
# Usage: end_session.sh [AGENT]  (default: ADA)
cd /home/dadito/IA/proyecto-seal/memory
PYTHON=/home/dadito/IA/seal-spark/.venv/bin/python3
AGENT=${1:-ADA}
LOG=/home/dadito/IA/proyecto-seal/logs/session_end.log

echo "$(date) — End of session for $AGENT" >> "$LOG"

# Capture conversation memories from transcript
$PYTHON session_capture.py --agent "$AGENT" >> "$LOG" 2>&1

# Run soul reflection (diary, opinions, relationships, style, distill)
$PYTHON soul_reflect.py full "$AGENT" >> "$LOG" 2>&1

# Backup
$PYTHON soul_backup.py >> "$LOG" 2>&1

echo "$(date) — Session capture complete for $AGENT" >> "$LOG"

# KAIROS daily log — append session entry to logs/YYYY/MM/YYYY-MM-DD.md
$PYTHON kairos_daily_log.py "$AGENT" >> "$LOG" 2>&1

echo "Soul saved for $AGENT."
