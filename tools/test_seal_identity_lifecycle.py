from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import seal_identity_lifecycle as lifecycle


def _token(directory: Path, agent: str, value: str = "synthetic-secret") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{agent}.token"
    path.write_text(value, encoding="utf-8")
    path.chmod(0o600)
    return path


def test_bootstrap_shadow_adds_metadata_without_changing_token(tmp_path: Path) -> None:
    token = _token(tmp_path, "ADA")
    before = token.read_bytes()
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    rows = lifecycle.bootstrap_shadow(tmp_path, ["ADA"], ttl_hours=24, now=now)
    assert token.read_bytes() == before
    meta_path = tmp_path / "ADA.token.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert stat.S_IMODE(meta_path.stat().st_mode) == 0o600
    assert meta["mode"] == "SHADOW"
    assert meta["enforced"] is False
    assert meta["expires_at"] == "2026-07-27T00:00:00+00:00"
    assert rows[0]["action"] == "created"


def test_bootstrap_is_idempotent_and_detects_token_drift(tmp_path: Path) -> None:
    token = _token(tmp_path, "ADA")
    lifecycle.bootstrap_shadow(tmp_path, ["ADA"])
    assert lifecycle.bootstrap_shadow(tmp_path, ["ADA"])[0]["action"] == "kept"
    token.write_text("different-secret", encoding="utf-8")
    with pytest.raises(RuntimeError, match="token_metadata_mismatch"):
        lifecycle.bootstrap_shadow(tmp_path, ["ADA"])


def test_private_reader_rejects_world_readable_and_symlink(tmp_path: Path) -> None:
    token = _token(tmp_path, "ADA")
    token.chmod(0o644)
    with pytest.raises(RuntimeError, match="not_private"):
        lifecycle.bootstrap_shadow(tmp_path, ["ADA"])
    target = _token(tmp_path, "REAL")
    link = tmp_path / "NEXUS.token"
    link.symlink_to(target)
    with pytest.raises((OSError, RuntimeError)):
        lifecycle.bootstrap_shadow(tmp_path, ["NEXUS"])


def test_audit_never_emits_token_bytes(tmp_path: Path) -> None:
    secret = "never-emit-this-token"
    runtime = tmp_path / "runtime"
    legacy = tmp_path / "legacy"
    _token(runtime, "ADA", secret)
    _token(legacy, "ADA", secret)
    report = lifecycle.audit([runtime, legacy], ["ADA"])
    encoded = json.dumps(report)
    assert secret not in encoded
    assert all(row["copies_equal"] is True for row in report["rows"])
    assert report["same_uid_isolation"] is False


def test_promote_current_enables_metadata_without_rotating_token(tmp_path: Path) -> None:
    token = _token(tmp_path, "ADA")
    before = token.read_bytes()
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    lifecycle.bootstrap_shadow(tmp_path, ["ADA"], ttl_hours=24, now=now)
    rows = lifecycle.promote_current(
        tmp_path,
        ["ADA"],
        now=datetime(2026, 7, 26, 1, tzinfo=timezone.utc),
    )
    meta = json.loads((tmp_path / "ADA.token.meta.json").read_text(encoding="utf-8"))
    assert token.read_bytes() == before
    assert meta["mode"] == "CURRENT"
    assert meta["enforced"] is True
    assert rows[0]["action"] == "promoted"


def test_stage_next_is_idempotent_and_preserves_current(tmp_path: Path) -> None:
    token = _token(tmp_path, "ADA")
    before = token.read_bytes()
    now = datetime(2026, 7, 26, tzinfo=timezone.utc)
    lifecycle.bootstrap_shadow(tmp_path, ["ADA"], ttl_hours=24, now=now)
    lifecycle.promote_current(
        tmp_path,
        ["ADA"],
        now=datetime(2026, 7, 26, 1, tzinfo=timezone.utc),
    )
    first = lifecycle.stage_next(
        tmp_path,
        ["ADA"],
        ttl_hours=24,
        now=datetime(2026, 7, 26, 2, tzinfo=timezone.utc),
    )
    next_before = (tmp_path / "ADA.token.next").read_bytes()
    second = lifecycle.stage_next(tmp_path, ["ADA"])
    assert token.read_bytes() == before
    assert (tmp_path / "ADA.token.next").read_bytes() == next_before
    assert first[0]["action"] == "created"
    assert first[0]["generation"] == 2
    assert second[0]["action"] == "kept"
