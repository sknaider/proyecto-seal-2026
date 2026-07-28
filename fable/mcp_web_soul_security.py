#!/usr/bin/env python3
"""Seguridad nativa compartida para el control plane de ``mcp-web-soul``.

No depende de Playwright ni de auto-browser. Centraliza el contrato que deben
usar tanto el wrapper MCP como el futuro SessionManager: audit append-only
encadenado, redacción de secretos y confinamiento de artifacts.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


_SENSITIVE_KEY = re.compile(
    r"(?i)(authorization|proxy[-_]?authorization|cookie|set[-_]?cookie|"
    r"password|passwd|secret|token|api[-_]?key|credential|session[-_]?key)"
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(authorization|cookie|password|passwd|secret|token|api[-_]?key)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")

_SAFE_HEADERS = {
    "accept",
    "accept-language",
    "cache-control",
    "content-length",
    "content-type",
    "etag",
    "last-modified",
    "location",
    "origin",
    "referer",
    "retry-after",
    "server",
    "user-agent",
    "vary",
}


def sanitize_text(value: Any) -> str:
    """Redacta secretos comunes en texto libre sin registrar su hash."""

    text = str(value or "")
    text = _BEARER.sub("Bearer [redacted]", text)
    return _SENSITIVE_TEXT.sub(lambda m: f"{m.group(1)}=[redacted]", text)


def sanitize_url(value: Any) -> str:
    """Conserva origen+ruta y elimina userinfo, query, fragment y data bodies."""

    raw = str(value or "")
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return "[redacted-url]"
    scheme = parsed.scheme.lower()
    if scheme in {"data", "blob"}:
        return f"{scheme}:[redacted]"
    if scheme == "about":
        return "about:blank" if parsed.path == "blank" else "about:[redacted]"
    if scheme not in {"http", "https"}:
        return f"{scheme}:[redacted]" if scheme else "[redacted-url]"
    try:
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
        port = parsed.port
    except (UnicodeError, ValueError):
        return f"{scheme}://[redacted]"
    if not host:
        return f"{scheme}://[redacted]"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urllib.parse.urlunsplit((scheme, netloc, parsed.path or "/", "", ""))


def _redacted_metadata(value: Any) -> dict[str, Any]:
    """Describe el valor sin hacerlo recuperable mediante diccionario de hashes."""

    raw = str(value or "")
    return {"redacted": True, "length": len(raw)}


def sanitize_value(key: str, value: Any, *, tool: str = "") -> Any:
    lowered = str(key).lower()
    tool = str(tool).lower().split(".", 1)[0]
    if _SENSITIVE_KEY.search(lowered):
        return _redacted_metadata(value)
    if tool in {"type_text", "set_cookie"} and lowered in {"value", "text"}:
        return _redacted_metadata(value)
    if tool == "browser_action" and lowered in {"value", "text", "body", "payload"}:
        return _redacted_metadata(value)
    if "url" in lowered or lowered in {"location", "referer"}:
        return sanitize_url(value)
    if isinstance(value, Mapping):
        return sanitize_mapping(value, tool=tool)
    if isinstance(value, (list, tuple)):
        return [sanitize_value(key, item, tool=tool) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(value)[:500]


def sanitize_mapping(values: Mapping[str, Any], *, tool: str = "") -> dict[str, Any]:
    return {
        str(key): sanitize_value(str(key), value, tool=tool)
        for key, value in values.items()
    }


def sanitize_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Allowlist de headers diagnósticos; los de autenticación nunca se copian."""

    safe: dict[str, str] = {}
    for key, value in (headers or {}).items():
        lowered = str(key).lower()
        if _SENSITIVE_KEY.search(lowered):
            continue
        if lowered.startswith("access-control-") or lowered in _SAFE_HEADERS:
            if lowered in {"location", "referer"}:
                safe[str(key)] = sanitize_url(value)
            else:
                safe[str(key)] = sanitize_text(value)[:500]
    return safe


def sanitize_network_records(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        safe: dict[str, Any] = {}
        for key, value in record.items():
            lowered = str(key).lower()
            if lowered in {"req_headers", "resp_headers", "headers"}:
                safe[key] = sanitize_headers(value if isinstance(value, Mapping) else {})
            elif lowered in {"sent_cookies", "cookies", "associatedcookies"}:
                safe[key] = {"redacted": True, "count": len(value or [])}
            elif lowered == "sent_cookie":
                safe[key] = bool(value)
            else:
                safe[key] = sanitize_value(str(key), value, tool="network")
        out.append(safe)
    return out


def resolve_artifact_path(requested: str, root: str | Path) -> Path:
    """Resuelve una ruta relativa dentro de ``root`` y rechaza traversal/symlinks."""

    base = Path(root).expanduser().resolve()
    candidate_input = Path(str(requested))
    if candidate_input.is_absolute():
        candidate = candidate_input.expanduser().resolve()
    else:
        candidate = (base / candidate_input).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError("artifact path fuera del root gestionado") from exc
    if candidate == base:
        raise ValueError("artifact path debe nombrar un archivo")
    return candidate


class AuditTrail:
    """JSONL 0600 con lock interproceso y hash-chain SHA-256."""

    ZERO_HASH = "0" * 64

    def __init__(self, path: str | Path, *, agent: str | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.agent = str(agent or os.environ.get("SEAL_AGENT") or "unknown")

    @staticmethod
    def _hash(record: Mapping[str, Any]) -> str:
        raw = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _last_hash(handle) -> str:
        handle.seek(0)
        last = ""
        for line in handle:
            if line.strip():
                last = line
        if not last:
            return AuditTrail.ZERO_HASH
        try:
            candidate = json.loads(last).get("event_hash", "")
        except json.JSONDecodeError as exc:
            raise ValueError("audit tail corrupto") from exc
        if not re.fullmatch(r"[0-9a-f]{64}", str(candidate)):
            raise ValueError("audit tail sin hash válido")
        return str(candidate)

    def append(
        self,
        *,
        tool: str,
        arguments: Mapping[str, Any] | None,
        ok: bool,
        session_id: str = "",
        action_class: str = "READ",
        error: str | None = None,
        required: bool = False,
    ) -> dict[str, Any] | None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            os.chmod(self.path, 0o600)
            with os.fdopen(fd, "r+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                previous_hash = self._last_hash(handle)
                record: dict[str, Any] = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "agent": self.agent,
                    "session_id": sanitize_text(session_id)[:128],
                    "tool": str(tool),
                    "action_class": str(action_class),
                    "arguments": sanitize_mapping(arguments or {}, tool=str(tool)),
                    "ok": bool(ok),
                    "error": sanitize_text(error)[:500] if error else None,
                    "previous_hash": previous_hash,
                }
                record["event_hash"] = self._hash(record)
                handle.seek(0, os.SEEK_END)
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                return record
        except (OSError, ValueError):
            if required:
                raise
            return None

    def verify(self) -> tuple[bool, int]:
        if not self.path.exists():
            return True, 0
        previous = self.ZERO_HASH
        count = 0
        with self.path.open(encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                event_hash = record.pop("event_hash", "")
                if record.get("previous_hash") != previous or self._hash(record) != event_hash:
                    return False, count
                previous = event_hash
                count += 1
        return True, count
