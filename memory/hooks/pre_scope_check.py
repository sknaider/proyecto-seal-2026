#!/usr/bin/env python3
"""
Sprint 3 / Mitigation C — Agent scope restriction hook.

Hook PreToolUse para Edit, MultiEdit, Write.
Verifica que el agente que edita tiene permiso de tocar el archivo target.
Usa agent_scope.yaml para definir allow/deny patterns por agente.

Modo actual (Sprint 3): WARN — nunca bloquea, solo audita y alerta.
Modo strict (futuro): cambiar mode: 'block' en agent_scope.yaml.

Contract:
    stdin  → JSON con {"tool_name", "tool_input": {"file_path"}, "agent"}
    stdout → JSON {"decision": "allow"|"block", "scope": str, "reason": str}
    exit   → 0 siempre
"""
from __future__ import annotations

import fnmatch
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path("/home/dadito/IA/proyecto-seal")
SCOPE_YAML = REPO_ROOT / "memory" / "agent_scope.yaml"
LOG_FILE = REPO_ROOT / "memory" / "logs" / "pre_scope_check.jsonl"
ALERT_ENDPOINT = "http://localhost:8765/api/agents/send"

LOG_FILE.parent.mkdir(parents=True, exist_ok=True)


def emit(result: dict) -> None:
    print(json.dumps(result, ensure_ascii=False))
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.exit(0)


def load_scope() -> tuple[dict, str]:
    """Returns (agents_dict, mode). Falls back to permissive on error."""
    if not SCOPE_YAML.exists():
        return {}, "warn"
    agents: dict = {}
    mode = "warn"
    # Minimal YAML parser (avoid pyyaml dependency)
    current_agent = None
    current_list = None
    for line in SCOPE_YAML.read_text(encoding="utf-8").splitlines():
        s = line.rstrip()
        if s.startswith("mode:"):
            mode = s.split(":", 1)[1].strip().strip("'\"")
            continue
        if s.startswith("  ") and s.strip().endswith(":") and not s.strip().startswith("-"):
            # agent name key (2-space indent)
            if not s.startswith("    "):
                current_agent = s.strip().rstrip(":")
                agents.setdefault(current_agent, {"allow": [], "deny": []})
                current_list = None
                continue
        if current_agent and s.strip() in ("allow:", "deny:"):
            current_list = s.strip().rstrip(":")
            continue
        if current_agent and current_list and s.strip().startswith("- "):
            pat = s.strip()[2:].strip().strip('"').strip("'")
            agents[current_agent][current_list].append(pat)
    return agents, mode


def check_scope(agent: str, file_path: str, agents: dict) -> tuple[str, str]:
    """Returns (status, reason). Status: 'in_scope'|'deny'|'not_in_scope'."""
    if not agent or agent not in agents:
        return "in_scope", "agent_not_configured"
    fp = os.path.abspath(file_path)
    scope = agents[agent]
    # Deny takes precedence
    for pat in scope.get("deny", []):
        if fnmatch.fnmatch(fp, pat):
            return "deny", f"matches deny pattern: {pat}"
    # Check allow
    for pat in scope.get("allow", []):
        if fnmatch.fnmatch(fp, pat):
            return "in_scope", f"matches allow pattern: {pat}"
    return "not_in_scope", "no allow pattern matched"


_ALERT_STAMP_DIR = Path("/tmp/seal_alert_stamps")
ALERT_COOLDOWN_S = 60


def post_alert(agent: str, file_path: str, reason: str) -> None:
    if os.environ.get("SEAL_HOOK_TEST"):
        return
    try:
        _ALERT_STAMP_DIR.mkdir(parents=True, exist_ok=True)
        from hashlib import sha256 as _sha256
        key = _sha256(f"scope:{agent}:{file_path}".encode()).hexdigest()[:16]
        stamp_file = _ALERT_STAMP_DIR / f"{key}.stamp"
        now = time.time()
        if stamp_file.exists():
            last = float(stamp_file.read_text().strip() or "0")
            if now - last < ALERT_COOLDOWN_S:
                return
        stamp_file.write_text(str(now))
    except Exception:
        pass
    try:
        import urllib.request
        msg = (
            f"[SCOPE WARN] {agent} intentó editar fuera de su scope: "
            f"{Path(file_path).name} — {reason}"
        )
        payload = json.dumps({
            "from": agent,
            "to": "William",
            "type": "alert",
            "channel": "web_chat",
            "message": msg,
        }, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            ALERT_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        emit({"decision": "allow", "scope": "empty_payload"})

    try:
        d = json.loads(raw)
    except Exception:
        emit({"decision": "allow", "scope": "unparseable"})

    tool = d.get("tool_name") or d.get("tool") or ""
    if tool not in ("Edit", "MultiEdit", "Write"):
        emit({"decision": "allow", "scope": "tool_not_guarded", "tool": tool})

    ti = d.get("tool_input") or d.get("input") or {}
    file_path = ti.get("file_path") or ti.get("path") or ""
    agent = d.get("agent") or os.environ.get("SEAL_AGENT", "")
    trace_id = d.get("trace_id", "-")

    if not file_path or not agent:
        emit({"decision": "allow", "scope": "missing_agent_or_path",
              "agent": agent, "file": file_path})

    agents, mode = load_scope()
    status, reason = check_scope(agent, file_path, agents)

    result = {
        "decision": "allow",  # Sprint 3: always allow (warn-only)
        "scope": status,
        "reason": reason,
        "agent": agent,
        "file": file_path,
        "mode": mode,
        "trace_id": trace_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    if status in ("deny", "not_in_scope"):
        post_alert(agent, file_path, reason)
        if mode == "block":
            result["decision"] = "block"
            result["reason_block"] = f"Scope violation [{status}]: {reason}"

    emit(result)


if __name__ == "__main__":
    main()
