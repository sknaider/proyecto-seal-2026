from __future__ import annotations

import hashlib
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_windows_bundle", ROOT / "tools" / "build_windows_bundle.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _fake_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "package"
    installer = root / "installer"
    installer.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="soul-platform"\nversion="9.8.7"\n'
    )
    (installer / "Install-Soul.ps1").write_text("installer")
    (installer / "Instalar-SOUL-Windows.bat").write_text("launcher")
    (installer / "LEEME-WINDOWS.txt").write_text("SOUL PLATFORM 9.8.7")
    wheel = tmp_path / "soul_platform-9.8.7-py3-none-any.whl"
    wheel.write_bytes(b"wheel-bytes")
    core_wheel = tmp_path / "soul_framework-0.4.2-py3-none-any.whl"
    core_wheel.write_bytes(b"core-wheel-bytes")
    return root, wheel, core_wheel


def test_bundle_is_checksum_bound_and_deterministic(tmp_path):
    root, wheel, core_wheel = _fake_root(tmp_path)
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    result = MODULE.build_bundle(root, wheel, core_wheel, first)
    MODULE.build_bundle(root, wheel, core_wheel, second)
    assert first.read_bytes() == second.read_bytes()
    assert result["wheel_sha256"] == hashlib.sha256(b"wheel-bytes").hexdigest()
    assert result["core_wheel_sha256"] == hashlib.sha256(
        b"core-wheel-bytes"
    ).hexdigest()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names == [
            "Install-Soul.ps1",
            "Instalar-SOUL-Windows.bat",
            "LEEME-WINDOWS.txt",
            wheel.name,
            f"{wheel.name}.sha256",
            core_wheel.name,
            f"{core_wheel.name}.sha256",
        ]
        checksum = archive.read(f"{wheel.name}.sha256").decode()
        assert result["wheel_sha256"] in checksum and wheel.name in checksum
        core_checksum = archive.read(f"{core_wheel.name}.sha256").decode()
        assert hashlib.sha256(b"core-wheel-bytes").hexdigest() in core_checksum
        assert core_wheel.name in core_checksum


def test_bundle_rejects_stale_wheel_or_stale_guide(tmp_path):
    root, wheel, core_wheel = _fake_root(tmp_path)
    stale = tmp_path / "soul_platform-9.8.6-py3-none-any.whl"
    stale.write_bytes(b"stale")
    with pytest.raises(ValueError, match="expected regular release wheel"):
        MODULE.build_bundle(root, stale, core_wheel, tmp_path / "bad.zip")
    (root / "installer" / "LEEME-WINDOWS.txt").write_text("SOUL PLATFORM 9.8.6")
    with pytest.raises(ValueError, match="guide version"):
        MODULE.build_bundle(root, wheel, core_wheel, tmp_path / "bad-guide.zip")


def test_bundle_rejects_wrong_core_wheel(tmp_path):
    root, wheel, _core_wheel = _fake_root(tmp_path)
    wrong = tmp_path / "soul_framework-0.4.0-py3-none-any.whl"
    wrong.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="Core wheel 0.4.2"):
        MODULE.build_bundle(root, wheel, wrong, tmp_path / "bad-core.zip")
