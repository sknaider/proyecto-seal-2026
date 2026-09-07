from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import seal_identity_lifecycle as lifecycle
from tools import seal_identity_rotate as rotate


def _seed_current(root: Path, *, now: datetime) -> None:
    token = root / "ADA.token"
    root.mkdir(parents=True, exist_ok=True)
    token.write_text("old-token\n", encoding="utf-8")
    token.chmod(0o600)
    lifecycle.bootstrap_shadow(root, ["ADA"], ttl_hours=24, now=now)
    lifecycle.promote_current(root, ["ADA"], now=now)


def _run(monkeypatch, root: Path, *, within: int = 72) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seal_identity_rotate.py",
            "--token-dir",
            str(root),
            "--agent",
            "ADA",
            "--renew-within-hours",
            str(within),
        ],
    )
    return rotate.main()


def test_rotation_is_recurrent_after_expired_grace(monkeypatch, tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    _seed_current(tmp_path, now=now - timedelta(hours=23))

    assert _run(monkeypatch, tmp_path) == 0
    current_meta_path = tmp_path / "ADA.token.meta.json"
    grace_meta_path = tmp_path / "ADA.token.next.meta.json"
    current_meta = json.loads(current_meta_path.read_text())
    grace_meta = json.loads(grace_meta_path.read_text())
    assert current_meta["generation"] == 2
    assert grace_meta["generation"] == 1
    assert grace_meta["grace_after_promotion"] is True

    # Simulate the next renewal window: the old grace is expired and the
    # current generation is due.  A recurrent rotator must retire generation 1
    # before staging generation 3; otherwise stage-next reuses generation 1 and
    # promote-next fails generation_not_successor.
    grace_meta["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    grace_meta_path.write_text(json.dumps(grace_meta), encoding="utf-8")
    grace_meta_path.chmod(0o600)
    current_meta["expires_at"] = (now + timedelta(hours=1)).isoformat()
    current_meta_path.write_text(json.dumps(current_meta), encoding="utf-8")
    current_meta_path.chmod(0o600)

    assert _run(monkeypatch, tmp_path) == 0
    final_current = json.loads(current_meta_path.read_text())
    final_grace = json.loads(grace_meta_path.read_text())
    assert final_current["generation"] == 3
    assert final_grace["generation"] == 2
    assert final_grace["grace_after_promotion"] is True
    archives = list((tmp_path / ".retired").glob("ADA-g1-*"))
    assert len(archives) == 1
    assert (archives[0] / "ADA.token.retired").is_file()
    assert (archives[0] / "ADA.token.retired.meta.json").is_file()


def test_dry_run_does_not_retire_active_grace(monkeypatch, tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    _seed_current(tmp_path, now=now - timedelta(hours=23))
    assert _run(monkeypatch, tmp_path) == 0
    before = (tmp_path / "ADA.token.next").read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seal_identity_rotate.py",
            "--token-dir",
            str(tmp_path),
            "--agent",
            "ADA",
            "--renew-within-hours",
            "72",
            "--dry-run",
        ],
    )
    assert rotate.main() == 0
    assert (tmp_path / "ADA.token.next").read_bytes() == before


def test_dry_run_does_not_retire_expired_grace(monkeypatch, tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    _seed_current(tmp_path, now=now - timedelta(hours=23))
    assert _run(monkeypatch, tmp_path) == 0

    grace_token_path = tmp_path / "ADA.token.next"
    grace_meta_path = tmp_path / "ADA.token.next.meta.json"
    before_token = grace_token_path.read_bytes()
    grace_meta = json.loads(grace_meta_path.read_text())
    grace_meta["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    grace_meta_path.write_text(json.dumps(grace_meta), encoding="utf-8")
    grace_meta_path.chmod(0o600)
    before_meta = grace_meta_path.read_bytes()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seal_identity_rotate.py",
            "--token-dir",
            str(tmp_path),
            "--agent",
            "ADA",
            "--renew-within-hours",
            "72",
            "--dry-run",
        ],
    )
    assert rotate.main() == 0
    assert grace_token_path.read_bytes() == before_token
    assert grace_meta_path.read_bytes() == before_meta
    assert not (tmp_path / ".retired").exists()
