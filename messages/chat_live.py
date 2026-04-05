#!/usr/bin/env python3
"""
chat_live.py — SEAL Team Chat en Tiempo Real
William puede ver y participar en la conversación ADA ↔ JARVIS en vivo.
Uso: python3 chat_live.py [historial=20]
"""

import sys, json, time, os
from pathlib import Path
from datetime import datetime, timezone

# Colores
RESET   = '\033[0m'
BOLD    = '\033[1m'
CYAN    = '\033[0;36m'      # ADA
MAGENTA = '\033[0;35m'      # JARVIS
YELLOW  = '\033[1;33m'      # William
RED     = '\033[0;31m'      # CRITICAL
GRAY    = '\033[0;90m'      # timestamps / metadata
GREEN   = '\033[0;32m'      # sistema
DIM     = '\033[2m'

DIR = Path(__file__).parent
LOG_ADA     = DIR / "terminal_log.jsonl"
LOG_JARVIS  = DIR / "vscode_commands.jsonl"
LOG_WILLIAM = DIR / "william_chat.jsonl"

ICONS = {
    'directive':       '📋',
    'task_assignment': '📌',
    'response':        '💬',
    'status':          '📊',
    'alert':           '🚨',
    'ack':             '✅',
    'ping':            '🏓',
    'design':          '🏗️',
    'critical_update': '🔴',
    'audit_report':    '🔍',
    'audit':           '🔍',
    'query':           '❓',
    'test':            '🧪',
    'briefing':        '📝',
    'wake':            '⏰',
    'milestone':       '🏆',
    'report':          '📄',
    'fix':             '🔧',
    'bug_report':      '🐛',
    'opinion':         '💡',
    'hook_test_ack':   '🔗',
}

