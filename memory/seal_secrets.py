"""Local secret loading helpers for legacy SEAL consumers.

Environment variables remain authoritative.  The compatibility file is read
only when it is a private, owner-controlled regular file; it is deliberately
not a shell or dotenv interpreter.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path
from urllib.parse import quote

_CREDENTIALS_PATH = Path.home() / ".config" / "seal" / "credentials.env"
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_CREDENTIAL_BYTES = 64 * 1024
_LOADED = False


def _read_private_text(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError("SEAL credentials must be a readable non-symlink file") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("SEAL credentials must be a regular file")
        if info.st_uid != os.geteuid():
            raise RuntimeError("SEAL credentials must be owned by the current uid")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise RuntimeError("SEAL credentials must have mode 0600 or stricter")
        raw = os.read(fd, _MAX_CREDENTIAL_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > _MAX_CREDENTIAL_BYTES:
        raise RuntimeError("SEAL credentials file exceeds the size limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("SEAL credentials must be valid UTF-8") from exc


def _parse_value(raw: str, line_number: int) -> str:
    value = raw.strip()
    if not value:
        return ""
    starts_quote = value[0] in {"'", '"'}
    ends_quote = value[-1] in {"'", '"'}
    if starts_quote or ends_quote:
        if not (starts_quote and ends_quote and value[0] == value[-1]):
            raise RuntimeError(f"Malformed quoted credential value at line {line_number}")
        value = value[1:-1]
    if "\x00" in value or "\r" in value or "\n" in value:
        raise RuntimeError(f"Credential value contains a control character at line {line_number}")
    return value


def load_credentials_file(path: Path = _CREDENTIALS_PATH) -> None:
    """Populate ``os.environ`` from strict ``KEY=VALUE`` lines.

    Existing environment values are never overwritten.  Shell syntax,
    interpolation, escapes, exports and inline comments are intentionally not
    interpreted.
    """
    global _LOADED
    if _LOADED:
        return
    try:
        text = _read_private_text(path)
    except FileNotFoundError:
        return

    parsed: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"Malformed SEAL credential line {line_number}")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not _KEY_RE.fullmatch(key):
            raise RuntimeError(f"Invalid SEAL credential key at line {line_number}")
        if key in seen:
            raise RuntimeError(f"Duplicate SEAL credential key at line {line_number}")
        seen.add(key)
        parsed.append((key, _parse_value(raw_value, line_number)))

    for key, value in parsed:
        if key not in os.environ:
            os.environ[key] = value
    _LOADED = True


def get_secret(*names: str, default: str | None = None, required: bool = False) -> str | None:
    load_credentials_file()
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    if required:
        joined = ", ".join(names)
        raise RuntimeError(f"Missing required SEAL secret/env: {joined}")
    return default


def _validated_port(raw: str | None) -> str:
    value = (raw or "5433").strip()
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise RuntimeError("PG_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("PG_PORT must be between 1 and 65535")
    return str(port)


def _validated_host(raw: str | None) -> str:
    host = (raw or "localhost").strip()
    if not host or any(char in host for char in "/@?#\x00\r\n"):
        raise RuntimeError("PG_HOST is invalid")
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        host = f"[{host}]"
    return host


def pg_dsn(required: bool = True) -> str:
    """Return the configured PostgreSQL DSN without a privileged default.

    A complete DSN supplied through the historical environment names remains
    compatible.  Component-based configuration requires an explicit
    ``PG_USER`` and percent-encodes user, password and database components.
    """
    direct = get_secret("SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN")
    if direct:
        return direct

    password = get_secret("PG_PASSWORD", "SEAL_DB_PASS", required=required)
    if not password:
        return ""
    user = get_secret("PG_USER", required=True)
    database = get_secret("PG_DATABASE", default="seal_memory") or "seal_memory"
    host = _validated_host(get_secret("PG_HOST", default="localhost"))
    port = _validated_port(get_secret("PG_PORT", default="5433"))
    return (
        f"postgresql://{quote(user or '', safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{quote(database, safe='')}"
    )
