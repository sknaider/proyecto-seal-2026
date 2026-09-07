"""Fail-closed database configuration for SEAL Studio.

The Studio process must authenticate directly as its restricted PostgreSQL
login.  A missing or mis-scoped DSN is a startup failure; it must never fall
back to the ``seal`` superuser.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit


ENV_NAME = "SEAL_STUDIO_DB_DSN"
EXPECTED_LOGIN = "svc_seal_studio"


def required_studio_dsn() -> str:
    dsn = os.environ.get(ENV_NAME, "").strip()
    if not dsn:
        raise RuntimeError(f"{ENV_NAME} is required; SEAL Studio fails closed")
    parsed = urlsplit(dsn)
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError(f"{ENV_NAME} must be a PostgreSQL DSN")
    if parsed.username != EXPECTED_LOGIN:
        raise RuntimeError(
            f"{ENV_NAME} must authenticate as {EXPECTED_LOGIN}; refusing broader login"
        )
    return dsn


DB_URL = required_studio_dsn()
