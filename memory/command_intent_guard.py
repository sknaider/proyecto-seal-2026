#!/usr/bin/env python3
"""SEAL command guardian: decide by intended effect, not spelling alone.

This module is an original SEAL implementation.  It does not execute commands.
The consumer is the Claude Code ``PreToolUse`` hook, so policy is evaluated at
the last read/consume point before Bash execution.

Tests and documentation that must contain dangerous shell text should place it
inside a single-quoted heredoc (``<<'EOF'``).  The guard strips that inert body;
an unquoted heredoc remains denied because the caller shell expands it first.

Fast path:
* deterministic denials for catastrophic targets, shell obfuscation and
  pipe-to-shell;
* deterministic allowances for routine build and literal /tmp cleanup;
* ordinary commands do not call a model.

Slow path:
* only ambiguous risk-bearing commands go to the local Gemma endpoint;
* timeout, transport errors and malformed answers fail closed;
* the command is never written to the audit log (only its SHA-256 digest).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
import urllib.request
from urllib.parse import urlsplit
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable


MODEL_ENDPOINT = os.environ.get(
    "SEAL_COMMAND_GUARD_ENDPOINT",
    "http://127.0.0.1:8899/v1/chat/completions",
)
MODEL_NAME = os.environ.get("SEAL_COMMAND_GUARD_MODEL", "gemma4-dum")
MODEL_TIMEOUT_SECONDS = 15.0
LONG_COMMAND_THRESHOLD = 5_000
LONG_COMMAND_TAIL = 5_000

_CATEGORIES = {
    "credentials",
    "exfiltration",
    "remote_execution",
    "destructive_operation",
    "privilege_escalation",
    "none",
}

_MODEL_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "dangerous": {"type": "boolean"},
        "category": {"type": "string", "enum": sorted(_CATEGORIES)},
        "reason": {"type": "string"},
    },
    "required": ["dangerous", "category", "reason"],
}

_SYSTEM_PROMPT = """You are the local SEAL command intent gate.
Classify whether a shell command can cause material harm beyond the operator's
normal software-engineering intent. Check exactly these risk classes:
credentials, exfiltration, remote_execution, destructive_operation, and
privilege_escalation.

Do not act as a generic malware scanner. Routine inspection, package installs,
reading a local .env for local debugging, activating a virtual environment,
read-only SSH inspection, and cleanup of ./build, dist, node_modules, .venv,
.pytest_cache, or a literal child of /tmp are not dangerous by themselves.
Recursive deletion of /, /usr, /etc, /home, ~, /var, or /boot (including their
children), transmitting secrets, unreviewed remote code execution, destructive
system changes, and privilege expansion are dangerous.

Return one JSON object only:
{"dangerous": true|false, "category": "one class or none", "reason": "short"}
"""


@dataclass(frozen=True)
class IntentDecision:
    action: str  # allow | warn | deny
    source: str  # deterministic | model | fail_closed
    category: str
    reason: str
    model_latency_ms: int = 0


def _decision(action: str, source: str, category: str, reason: str, latency: int = 0) -> IntentDecision:
    return IntentDecision(action, source, category, reason, latency)


def _scan_window(command: str) -> str:
    """Bound model/pattern input while always retaining a long command's tail."""
    if len(command) <= LONG_COMMAND_THRESHOLD:
        return command
    return command[:LONG_COMMAND_THRESHOLD] + "\n# [SEAL: middle omitted]\n" + command[-LONG_COMMAND_TAIL:]


_HEREDOC_START = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_UNQUOTED_HEREDOC = re.compile(r"<<-?\s*(?!['\"])[A-Za-z_][A-Za-z0-9_]*")


def _strip_heredoc_bodies(command: str) -> str:
    """Remove heredoc data so examples inside payloads are not treated as code."""
    kept: list[str] = []
    terminator: str | None = None
    for line in command.splitlines():
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        kept.append(line)
        match = _HEREDOC_START.search(line)
        if match:
            terminator = match.group(2)
    return "\n".join(kept)


_COMMAND_SPLIT = re.compile(r"&&|\|\||[;\n]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(?:\S+|'[^']*'|\"[^\"]*\")\s+")


def _strip_prefixes(segment: str) -> str:
    """Remove common wrappers without turning quoted data into execution."""
    value = segment.strip().lstrip("({").strip().lstrip("\\").strip()
    while True:
        old = value
        value = _ASSIGNMENT.sub("", value, count=1).strip()
        value = re.sub(r"^(?:time|nohup|command|exec)\s+", "", value, count=1, flags=re.I).strip()
        value = re.sub(
            r"^(?:sudo|doas)\s+(?:(?:-[ughpCRT]\s+\S+|--(?:user|group|host|prompt|chdir)\s+\S+|-\S+)\s+)*",
            "",
            value,
            count=1,
            flags=re.I,
        ).strip()
        value = re.sub(r"^(?:nice|ionice|stdbuf)\s+(?:(?:-\S+)(?:\s+\S+)?\s+)*", "", value, count=1, flags=re.I).strip()
        value = re.sub(r"^env\s+(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*", "", value, count=1, flags=re.I).strip()
        if value == old:
            return value


