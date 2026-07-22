from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
API = ROOT / "seal-agent" / "api"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rollback_manifest_pins_source_and_build_inputs() -> None:
    manifest = json.loads((API / "rollback-manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["artifacts"].items():
        assert expected == f"sha256:{_sha256(API / name)}"

    requirements = (API / "requirements.rollback.txt").read_text(encoding="utf-8").splitlines()
    assert requirements
    assert all("==" in line for line in requirements if line.strip() and not line.startswith("#"))


def test_rollback_image_contract_is_immutable_and_unprivileged() -> None:
    dockerfile = (API / "Dockerfile.rollback").read_text(encoding="utf-8")
    manifest = json.loads((API / "rollback-manifest.json").read_text(encoding="utf-8"))

    assert dockerfile.startswith("FROM python@sha256:")
    assert "USER soulapi" in dockerfile
    assert 'CMD ["python", "-m", "uvicorn"' in dockerfile
    assert "pip install" not in dockerfile.split("CMD ", 1)[1]
    assert manifest["runtime_contract"]["bind_mounts"] == 0
    assert manifest["runtime_contract"]["install_at_startup"] is False
    assert manifest["legacy_container"]["must_not_be_started"] is True
    assert manifest["quarantine_expires_at"]
