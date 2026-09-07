from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import soul_memory_sdk_onboard as onboard  # noqa: E402
import soul_memory_sdk_onboard_recover as recover  # noqa: E402


TENANT_ID = "12345678-1234-4abc-8def-1234567890ab"
RAW_KEY = "sk-soul-RECOVERY-TEST-ONLY"


def _pending(tmp_path: Path) -> Path:
    request = onboard.build_request(
        tenant_id=TENANT_ID,
        tenant_name="Acme Health",
        scopes=["read", "write"],
        agents=["ADA"],
        key_label="acme-prod-1",
        plan_id="pro",
        expires_at="2027-01-01T00:00:00+00:00",
        ttl_seconds=None,
    )
    record = onboard.build_expected_key_record(request, RAW_KEY, "William")
    path, fd = onboard.reserve_output_file(str(tmp_path / "pending.json"))
    onboard.write_pending_raw_key_file(fd, request, RAW_KEY, record)
    os.close(fd)
    return path


def test_secure_pending_read_and_request_reconstruction(tmp_path: Path) -> None:
    path = _pending(tmp_path)
    before = path.stat()
    payload = recover.read_pending_escrow(str(path))
    request, raw, record = recover.request_from_pending(payload)
    after = path.stat()
    assert request.tenant_id.hex == TENANT_ID.replace("-", "")
    assert raw == RAW_KEY
    assert record["created_by"] == "William"
    assert stat.S_IMODE(after.st_mode) == 0o600
    assert (before.st_ino, before.st_size, before.st_mtime_ns) == (
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    )


def test_recovery_rejects_symlink_and_non_0600(tmp_path: Path) -> None:
    path = _pending(tmp_path)
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(onboard.TenantOnboardingError, match="symlink"):
        recover.read_pending_escrow(str(link))
    path.chmod(0o640)
    with pytest.raises(onboard.TenantOnboardingError, match="owned_regular_0600"):
        recover.read_pending_escrow(str(path))


def test_recovery_rejects_tampered_record(tmp_path: Path) -> None:
    path = _pending(tmp_path)
    payload = json.loads(path.read_text())
    payload["expected_key_record"]["sha256"] = "0" * 64
    path.write_text(json.dumps(payload))
    path.chmod(0o600)
    with pytest.raises(onboard.TenantOnboardingError, match="exact_key_record_mismatch"):
        recover.request_from_pending(recover.read_pending_escrow(str(path)))


@pytest.mark.asyncio
async def test_inspect_pending_is_read_only_and_never_returns_raw(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _pending(tmp_path)
    before = path.read_bytes()

    class Conn:
        async def close(self) -> None:
            return None

    async def connect(_dsn: str) -> Conn:
        return Conn()

    async def inspect(_conn, request, raw_key, expected_record):
        assert raw_key == RAW_KEY
        assert expected_record["created_by"] == "William"
        return "committed", {"first_key": {"raw_key": raw_key}}

    monkeypatch.setattr(recover, "service_pg_dsn", lambda *_: "postgresql://svc_soul_sdk_onboard_william:x@localhost/db")
    monkeypatch.setattr(recover, "_connect", connect)
    monkeypatch.setattr(recover, "_inspect_outcome", inspect)
    result = await recover.inspect_pending(str(path))
    assert result["exact_committed_state"] is True
    assert RAW_KEY not in json.dumps(result)
    assert path.read_bytes() == before