def _executable_segments(command: str) -> list[str]:
    clean = _strip_heredoc_bodies(command)
    segments: list[str] = []
    for raw in _COMMAND_SPLIT.split(clean):
        segment = raw.strip()
        if not segment or segment.startswith("#"):
            continue
        normalized = _strip_prefixes(segment)
        if re.match(r"^(?:echo|printf|grep)\b", normalized, re.I) and "|" not in normalized:
            continue
        segments.append(normalized)
    return segments


# Compatibility seam for the 50 regression cases written after the 1-Sep
# incident.  The intent layer adds safe provenance; it does not erase this API.
_VAR_RM = re.compile(
    r"(?:^|/)rm(?:\s+(?:-[A-Za-z]+|--[a-z-]+))*\s+-[A-Za-z]*[rR][A-Za-z]*"
    r"(?:\s+(?:-[A-Za-z]+|--[a-z-]+))*\s+[\"']?\$(?!\{[A-Za-z_][A-Za-z0-9_]*:\?)"
    r"|(?:^|/)rm(?:\s+--[a-z-]+)*\s+--recursive(?:\s+--[a-z-]+)*\s+[\"']?\$"
    r"(?!\{[A-Za-z_][A-Za-z0-9_]*:\?)",
    re.I,
)
_VAR_GLOB = re.compile(
    r"(?:rm\s+-[A-Za-z]*[rR][A-Za-z]*|rmdir)\s+[\"']?\$([A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})[^\"'\s]*[\"']?/\*",
    re.I,
)


def _temp_variable_is_guarded(command: str, name: str) -> bool:
    n = re.escape(name.strip("{}"))
    checks = (
        rf"\b{n}\s*=\s*[\"']?\$\(\s*mktemp\b[^)]*-d",
        rf"\b{n}\s*=\s*[\"']?\$\(\s*mktemp\s+-d\b",
        rf"case\s+[\"']?\${{{n}}}|case\s+[\"']?\${n}",
        rf"\[\[\s*[\"']?\${{{n}}}?[\"']?\s*==\s*/tmp/\*",
    )
    if any(re.search(pattern, command, re.I | re.S) for pattern in checks):
        # A case statement only counts when its admitted branch is /tmp/*.
        if re.search(rf"case\s+[^\n;]*\${{{n}}}?[^\n;]*\s+in\s+/tmp/\*", command, re.I | re.S):
            return True
        if re.search(rf"\[\[\s*[^\n;]*\${{{n}}}?[^\n;]*==\s*/tmp/\*", command, re.I):
            return True
        if re.search(rf"\b{n}\s*=\s*[\"']?\$\(\s*mktemp(?:\s+-d|\b[^)]*\s-d\b)", command, re.I):
            return True
    return False


def _var_destructive_kind(command: str) -> str:
    """Return ``deny``, ``warn`` or ``""`` for variable-path removal.

    A proven /tmp variable is allowed with a warning.  A bare variable/glob is
    denied.  Any prior segment lowers a non-glob variable to warning because it
    may contain a guard; the local model is deliberately not used to re-freeze
    this historically safe path.
    """
    clean = _strip_heredoc_bodies(command)
    segments = [raw.strip() for raw in _COMMAND_SPLIT.split(clean) if raw.strip() and not raw.strip().startswith("#")]
    ambiguous = False
    for index, raw in enumerate(segments):
        segment = _strip_prefixes(raw)
        if re.match(r"^(?:echo|printf|grep)\b", segment, re.I):
            continue
        segment = re.sub(r"^/(?:usr/)?s?bin/", "", segment)
        glob = _VAR_GLOB.search(segment)
        if glob:
            if _temp_variable_is_guarded(command, glob.group(1)):
                ambiguous = True
                continue
            return "deny"
        if _VAR_RM.match(segment):
            if index == 0:
                return "deny"
            ambiguous = True
    return "warn" if ambiguous else ""


