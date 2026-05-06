#!/usr/bin/env python3
"""
Whisper Inbox — lector de whispers no-leídos para un agente.

Uso:
  python3 whisper_inbox.py --agent NEXUS              # imprime resumen + actualiza cursor
  python3 whisper_inbox.py --agent NEXUS --peek       # imprime sin actualizar cursor
  python3 whisper_inbox.py --agent NEXUS --reset      # marca todo como leído (clear cursor)
  python3 whisper_inbox.py --agent NEXUS --max 20     # limita N whispers a mostrar

Cursor: /tmp/.whisper_inbox_<AGENT>.cursor — guarda el ts ISO del último whisper procesado.

Diseñado para ser invocado en el launcher del agente y prepender output a SEAL_BOOT_MSG.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

SEAL_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
AUDIT_LOG = SEAL_DIR / "whisper_audit.jsonl"
CURSOR_DIR = Path("/tmp")
KNOWN_AGENTS = {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS"}


def cursor_path(agent: str) -> Path:
    return CURSOR_DIR / f".whisper_inbox_{agent}.cursor"


def read_cursor(agent: str) -> str:
    p = cursor_path(agent)
    if not p.exists():
        return ""
    return p.read_text().strip()


def write_cursor(agent: str, ts: str):
    cursor_path(agent).write_text(ts)


def load_unread(agent: str, since: str, max_items: int) -> list[dict]:
    if not AUDIT_LOG.exists():
        return []
    items = []
    with open(AUDIT_LOG) as f:
        for line in f:
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("to") != agent:
                continue
            if e.get("receipt") != "acked":
                continue
            ts = e.get("ts", "")
            if since and ts <= since:
                continue
            items.append(e)
    items.sort(key=lambda x: x.get("ts", ""))
    return items[-max_items:] if max_items else items


def format_inbox(agent: str, items: list[dict]) -> str:
    if not items:
        return f"[whisper-inbox/{agent}] sin whispers nuevos"
    lines = [f"[whisper-inbox/{agent}] {len(items)} whisper(s) nuevo(s):"]
    for w in items:
        ts = w.get("ts", "")[:19].replace("T", " ")
        sender = w.get("from", "?")
        tier = w.get("tier", "?")
        purpose = w.get("purpose", "")
        content = w.get("content", "")
        if len(content) > 140:
            content = content[:137] + "..."
        lines.append(f"  • {ts} UTC  {sender}→ tier{tier}/{purpose}: {content}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True, choices=sorted(KNOWN_AGENTS))
    ap.add_argument("--peek", action="store_true",
                    help="no actualizar el cursor (preview)")
    ap.add_argument("--reset", action="store_true",
                    help="marcar todos como leídos sin imprimir")
    ap.add_argument("--max", type=int, default=20,
                    help="máximo N whispers a mostrar (default 20)")
    ap.add_argument("--json", action="store_true",
                    help="output JSON en vez de texto")
    args = ap.parse_args()

    agent = args.agent

    if args.reset:
        now = datetime.now(timezone.utc).isoformat()
        write_cursor(agent, now)
        print(f"[whisper-inbox/{agent}] cursor reset to {now}")
        return

    cursor = read_cursor(agent)
    items = load_unread(agent, cursor, args.max)

    if args.json:
        print(json.dumps({"agent": agent, "cursor": cursor,
                          "count": len(items), "items": items},
                         indent=2, ensure_ascii=False))
    else:
        print(format_inbox(agent, items))

    if items and not args.peek:
        last_ts = items[-1].get("ts", "")
        if last_ts:
            write_cursor(agent, last_ts)


if __name__ == "__main__":
    main()
