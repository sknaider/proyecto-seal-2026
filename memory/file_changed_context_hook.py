#!/usr/bin/env python3
"""Safe FileChanged hook adapter for SEAL message JSONL files.

The previous settings embedded Python, JSON and shell quoting three levels
deep.  Every declared FileChanged command failed before producing JSON.  This
adapter keeps settings declarative and guarantees either a valid hook payload
or an empty object.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ALLOWED_ROOT = Path("/home/dadito/IA/proyecto-seal/messages").resolve()


def read_last_json(path: Path) -> dict:
    """Read the last non-empty JSON object without loading an entire JSONL."""
    try:
        resolved = path.expanduser().resolve(strict=True)
        resolved.relative_to(ALLOWED_ROOT)
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    except (OSError, ValueError):
        return {}
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def render(kind: str, message: dict) -> str:
    if kind == "vscode_command":
        return (
            f"MENSAJE DE JARVIS: [{message.get('type', '?')}] "
            f"{str(message.get('message', ''))[:300]}"
        )
    if kind == "jarvis_wakeup":
        count = message.get("count", 1)
        return (
            f"⚡ JARVIS ESCRIBIO ({count} mensajes nuevos):\n"
            f"{str(message.get('message', ''))[:400]}"
        )
    if kind == "ada_soul_alert":
        return f"🚨 SOUL ALERT: {str(message.get('message', ''))[:300]}"
    if kind == "jarvis_soul_alert":
        return f"🚨 JARVIS SOUL ALERT: {str(message.get('message', ''))[:300]}"
    if kind == "nexus_inbox":
        return (
            f"📨 DM de {message.get('from', '?')}: "
            f"{str(message.get('message', ''))[:400]}"
        )
    return ""


def build_output(
    kind: str,
    path: Path,
    *,
    expected_agent: str | None = None,
    current_agent: str | None = None,
) -> dict:
    """Build context only for the explicitly targeted agent.

    FileChanged settings are global.  Without this gate, a NEXUS inbox event
    could be injected into ADA/JARVIS/ALICE, violating channel isolation.
    """
    if expected_agent:
        actual = (current_agent if current_agent is not None else os.environ.get("SEAL_AGENT", "")).upper()
        if actual != expected_agent.upper():
            return {}
    message = read_last_json(path)
    context = render(kind, message) if message else ""
    if not context:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "FileChanged",
            "additionalContext": context,
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--agent", required=True, choices=("ADA", "JARVIS", "NEXUS"))
    parser.add_argument(
        "--kind",
        required=True,
        choices=(
            "vscode_command",
            "jarvis_wakeup",
            "ada_soul_alert",
            "jarvis_soul_alert",
            "nexus_inbox",
        ),
    )
    args = parser.parse_args()
    # Consume Claude's event payload so a closed stdin never leaks to children.
    try:
        sys.stdin.read()
    except OSError:
        pass
    print(json.dumps(build_output(args.kind, args.file, expected_agent=args.agent), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