_STATIC_DENY: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|fi)?sh\b", re.I), "remote_execution", "remote content piped directly to a shell"),
    (re.compile(r"(?:base64\s+(?:-d|--decode)|xxd\s+-r|openssl\s+enc\s+-d)\b[^|\n]*\|\s*(?:ba|z|fi)?sh\b", re.I), "remote_execution", "decoded content piped directly to a shell"),
    (re.compile(r"(?:ba|z|fi)?sh\s+<\(\s*(?:curl|wget)\b", re.I), "remote_execution", "remote content executed through process substitution"),
    (re.compile(r"\beval\s+[\"']?\$\(\s*(?:curl|wget)\b", re.I), "remote_execution", "remote content executed through eval"),
    (re.compile(r"\$\{!?[A-Za-z_][A-Za-z0-9_]*(?:@P|!)[^}]*\}", re.I), "remote_execution", "indirect shell expansion used as executable content"),
    (re.compile(r"(?:ba|z|fi)?sh\s+<<<\s*[\"']?\$\(", re.I), "remote_execution", "command substitution fed directly to a shell"),
    (re.compile(r":\(\)\s*\{\s*:\|:\s*&\s*\}\s*;?\s*:"), "destructive_operation", "fork bomb"),
    (re.compile(r"\bmkfs(?:\.[A-Za-z0-9]+)?\b"), "destructive_operation", "filesystem formatting"),
    (re.compile(r"\bdd\b[^\n;&|]*\bof\s*=\s*/dev/(?:sd|hd|nvme|vd)", re.I), "destructive_operation", "raw write to a block device"),
    (re.compile(r"\bDROP\s+(?:TABLE|DATABASE|SCHEMA)\b", re.I), "destructive_operation", "SQL DROP operation"),
    (re.compile(r"\bgit\s+push\b[^\n;&]*?(?:--force\b|-f\b)[^\n;&]*\b(?:main|master)\b", re.I), "destructive_operation", "force push to protected branch"),
    (re.compile(r"\bchmod\s+(?:-R\s+)?777\b", re.I), "privilege_escalation", "world-writable permissions"),
)

_CRITICAL_ROOTS = ("/usr", "/etc", "/home", "/var", "/boot")
_SAFE_RELATIVE_ROOTS = {
    "build",
    "dist",
    "node_modules",
    ".venv",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "coverage",
}
_RM_CALL = re.compile(r"(?:^|[\s(])(?:/(?:usr/)?bin/)?rm\b([^;&|\n]*)", re.I)


def _rm_tokens(argument_text: str) -> tuple[bool, list[str]]:
    try:
        tokens = shlex.split(argument_text, posix=True)
    except ValueError:
        tokens = argument_text.split()
    recursive = False
    targets: list[str] = []
    options_done = False
    for token in tokens:
        if not options_done and token == "--":
            options_done = True
            continue
        if not options_done and token.startswith("-"):
            if token == "--recursive" or (not token.startswith("--") and any(c in token[1:] for c in "rR")):
                recursive = True
            continue
        targets.append(token)
    return recursive, targets


def _critical_target(target: str) -> bool:
    t = target.rstrip("/") or "/"
    if t in {"/", "~", "$HOME", "${HOME}"} or t.startswith(("~/", "$HOME/", "${HOME}/")):
        return True
    return any(t == root or t.startswith(root + "/") for root in _CRITICAL_ROOTS)


def _safe_cleanup_target(target: str) -> bool:
    t = target.rstrip("/")
    if t.startswith("/tmp/") and t not in {"/tmp", "/tmp/.."} and "/../" not in t:
        return True
    while t.startswith("./"):
        t = t[2:]
    if not t or t == ".." or t.startswith("../") or "/../" in t:
        return False
    return t.split("/", 1)[0] in _SAFE_RELATIVE_ROOTS


def _rm_effect(command: str) -> tuple[str, str]:
    """Return (kind, reason): critical | routine | ambiguous | none."""
    saw_recursive = False
    saw_ambiguous = False
    for segment in _executable_segments(command):
        for match in _RM_CALL.finditer(segment):
            recursive, targets = _rm_tokens(match.group(1))
            if not recursive:
                continue
            saw_recursive = True
            for target in targets:
                if _critical_target(target):
                    return "critical", f"recursive deletion targets protected path {target!r}"
                if target.startswith("$"):
                    saw_ambiguous = True
                elif not _safe_cleanup_target(target):
                    saw_ambiguous = True
    if saw_ambiguous:
        return "ambiguous", "recursive deletion target is not a declared routine cleanup path"
    if saw_recursive:
        return "routine", "routine build or literal /tmp cleanup"
    return "none", ""


_RISK_SIGNAL = re.compile(
    r"\b(?:rm|rmdir|find|sudo|doas|su|curl|wget|ssh|scp|sftp|rsync|nc|ncat|socat|"
    r"eval|bash|zsh|sh|python|perl|ruby|dd|mkfs|kill|pkill|killall|systemctl|"
    r"chmod|chown|setcap|iptables|nft|mount|umount|docker|podman|kubectl|psql)\b|"
    r"(?:token|secret|password|passwd|credential|private[_ -]?key|\.env)",
    re.I,
)


