#!/usr/bin/env python3
"""Build the deterministic, checksum-bound Windows one-click bundle."""

from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

import tomllib

INSTALLER_FILES = (
    "Install-Soul.ps1",
    "Instalar-SOUL-Windows.bat",
    "LEEME-WINDOWS.txt",
)
ZIP_TIMESTAMP = (2020, 2, 2, 0, 0, 0)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _zip_entry(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, payload)


def build_bundle(root: Path, wheel: Path, output: Path) -> dict[str, str]:
    root = root.resolve()
    wheel = wheel.resolve()
    output = output.resolve()
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    version = str(project["version"])
    expected_wheel = f"soul_platform-{version}-py3-none-any.whl"
    if wheel.name != expected_wheel or not wheel.is_file() or wheel.is_symlink():
        raise ValueError(f"expected regular release wheel {expected_wheel}")
    installer = root / "installer"
    payloads: dict[str, bytes] = {}
    for name in INSTALLER_FILES:
        path = installer / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing safe installer file: {name}")
        payloads[name] = path.read_bytes()
    if f"SOUL PLATFORM {version}" not in payloads["LEEME-WINDOWS.txt"].decode("utf-8"):
        raise ValueError("Windows guide version does not match pyproject")
    wheel_bytes = wheel.read_bytes()
    wheel_hash = _sha256(wheel_bytes)
    payloads[wheel.name] = wheel_bytes
    payloads[f"{wheel.name}.sha256"] = f"{wheel_hash}  {wheel.name}\n".encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        for name in (*INSTALLER_FILES, wheel.name, f"{wheel.name}.sha256"):
            _zip_entry(archive, name, payloads[name])
    return {
        "version": version,
        "wheel_sha256": wheel_hash,
        "bundle_sha256": _sha256(output.read_bytes()),
        "bundle": str(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    project = tomllib.loads((args.root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    output = (
        args.output
        or args.root / "dist" / f"SOUL-Platform-{project['version']}-Windows.zip"
    )
    result = build_bundle(args.root, args.wheel, output)
    for key, value in result.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
