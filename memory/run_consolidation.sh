#!/bin/bash
# SEAL Soul — Full Consolidation + Reflection + Session Capture
# Runs daily at 4AM via cron
cd /home/dadito/IA/proyecto-seal/memory
PYTHON=/home/dadito/IA/seal-spark/.venv/bin/python3
LOG=/home/dadito/IA/proyecto-seal/logs/consolidation.log

echo "$(date) — Starting SEAL Soul consolidation cycle" >> "$LOG"

# Step 1: Capture sessions (extract memories from Claude Code transcripts)
$PYTHON session_capture.py --agent ADA >> "$LOG" 2>&1

# Step 2: Consolidate (summarize events, merge redundant, archive old, detect drift)
$PYTHON consolidate.py --hours 24 >> "$LOG" 2>&1

# Step 3: Soul reflection (diary, opinions, relationships, style, distilled boot prompt)
$PYTHON soul_reflect.py full ADA >> "$LOG" 2>&1
$PYTHON soul_reflect.py full JARVIS >> "$LOG" 2>&1

# Step 4: Backup
$PYTHON soul_backup.py >> "$LOG" 2>&1

echo "$(date) ��� SEAL Soul consolidation cycle complete" >> "$LOG"
