from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from seal_identity_tokens import SCHEMA, token_owner, valid_tokens


NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _slot(root: Path, agent: str, slot: str, token: str, expires: datetime) -> None:
    suffix = ".token" if slot == "CURRENT" else ".token.next"
    token_path = root / f"{agent}{suffix}"
    meta_path = root / f"{agent}{suffix}.meta.json"
    root.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    token_path.chmod(0o600)
    meta_path.write_text(
        json.dumps(
            {
                "schema": SCHEMA,
                "agent": agent,
                "mode": slot,
                "generation": 1 if slot == "CURRENT" else 2,
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                "expires_at": expires.isoformat(),
                "enforced": True,
            }
        ),
        encoding="utf-8",
    )
    meta_path.chmod(0o600)


def test_enforce_accepts_current_and_next(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEAL_TOKEN_LIFECYCLE_MODE", "ENFORCE")
    _slot(tmp_path, "ADA", "CURRENT", "current-secret", NOW + timedelta(hours=1))
    _slot(tmp_path, "ADA", "NEXT", "next-secret", NOW + timedelta(hours=2))
    assert valid_tokens([tmp_path], "ADA", now=NOW) == (
        "current-secret",
        "next-secret",
    )
    assert token_owner([tmp_path], ["ADA"], "next-secret", now=NOW) == "ADA"


def test_enforce_rejects_expired_mismatch_and_revoked(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEAL_TOKEN_LIFECYCLE_MODE", "ENFORCE")
    _slot(tmp_path, "ADA", "CURRENT", "expired", NOW - timedelta(seconds=1))
    assert valid_tokens([tmp_path], "ADA", now=NOW) == ()

    _slot(tmp_path, "ADA", "CURRENT", "live", NOW + timedelta(hours=1))
    meta_path = tmp_path / "ADA.token.meta.json"
    meta = json.loads(meta_path.read_text())
    meta["revoked_at"] = NOW.isoformat()
    meta_path.write_text(json.dumps(meta))
    meta_path.chmod(0o600)
    assert valid_tokens([tmp_path], "ADA", now=NOW) == ()


def test_runtime_only_excludes_legacy_directory(monkeypatch, tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    legacy = tmp_path / "legacy"
    _slot(runtime, "ADA", "CURRENT", "runtime", NOW + timedelta(hours=1))
    _slot(legacy, "ADA", "CURRENT", "legacy", NOW + timedelta(hours=1))
    monkeypatch.setenv("SEAL_TOKEN_LIFECYCLE_MODE", "ENFORCE")
    assert valid_tokens([runtime], "ADA", now=NOW) == ("runtime",)
