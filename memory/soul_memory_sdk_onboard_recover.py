#!/usr/bin/env python3
"""Read-only diagnosis for a pending SOUL SDK onboarding escrow.

This command never changes PostgreSQL or the escrow file. It proves whether
the exact tenant/key/subscription/role state committed after an ambiguous
client outcome, while keeping the raw key out of stdout and logs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

from operational_db_credentials import service_pg_dsn
from soul_memory_sdk_onboard import (
    TenantOnboardingError,
    _assert_dsn_operator_login,
    _connect,
    _inspect_outcome,
    _record_matches_request,
    build_request,
)

MAX_ESCROW_BYTES = 64 * 1024


def read_pending_escrow(path_value: str) -> dict[str, Any]:
    """Read one owner-only regular escrow without following symlinks."""
    path = Path(path_value).expanduser()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise TenantOnboardingError("pending_file_unreadable_or_symlink") from exc
    try:
        metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size <= 0
            or metadata.st_size > MAX_ESCROW_BYTES
        ):
            raise TenantOnboardingError("pending_file_must_be_owned_regular_0600_bounded")
        data = bytearray()
        while len(data) <= MAX_ESCROW_BYTES:
            chunk = os.read(fd, min(8192, MAX_ESCROW_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_ESCROW_BYTES:
            raise TenantOnboardingError("pending_file_too_large")
    finally:
        os.close(fd)
    try:
        payload = json.loads(bytes(data))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TenantOnboardingError("pending_file_invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("state") != "pending_database_outcome":
        raise TenantOnboardingError("pending_file_state_required")
    return dict(payload)


def request_from_pending(payload: dict[str, Any]) -> tuple[Any, str, dict[str, Any]]:
    raw_key = payload.get("raw_api_key")
    record = payload.get("expected_key_record")
    if not isinstance(raw_key, str) or not raw_key or not isinstance(record, dict):
        raise TenantOnboardingError("pending_file_missing_exact_key_material")
    request = build_request(
        tenant_id=payload.get("tenant_id", ""),
        tenant_name=str(payload.get("tenant_name", "")),
        scopes=record.get("scopes") or [],
        agents=record.get("agents") or [],
        key_label=str(record.get("label") or ""),
        plan_id=str(payload.get("plan_id") or ""),
        expires_at=str(record.get("expires_at") or "") or None,
        ttl_seconds=None,
    )
    operator = str(record.get("created_by") or "")
    if not _record_matches_request(record, request, raw_key, operator):
        raise TenantOnboardingError("pending_file_exact_key_record_mismatch")
    return request, raw_key, dict(record)


async def inspect_pending(path_value: str) -> dict[str, Any]:
    payload = read_pending_escrow(path_value)
    request, raw_key, expected_record = request_from_pending(payload)
    dsn = service_pg_dsn("SOUL_MEMORY_ONBOARDING_DSN")
    _assert_dsn_operator_login(dsn)
    conn = await _connect(dsn)
    try:
        outcome, _recovered = await _inspect_outcome(conn, request, raw_key, expected_record)
    finally:
        await conn.close()
    return {
        "mode": "read-only-pending-inspection",
        "tenant_id": str(request.tenant_id),
        "outcome": outcome,
        "exact_committed_state": outcome == "committed",
        "escrow_state": "pending_database_outcome",
        "file_modified": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only inspection of pending onboarding escrow.")
    parser.add_argument("--pending-file", required=True)
    return parser


def main() -> None:
    result = asyncio.run(inspect_pending(build_parser().parse_args().pending_file))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
