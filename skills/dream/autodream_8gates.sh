#!/usr/bin/env bash
# autoDream 8-Gate Check + Trigger (SEAL implementation)
# Spec: SEAL_MASTER_DOC/SPEC_03_BOOTSTRAP_KAIROS_DREAM.md
# Activado: 2026-04-27 (luz verde William)
#
# 8 Gates (barato → caro):
# 1. Feature gate: AUTODREAM_ENABLED env/config
# 2. KAIROS exclusion: si active_recall está en curso → skip
# 3. Remote exclusion: no en sesión SSH sin TTY
# 4. Auto-memory enabled: confirma que memory extraction está activa
# 5. Time gate: >= 24h desde última consolidación
# 6. Scan throttle: >= 10 min desde último scan
# 7. Session gate: >= 5 sesiones nuevas desde última consolidación
# 8. Lock: adquisición atómica via PID file

set -euo pipefail

SKILL_DIR="$HOME/.claude/skills/dream"
LAST_DREAM_FILE="$SKILL_DIR/.last-dream"
SCAN_TIMESTAMP="/tmp/.autodream_last_scan"
LOCK_FILE="/tmp/.autodream.lock"
LOG_FILE="/tmp/autodream_8gates.log"
MIN_SESSIONS=5
MIN_SCAN_GAP_SECS=600   # 10 min
MIN_TIME_GAP_SECS=86400  # 24h
AGENT="${SEAL_AGENT:-ADA}"

_log() { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG_FILE"; }

# GATE 1: Feature gate
[[ "${AUTODREAM_ENABLED:-1}" != "1" ]] && { _log "GATE1 FAIL: disabled"; exit 1; }
_log "GATE1 OK"

# GATE 2: KAIROS exclusion — skip if active_recall hook is in progress
# Check if any active_recall process is running
if pgrep -f "active_recall_hook" > /dev/null 2>&1; then
    _log "GATE2 FAIL: KAIROS/active_recall in progress"
    exit 1
fi
_log "GATE2 OK"

# GATE 3: Remote exclusion — check if this is an interactive local session
# Skip if running in non-interactive shell without TTY (e.g. pure cron without session)
if [[ ! -t 1 ]] && [[ -z "${SEAL_AGENT:-}" ]]; then
    _log "GATE3 FAIL: no TTY and no SEAL_AGENT (pure cron context)"
    exit 1
fi
_log "GATE3 OK"

# GATE 4: Auto-memory enabled — verify settings.json has Stop hook
if ! grep -q 'memory_extraction_hook' "$HOME/.claude/settings.json" 2>/dev/null; then
    _log "GATE4 FAIL: memory extraction hook not in settings.json"
    exit 1
fi
_log "GATE4 OK"

# GATE 5: Time gate — >= 24h since last consolidation
NOW=$(date +%s)
if [[ -f "$LAST_DREAM_FILE" ]]; then
    LAST_DREAM=$(cat "$LAST_DREAM_FILE")
    ELAPSED=$(( NOW - LAST_DREAM ))
    if (( ELAPSED < MIN_TIME_GAP_SECS )); then
        HOURS=$(( ELAPSED / 3600 ))
        _log "GATE5 FAIL: only ${HOURS}h since last dream (need 24h)"
        exit 1
    fi
    _log "GATE5 OK: $((ELAPSED/3600))h since last dream"
else
    _log "GATE5 OK: first run (no .last-dream)"
fi

# GATE 6: Scan throttle — >= 10 min since last scan
if [[ -f "$SCAN_TIMESTAMP" ]]; then
    LAST_SCAN=$(cat "$SCAN_TIMESTAMP")
    SCAN_ELAPSED=$(( NOW - LAST_SCAN ))
    if (( SCAN_ELAPSED < MIN_SCAN_GAP_SECS )); then
        _log "GATE6 FAIL: scanned ${SCAN_ELAPSED}s ago (need 600s)"
        exit 1
    fi
fi
echo "$NOW" > "$SCAN_TIMESTAMP"
_log "GATE6 OK"

# GATE 7: Session gate — >= 5 conversation JSONL files newer than last consolidation
PROJ_DIR="$HOME/.claude/projects/-home-dadito-IA-proyecto-seal"
LAST_DREAM_TS=0
[[ -f "$LAST_DREAM_FILE" ]] && LAST_DREAM_TS=$(cat "$LAST_DREAM_FILE")

NEW_SESSIONS=0
for f in "$PROJ_DIR"/*.jsonl; do
    [[ -f "$f" ]] || continue
    FILE_MTIME=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    (( FILE_MTIME > LAST_DREAM_TS )) && (( NEW_SESSIONS++ ))
done

if (( NEW_SESSIONS < MIN_SESSIONS )); then
    _log "GATE7 FAIL: only $NEW_SESSIONS new sessions (need $MIN_SESSIONS)"
    exit 1
fi
_log "GATE7 OK: $NEW_SESSIONS new sessions"

# GATE 8: PID lock — atomic acquisition
if [[ -f "$LOCK_FILE" ]]; then
    OLD_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo 0)
    # Check if PID is still alive
    if kill -0 "$OLD_PID" 2>/dev/null; then
        _log "GATE8 FAIL: lock held by PID $OLD_PID"
        exit 1
    else
        _log "GATE8: stale lock (PID $OLD_PID dead), removing"
        rm -f "$LOCK_FILE"
    fi
fi

# Write our PID atomically, verify we won
echo $$ > "$LOCK_FILE"
SLEEP_PID=$(cat "$LOCK_FILE")
if [[ "$SLEEP_PID" != "$$" ]]; then
    _log "GATE8 FAIL: lost lock race (got $SLEEP_PID)"
    exit 1
fi
_log "GATE8 OK: lock acquired PID=$$"

# ALL 8 GATES PASSED — Execute autoDream
_log "ALL GATES PASSED — launching dream consolidation for $AGENT"

# Update last-dream timestamp BEFORE launching (prevents double-trigger)
echo "$NOW" > "$LAST_DREAM_FILE"

# Launch Claude dream consolidation in background (non-blocking)
nohup claude -p \
    "Ejecuta la consolidación de memoria autoDream para el agente $AGENT. \
Lee ~/.claude/projects/-home-dadito-IA-proyecto-seal/memory/MEMORY.md y todos los archivos en ese directorio. \
FASES: (1) Orient — lista directorio, lee MEMORY.md, identifica topics existentes. \
(2) Gather — busca señal nueva en logs recientes, memorias obsoletas, grep selectivo en transcripts JSONL. \
(3) Consolidate — actualiza archivos existentes en vez de crear duplicados. Fechas relativas → absolutas. Elimina contradicciones. \
(4) Prune — MEMORY.md < 200 líneas, < 25KB, 1 línea por entry, < 150 chars cada una. \
Al terminar: escribe timestamp Unix en /tmp/.autodream_consolidation_done" \
    --allowedTools "Read,Write,Edit,Bash,Glob,Grep" \
    --output-format text \
    > "/tmp/autodream-${AGENT}-$(date +%Y%m%d-%H%M%S).log" 2>&1 &

DREAM_PID=$!
_log "Dream consolidation launched PID=$DREAM_PID"

# Release lock after a short window (dream process is now independent)
sleep 2
rm -f "$LOCK_FILE"
_log "Lock released"

exit 0
