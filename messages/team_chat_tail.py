#!/usr/bin/env python3
"""team_chat_tail — cross-inbox reader for SEAL agents.

Merges ada_messages, jarvis_messages, alice_messages, william_chat and
terminal_log into a single chronological stream, with NO content truncation.
All three agents (ADA/JARVIS/ALICE) run this at the start of each turn so
they see what the others said before they opine.

Rationale: the WebSocket task-notifications are truncated at ~500 chars by
the harness; reading the jsonl files directly bypasses that limit. Each
agent's own message_store already writes to its own inbox, so this is
read-only and safe.

Usage:
  team_chat_tail.py                # last 30 minutes, all inboxes
  team_chat_tail.py --minutes 90   # last 90 minutes
  team_chat_tail.py --limit 40     # most recent 40 entries regardless of time
  team_chat_tail.py --agent ADA    # what ADA has said + been addressed to
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

MSG_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
INBOXES = [
    MSG_DIR / "ada_messages.jsonl",
    MSG_DIR / "jarvis_messages.jsonl",
    MSG_DIR / "alice_messages.jsonl",
    MSG_DIR / "william_chat.jsonl",
    MSG_DIR / "terminal_log.jsonl",
]

COLORS = {
    "ADA": "\033[38;5;75m",
    "JARVIS": "\033[38;5;141m",
    "ALICE": "\033[38;5;209m",
    "WILLIAM": "\033[38;5;40m",
    "DUM": "\033[38;5;244m",
    "SYSTEM": "\033[38;5;244m",
}
RESET = "\033[0m"
DIM = "\033[2m"


def parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def load_entries(paths: list[Path]) -> list[dict]:
    seen_ids: set[str] = set()
    out: list[dict] = []
    for p in paths:
        if not p.exists():
            continue
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    mid = e.get("id") or e.get("idempotency_key") or ""
                    if mid and mid in seen_ids:
                        continue
                    if mid:
                        seen_ids.add(mid)
                    out.append(e)
        except Exception:
            continue
    return out


def fmt(e: dict) -> str:
    sender = (e.get("from") or "?").upper()
    to = e.get("to") or ""
    ts = e.get("timestamp") or ""
    try:
        dt = parse_ts(ts)
        tstr = dt.astimezone().strftime("%H:%M:%S") if dt else ts[:19]
    except Exception:
        tstr = ts[:19]
    typ = e.get("type", "msg")
    chan = e.get("channel", "")
    msg = e.get("message") or e.get("command") or ""
    if isinstance(msg, dict):
        msg = json.dumps(msg, ensure_ascii=False)
    color = COLORS.get(sender, "")
    header = f"{DIM}[{tstr}]{RESET} {color}{sender}{RESET} → {to} {DIM}({typ}/{chan}){RESET}"
    body_lines = str(msg).splitlines()
    body = "\n".join("  " + ln for ln in body_lines)
    return f"{header}\n{body}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--limit", type=int, default=0, help="max entries (0 = unbounded by count)")
    ap.add_argument("--agent", default="", help="filter: only entries from/to this agent")
    ap.add_argument("--json", action="store_true", help="emit JSONL instead of formatted")
    args = ap.parse_args()

    entries = load_entries(INBOXES)
    cutoff = datetime.now(LIMA_TZ) - timedelta(minutes=args.minutes)

    filtered: list[tuple[datetime, dict]] = []
    for e in entries:
        dt = parse_ts(e.get("timestamp", ""))
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt < cutoff:
            continue
        if args.agent:
            a = args.agent.lower()
            if a not in (e.get("from", "").lower(), e.get("to", "").lower()):
                continue
        filtered.append((dt, e))

    filtered.sort(key=lambda x: x[0])
    if args.limit > 0:
        filtered = filtered[-args.limit:]

    if not filtered:
        print(f"(no team chat activity in last {args.minutes} min)")
        return 0

    if args.json:
        for _, e in filtered:
            print(json.dumps(e, ensure_ascii=False))
        return 0

    print(f"=== team chat — last {args.minutes} min, {len(filtered)} entries ===")
    for _, e in filtered:
        print(fmt(e))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
