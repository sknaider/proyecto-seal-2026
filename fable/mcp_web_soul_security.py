#!/usr/bin/env python3
"""Seguridad nativa compartida para el control plane de ``mcp-web-soul``.

No depende de Playwright ni de auto-browser. Centraliza el contrato que deben
usar tanto el wrapper MCP como el futuro SessionManager: audit append-only
encadenado, redacción de secretos y confinamiento de artifacts.
"""

from __future__ import annotations

import fcntl
import grp
import hashlib
import hmac
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from mcp_web_soul_witness import WitnessClient, WitnessError


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

    def __init__(
        self,
        path: str | Path,
        *,
        agent: str | None = None,
        witness_socket: str | Path | None = None,
        witness_required: bool = False,
        reader_group: str | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.agent = str(agent or os.environ.get("SEAL_AGENT") or "unknown")
        self.witness = WitnessClient(witness_socket) if witness_socket else None
        self.witness_required = bool(witness_required)
        self.reader_group = str(reader_group).strip() if reader_group else None
        self.reader_gid = grp.getgrnam(self.reader_group).gr_gid if self.reader_group else None
        # El JSONL puede recibir eventos de varios agentes bajo el mismo lock; el
        # stream autoritativo es el archivo, no el nombre del escritor.
        stream_material = str(self.path).encode("utf-8")
        self.stream = "audit:" + hashlib.sha256(stream_material).hexdigest()

    def _secure_storage(self) -> int:
        """Create the audit path with either private or read-only group access."""

        directory_mode = 0o2750 if self.reader_gid is not None else 0o700
        file_mode = 0o640 if self.reader_gid is not None else 0o600
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=directory_mode)
        if self.reader_gid is not None:
            os.chown(self.path.parent, -1, self.reader_gid)
        os.chmod(self.path.parent, directory_mode)
        return file_mode

    def _secure_file(self, mode: int) -> None:
        if self.reader_gid is not None:
            os.chown(self.path, -1, self.reader_gid)
        os.chmod(self.path, mode)

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

    @classmethod
    def _state(cls, handle) -> tuple[str, int]:
        handle.seek(0)
        previous = cls.ZERO_HASH
        count = 0
        for line in handle:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError("audit tail corrupto") from exc
            event_hash = record.pop("event_hash", "")
            if record.get("previous_hash") != previous or cls._hash(record) != event_hash:
                raise ValueError("audit chain corrupta")
            previous = str(event_hash)
            count += 1
        return previous, count

    def _reconcile_from_witness(self, handle, previous: str, count: int) -> tuple[str, int]:
        """Recover witness-committed/local-unflushed records before new work.

        The witness is authoritative only for the monotonic suffix it stores in
        full. Legacy seeded heads intentionally remain fail-closed if their full
        records are unavailable.
        """

        if self.witness is None:
            return previous, count
        head = self.witness.request({"op": "head", "stream": self.stream})
        witnessed_count = int(head["sequence"])
        witnessed_head = str(head["head_hash"])
        if witnessed_count < count:
            raise WitnessError("local audit is ahead of external witness")
        if witnessed_count == count:
            if not hmac.compare_digest(witnessed_head, previous):
                raise WitnessError("local audit head conflicts with external witness")
            return previous, count

        recovered: list[dict[str, Any]] = []
        cursor = previous
        for sequence in range(count + 1, witnessed_count + 1):
            response = self.witness.request({
                "op": "event", "stream": self.stream, "sequence": sequence,
            })
            record = response.get("record")
            if not isinstance(record, dict):
                raise WitnessError("witness recovery record missing")
            unhashed = dict(record)
            event_hash = str(unhashed.pop("event_hash", ""))
            if (
                unhashed.get("previous_hash") != cursor
                or not re.fullmatch(r"[0-9a-f]{64}", event_hash)
                or not hmac.compare_digest(self._hash(unhashed), event_hash)
            ):
                raise WitnessError("witness recovery chain corrupt")
            cursor = event_hash
            recovered.append(record)
        if not hmac.compare_digest(cursor, witnessed_head):
            raise WitnessError("witness recovery head mismatch")

        handle.seek(0, os.SEEK_END)
        for record in recovered:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        return cursor, witnessed_count

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
            file_mode = self._secure_storage()
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, file_mode)
            self._secure_file(file_mode)
            with os.fdopen(fd, "r+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                previous_hash, previous_count = self._state(handle)
                previous_hash, previous_count = self._reconcile_from_witness(
                    handle, previous_hash, previous_count,
                )
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
                if self.witness is not None:
                    self.witness.request({
                        "op": "append",
                        "stream": self.stream,
                        "sequence": previous_count + 1,
                        "previous_hash": previous_hash,
                        "head_hash": record["event_hash"],
                        "record": record,
                    })
                handle.seek(0, os.SEEK_END)
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                return record
        except (OSError, ValueError, WitnessError):
            if required or self.witness_required:
                raise
            return None

    def verify(self) -> tuple[bool, int]:
        previous = self.ZERO_HASH
        count = 0
        # Un archivo ausente representa el estado local vacio, no una prueba de
        # integridad. Si existe un witness, SIEMPRE debe comparar ese estado
        # (seq=0/ZERO_HASH) con la cabeza externa; de lo contrario borrar el
        # JSONL completo convertiria una manipulacion en un falso-verde.
        if self.path.exists():
            try:
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
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return False, count
        if self.witness is not None:
            try:
                self.witness.request({
                    "op": "verify", "stream": self.stream,
                    "sequence": count, "head_hash": previous,
                })
            except (OSError, WitnessError, json.JSONDecodeError):
                return False, count
        return True, count