def deterministic_decision(command: str) -> IntentDecision | None:
    """Return a final deterministic decision, or None for the model slow path."""
    window = _scan_window(command)
    if _UNQUOTED_HEREDOC.search(window):
        return _decision("deny", "deterministic", "remote_execution", "Unquoted heredoc can expand command substitutions before the payload is consumed")

    executable = "\n".join(_executable_segments(window))
    for pattern, category, reason in _STATIC_DENY:
        if pattern.search(executable):
            return _decision("deny", "deterministic", category, reason)

    effect, reason = _rm_effect(window)
    if effect == "critical":
        return _decision("deny", "deterministic", "destructive_operation", reason)

    variable_kind = _var_destructive_kind(window)
    if variable_kind == "deny":
        return _decision("deny", "deterministic", "destructive_operation", "recursive removal uses an unguarded variable path")
    if variable_kind == "warn":
        return _decision("warn", "deterministic", "destructive_operation", "variable-path cleanup has prior /tmp provenance or a structural guard; allowed without blocking")
    if effect == "routine":
        return _decision("allow", "deterministic", "none", reason)

    if not _RISK_SIGNAL.search(executable):
        return _decision("allow", "deterministic", "none", "no command-risk signal")
    return None


Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _endpoint_is_loopback(url: str) -> bool:
    """The command may contain secrets; it must never leave this host."""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }


def _http_transport(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def _parse_model_json(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict) or type(parsed.get("dangerous")) is not bool:
        raise ValueError("model answer lacks boolean dangerous")
    category = parsed.get("category")
    if category not in _CATEGORIES:
        raise ValueError("model answer has an unknown category")
    if parsed["dangerous"] == (category == "none"):
        raise ValueError("model answer contradicts dangerous/category")
    if not isinstance(parsed.get("reason"), str) or not parsed["reason"].strip():
        raise ValueError("model answer lacks a reason")
    return parsed


def classify_with_local_model(
    command: str,
    *,
    transport: Transport | None = None,
    timeout: float = MODEL_TIMEOUT_SECONDS,
) -> IntentDecision:
    """Classify one ambiguous command; every failure is a denial."""
    if not _endpoint_is_loopback(MODEL_ENDPOINT):
        return _decision(
            "deny",
            "fail_closed",
            "exfiltration",
            "local intent classifier endpoint is not loopback",
        )
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _scan_window(command)},
        ],
        "temperature": 0,
        "max_tokens": 120,
        # llama.cpp only constrains primitive types when the JSON schema is
        # supplied.  ``type=json_object`` alone let Gemma emit "false" as a
        # string, which correctly failed closed but froze benign commands.
        "response_format": {"type": "json_object", "schema": _MODEL_JSON_SCHEMA},
    }
    started = time.monotonic()
    try:
        raw = (transport or _http_transport)(MODEL_ENDPOINT, payload, timeout)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > int(timeout * 1000):
            raise TimeoutError(f"classifier exceeded {timeout:.1f}s")
        content = raw["choices"][0]["message"]["content"]
        answer = _parse_model_json(content)
        category = str(answer.get("category", "none")).strip().lower().replace(" ", "_")
        if category not in _CATEGORIES:
            category = "none" if not answer["dangerous"] else "destructive_operation"
        reason = str(answer.get("reason", "local model decision"))[:300]
        action = "deny" if answer["dangerous"] else "allow"
        return _decision(action, "model", category, reason, elapsed_ms)
    except Exception as exc:  # fail-closed is the contract, not a silent fallback
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return _decision(
            "deny",
            "fail_closed",
            "destructive_operation",
            f"local intent classifier unavailable or invalid: {type(exc).__name__}",
            elapsed_ms,
        )


def evaluate_command(command: str, *, transport: Transport | None = None) -> IntentDecision:
    if not isinstance(command, str) or not command.strip():
        return _decision("allow", "deterministic", "none", "empty command")
    quick = deterministic_decision(command)
    if quick is not None:
        return quick
    if os.environ.get("SEAL_COMMAND_GUARD_MODEL_ENABLED", "1").strip().lower() in {"0", "false", "no"}:
        return _decision("deny", "fail_closed", "destructive_operation", "ambiguous risky command while local classifier is disabled")
    return classify_with_local_model(command, transport=transport)


def audit_decision(command: str, decision: IntentDecision) -> None:
    """Append a redacted receipt; inability to audit never changes the decision."""
    if os.environ.get("SEAL_COMMAND_GUARD_AUDIT", "1").strip().lower() in {"0", "false", "no"}:
        return
    configured = os.environ.get("SEAL_COMMAND_GUARD_AUDIT_PATH", "")
    path = Path(configured) if configured else Path.home() / ".local/state/seal/command_intent_guard.jsonl"
    row = {
        "ts_epoch": time.time(),
        "command_sha256": hashlib.sha256(command.encode("utf-8", "replace")).hexdigest(),
        "command_length": len(command),
        **asdict(decision),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, (json.dumps(row, ensure_ascii=True) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except OSError:
        pass