def parse_line(raw: str, source: str):
    """Parsea una línea JSONL. Retorna (timestamp, source, msg) o None."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        msg = json.loads(raw)
        return (msg.get('timestamp', '1970-01-01T00:00:00Z'), source, msg)
    except json.JSONDecodeError:
        return None

def format_msg(ts: str, source: str, msg: dict, compact=False) -> str:
    """Formatea un mensaje para mostrar en terminal."""
    # Timestamp legible
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        # Convertir a hora local (UTC-5 Perú)
        ts_local = dt.strftime('%d/%m %H:%M')
    except:
        ts_local = ts[5:16].replace('T', ' ')

    sender  = msg.get('from', source)
    mtype   = msg.get('type', 'message')
    mid     = msg.get('id', '')
    prio    = msg.get('priority', '')
    icon    = ICONS.get(mtype, '💬')

    # Colores por agente
    if sender == 'ADA':
        color  = CYAN
        header = f'{CYAN}{BOLD}ADA{RESET} {GRAY}→{RESET} {MAGENTA}JARVIS{RESET}'
    elif sender == 'JARVIS':
        color  = MAGENTA
        header = f'{MAGENTA}{BOLD}JARVIS{RESET} {GRAY}→{RESET} {CYAN}ADA{RESET}'
    elif sender == 'William':
        color  = YELLOW
        header = f'{YELLOW}{BOLD}👑 William{RESET} {GRAY}→{RESET} {CYAN}ADA{RESET}{GRAY}+{RESET}{MAGENTA}JARVIS{RESET}'
    else:
        color  = GRAY
        header = f'{GRAY}{sender}{RESET}'

    # Priority badge
    if prio == 'critical':
        badge = f' {RED}[CRÍTICO]{RESET}'
    elif prio == 'high':
        badge = f' {RED}[ALTO]{RESET}'
    else:
        badge = ''

    # Header line
    lines = []
    lines.append(
        f'{GRAY}[{ts_local}]{RESET}  {header}  '
        f'{icon} {DIM}{mtype}{RESET}{badge}  {GRAY}{mid}{RESET}'
    )

    # Contenido del mensaje — sin cortes
    text = str(msg.get('message', msg.get('command', ''))).strip()
    if text:
        for line in text.split('\n'):
            lines.append(f'  {color}{line}{RESET}')

    lines.append('')  # línea en blanco entre mensajes
    return '\n'.join(lines)

def read_all_msgs(n: int):
    """Lee todos los mensajes de los tres archivos, ordena por timestamp, retorna los últimos n."""
    msgs = []
    for log, source in [(LOG_ADA, 'ADA'), (LOG_JARVIS, 'JARVIS'), (LOG_WILLIAM, 'William')]:
        if log.exists():
            with open(log, 'r', encoding='utf-8') as f:
                for line in f:
                    parsed = parse_line(line, source)
                    if parsed:
                        msgs.append(parsed)
    msgs.sort(key=lambda x: x[0])
    return msgs[-n:]

def read_new_lines(path: Path, offset: int):
    """Lee líneas nuevas desde un offset dado. Retorna (nuevas_líneas, nuevo_offset)."""
    if not path.exists():
        return [], offset
    size = path.stat().st_size
    if size <= offset:
        return [], offset
    with open(path, 'r', encoding='utf-8') as f:
        f.seek(offset)
        content = f.read()
        new_offset = f.tell()
    lines = content.split('\n')
    return [l for l in lines if l.strip()], new_offset

def print_header(history: int):
    os.system('clear')
    print(f'{BOLD}{"━" * 60}')
    print(f'  🔭 SEAL CHAT — ADA ↔ JARVIS  (en vivo)')
    print(f'{"━" * 60}{RESET}')
    print(f'{GRAY}  Mostrando últimos {history} mensajes + actualizaciones en tiempo real')
    print(f'  Ctrl+C para salir{RESET}')
    print(f'{BOLD}{"━" * 60}{RESET}\n')

def main():
    history = int(sys.argv[1]) if len(sys.argv) > 1 else 20

    # Mostrar historial inicial
    print_header(history)
    initial = read_all_msgs(history)
    for ts, source, msg in initial:
        print(format_msg(ts, source, msg, compact=True), end='')

    print(f'{GREEN}{"─" * 60}')
    print(f'  ⬇  Mensajes nuevos aparecerán aquí{RESET}')
    print(f'{GREEN}{"─" * 60}{RESET}\n')

    # Guardar posiciones actuales de los archivos
    offset_ada     = LOG_ADA.stat().st_size     if LOG_ADA.exists()     else 0
    offset_jarvis  = LOG_JARVIS.stat().st_size  if LOG_JARVIS.exists()  else 0
    offset_william = LOG_WILLIAM.stat().st_size if LOG_WILLIAM.exists() else 0

    # Bucle de monitoreo
    try:
        while True:
            time.sleep(0.5)

            new_entries = []

            # Verificar ADA
            new_ada, offset_ada = read_new_lines(LOG_ADA, offset_ada)
            for raw in new_ada:
                parsed = parse_line(raw, 'ADA')
                if parsed:
                    new_entries.append(parsed)

            # Verificar JARVIS
            new_jarvis, offset_jarvis = read_new_lines(LOG_JARVIS, offset_jarvis)
            for raw in new_jarvis:
                parsed = parse_line(raw, 'JARVIS')
                if parsed:
                    new_entries.append(parsed)

            # Verificar William
            new_william, offset_william = read_new_lines(LOG_WILLIAM, offset_william)
            for raw in new_william:
                parsed = parse_line(raw, 'William')
                if parsed:
                    new_entries.append(parsed)

            # Ordenar por timestamp antes de imprimir
            new_entries.sort(key=lambda x: x[0])
            for ts, source, msg in new_entries:
                print(format_msg(ts, source, msg, compact=False), end='', flush=True)

    except KeyboardInterrupt:
        print(f'\n{GRAY}[Chat cerrado]{RESET}\n')

if __name__ == '__main__':
    main()
