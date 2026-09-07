#!/bin/bash
# chat_view.sh — Vista unificada de conversación ADA ↔ JARVIS

DIR="$(dirname "$0")"
LOG_ADA="$DIR/terminal_log.jsonl"
LOG_JARVIS="$DIR/vscode_commands.jsonl"
LINES="${1:-30}"

BOLD='\033[1m'
RESET='\033[0m'
GRAY='\033[0;90m'

echo -e "${BOLD}━━━ SEAL CHAT — ADA ↔ JARVIS (últimos $LINES mensajes) ━━━${RESET}\n"

{
  [ -f "$LOG_ADA" ]    && awk '{print "ADA|" $0}' "$LOG_ADA"
  [ -f "$LOG_JARVIS" ] && awk '{print "JAR|" $0}' "$LOG_JARVIS"
} | python3 -c "
import sys, json, os

RESET   = '\033[0m'
BOLD    = '\033[1m'
CYAN    = '\033[0;36m'
MAGENTA = '\033[0;35m'
YELLOW  = '\033[1;33m'
GRAY    = '\033[0;90m'
RED     = '\033[0;31m'

icons = {
    'directive':'📋','task_assignment':'📌','response':'💬','status':'📊',
    'alert':'🚨','ack':'✅','ping':'🏓','design':'🏗️','critical_update':'🔴',
    'audit_report':'🔍','query':'❓','test':'🧪','briefing':'📝','wake':'⏰',
}

lines = []
for raw in sys.stdin:
    raw = raw.strip()
    if not raw: continue
    source, _, rest = raw.partition('|')
    try:
        msg = json.loads(rest)
    except:
        continue
    lines.append((msg.get('timestamp','1970'), source, msg))

lines.sort(key=lambda x: x[0])
n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
lines = lines[-n:]

for ts, source, msg in lines:
    ts_short = ts[5:16].replace('T',' ')
    sender = msg.get('from', source)
    mtype  = msg.get('type','')
    mid    = msg.get('id','')
    prio   = msg.get('priority','')
    icon   = icons.get(mtype,'💬')

    if sender == 'ADA':
        color = CYAN
        arrow = f'{CYAN}ADA{RESET} → {MAGENTA}JARVIS{RESET}'
    elif sender == 'JARVIS':
        color = MAGENTA
        arrow = f'{MAGENTA}JARVIS{RESET} → {CYAN}ADA{RESET}'
    else:
        color = GRAY
        arrow = f'{GRAY}{sender}{RESET}'

    prio_str = f' {RED}[CRITICAL]{RESET}' if prio=='critical' else f' {YELLOW}[HIGH]{RESET}' if prio=='high' else ''
    print(f'{GRAY}{ts_short}{RESET}  {arrow}  {icon} {BOLD}{mtype}{RESET}{prio_str}  {GRAY}{mid}{RESET}')

    text = str(msg.get('message', msg.get('command','')))
    msg_lines = [l for l in text.split('\n') if l.strip()]
    for i, line in enumerate(msg_lines[:5]):
        line = line.strip()
        if len(line) > 120: line = line[:117]+'...'
        print(f'  {color}{line}{RESET}')
    if len(msg_lines) > 5:
        print(f'  {GRAY}... ({len(msg_lines)-5} líneas más){RESET}')
    print()
" "$LINES"

echo -e "${GRAY}Uso: $0 [N]   — muestra los últimos N mensajes (default 30)${RESET}"
