#!/usr/bin/env python3
"""
Sprint 1 / Mitigación D — Edit precision hook.

Hook PreToolUse para Edit, MultiEdit, Write.
Rechaza ediciones cuyo `old_string` contenga metacaracteres de regex sin escapar,
forzando string-match exacto. Reduce drift silencioso por reemplazos colaterales.

Válvula de escape: el agente puede declarar `--literal-pattern` (o un campo
`literal_pattern: true` en el payload de la tool) para autorizar metacaracteres
literales — la excepción queda audit-logged.

Contract (Claude Code hooks):
    stdin  → JSON con {"tool_name": str, "tool_input": dict, "agent": str?}
    stdout → JSON {"decision": "allow"|"block", "reason": str}
    exit   → 0 siempre (decisión va por JSON, no por exit code)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

AUDIT_LOG = Path("/home/dadito/IA/proyecto-seal/memory/logs/edit_precision_audit.jsonl")
ALERT_ENDPOINT = "http://localhost:8765/api/agents/send"

# Metacaracteres de regex que más comúnmente causan drift silencioso
# cuando se asumen literales. Escapados los detectamos y los dejamos pasar.
SUSPICIOUS_METACHARS = re.compile(
    r"(?<!\\)(\.\*|\.\+|\\d|\\w|\\s|\\b|\\W|\\D|\\S|\[\^?[^\]]*\]|\([^)]*\)\?|\([^)]*\)\+|\([^)]*\)\*|\{\d+,?\d*\})"
)
# Tools que forzamos a precisión literal
GUARDED_TOOLS = {"Edit", "MultiEdit", "Write"}


def _log(entry: dict) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _post_alert(agent: str, reason: str, tool: str, snippet: str) -> None:
    try:
        import urllib.request
        payload = {
            "from": "edit_precision_hook",
            "to": "JARVIS",
            "type": "system_alert",
            "channel": "web_chat",
            "message": (
                f"[edit_precision] BLOQUEO {tool} de {agent}: {reason}\n"
                f"Snippet: {snippet[:200]!r}"
            ),
        }
        req = urllib.request.Request(
            ALERT_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=2).read()
    except Exception:
        pass


def _looks_like_regex(s: str) -> tuple[bool, str | None]:
    if not s:
        return False, None
    m = SUSPICIOUS_METACHARS.search(s)
    if m:
        return True, m.group(0)
    return False, None


def evaluate(tool_name: str, tool_input: dict, agent: str = "unknown") -> dict:
    if tool_name not in GUARDED_TOOLS:
        return {"decision": "allow", "reason": "tool not guarded"}

    literal_flag = bool(tool_input.get("literal_pattern", False))

    candidates: list[tuple[str, str]] = []
    if tool_name == "Edit":
        candidates.append(("old_string", tool_input.get("old_string", "")))
    elif tool_name == "MultiEdit":
        for i, edit in enumerate(tool_input.get("edits", []) or []):
            candidates.append((f"edits[{i}].old_string", edit.get("old_string", "")))
    # Write no tiene old_string — no hay riesgo de regex en old_string; permitimos
    # (la mitigación B/F cubre el contenido).

    for field, value in candidates:
        is_regex, snippet = _looks_like_regex(value)
        if not is_regex:
            continue
        if literal_flag:
            _log({
                "agent": agent, "tool": tool_name, "field": field,
                "decision": "allow_literal_flag", "snippet": snippet,
            })
            return {
                "decision": "allow",
                "reason": f"regex metachar {snippet!r} en {field} pero literal_pattern=true (audit-logged)",
            }
        reason = (
            f"{field} contiene metacaracteres regex ({snippet!r}). "
            "Edit/MultiEdit/Write usan string-match literal — usa el string exacto del archivo. "
            "Si el archivo realmente contiene esos caracteres, añade literal_pattern=true."
        )
        _log({
            "agent": agent, "tool": tool_name, "field": field,
            "decision": "block", "snippet": snippet,
        })
        _post_alert(agent, reason, tool_name, value)
        return {"decision": "block", "reason": reason}

    return {"decision": "allow", "reason": "no regex metachars detected"}


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"decision": "allow", "reason": "empty payload"}))
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"decision": "allow", "reason": f"unparseable payload: {e}"}))
        return 0

    tool_name = payload.get("tool_name") or payload.get("tool", "")
    tool_input = payload.get("tool_input") or payload.get("input", {}) or {}
    agent = payload.get("agent") or os.environ.get("SEAL_AGENT", "unknown")

    result = evaluate(tool_name, tool_input, agent)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
