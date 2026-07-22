"""Fail-closed PostgreSQL credential loader for operational services.

Each service gets one dedicated environment variable.  The value may be
provided directly or through ``<ENV_NAME>_FILE``.  Secret files must be
regular, owned by the current uid, and inaccessible to group/other users.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit


class OperationalCredentialError(RuntimeError):
    """Credential configuration is missing or violates the local contract."""


def _read_private_file(path_value: str, env_name: str) -> str:
    path = Path(path_value).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise OperationalCredentialError(
            f"{env_name}_FILE must be a readable regular non-symlink file"
        ) from exc
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            metadata = os.fstat(handle.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise OperationalCredentialError(
                    f"{env_name}_FILE must be a regular non-symlink file"
                )
            if metadata.st_uid != os.geteuid():
                raise OperationalCredentialError(
                    f"{env_name}_FILE must be owned by the service uid"
                )
            if stat.S_IMODE(metadata.st_mode) & 0o077:
                raise OperationalCredentialError(
                    f"{env_name}_FILE must have mode 0600 or stricter"
                )
            value = handle.read().strip()
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    if not value:
        raise OperationalCredentialError(f"{env_name}_FILE is empty")
    return value


def _read_transition_dsn(env_name: str, database: str | None) -> str:
    """Read the pre-cutover shared DSN only from the existing 0600 store."""

    path = os.environ.get(
        "SEAL_OPERATIONAL_TRANSITION_FILE",
        str(Path.home() / ".config" / "seal" / "credentials.env"),
    )
    raw = _read_private_file(path, "SEAL_OPERATIONAL_TRANSITION")
    values: dict[str, str] = {}
    for item in raw.splitlines():
        line = item.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    dsn = next(
        (
            values[name]
            for name in (env_name, "SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN")
            if values.get(name)
        ),
        "",
    )
    if not dsn:
        raise OperationalCredentialError(
            "0600 transition store has no operational PostgreSQL DSN"
        )
    if database:
        parsed = urlsplit(dsn)
        dsn = urlunsplit(
            (parsed.scheme, parsed.netloc, f"/{database}", parsed.query, parsed.fragment)
        )
    return dsn


def service_pg_dsn(
    env_name: str,
    *,
    expected_role: str | None = None,
    allow_private_transition: bool = False,
    transition_database: str | None = None,
) -> str:
    """Load and validate a service-specific PostgreSQL DSN.

    Exactly one of ``env_name`` or ``env_name + '_FILE'`` must be set.  The
    role check prevents an operator from accidentally wiring a runtime back
    to the shared/superuser identity during cutover.
    """

    direct = os.environ.get(env_name, "").strip()
    file_path = os.environ.get(f"{env_name}_FILE", "").strip()
    if os.environ.get("SEAL_REQUIRE_DEDICATED_OPERATIONAL_DSN") == "1":
        allow_private_transition = False
    if direct and file_path:
        raise OperationalCredentialError(
            f"set only one of {env_name} or {env_name}_FILE"
        )
    transition = False
    if not direct and not file_path and allow_private_transition:
        direct = _read_transition_dsn(env_name, transition_database)
        transition = True
    if not direct and not file_path:
        raise OperationalCredentialError(
            f"missing required {env_name} or {env_name}_FILE"
        )
    dsn = direct or _read_private_file(file_path, env_name)
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise OperationalCredentialError(f"{env_name} is not a PostgreSQL DSN")
    actual_role = unquote(parsed.username or "")
    if expected_role and actual_role != expected_role and not (
        transition and actual_role == "seal"
    ):
        raise OperationalCredentialError(
            f"{env_name} must authenticate as dedicated role {expected_role}"
        )
    if parsed.password is None:
        raise OperationalCredentialError(f"{env_name} must contain a password")
    return dsn
