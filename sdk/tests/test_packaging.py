from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import zipfile


SDK_ROOT = Path(__file__).resolve().parents[1]


def test_package_versions_are_aligned() -> None:
    project = tomllib.loads((SDK_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    namespace: dict[str, object] = {}
    init_text = (SDK_ROOT / "seal_memory" / "__init__.py").read_text(encoding="utf-8")
    version_line = next(line for line in init_text.splitlines() if line.startswith("__version__"))
    exec(version_line, namespace)
    assert project["project"]["version"] == namespace["__version__"] == "0.2.0"


def test_wheel_contains_current_contract_and_imports_cleanly() -> None:
    wheels = sorted((SDK_ROOT / "dist").glob("seal_memory-0.2.0-*.whl"))
    assert wheels, "build the 0.2.0 wheel before running the packaging gate"
    wheel = wheels[-1]

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for required in (
            "seal_memory/__init__.py",
            "seal_memory/client.py",
            "seal_memory/async_client.py",
            "seal_memory/contracts.py",
            "seal_memory/mem0_compat.py",
            "seal_memory-0.2.0.dist-info/METADATA",
        ):
            assert required in names
        metadata = archive.read("seal_memory-0.2.0.dist-info/METADATA").decode("utf-8")
        assert "Version: 0.2.0\n" in metadata

    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "PYTHONPATH": str(wheel),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, seal_memory; "
                "assert seal_memory.__version__ == '0.2.0'; "
                "assert 'seal_memory.mem0_compat' not in sys.modules; "
                "from seal_memory import MemoryClient; "
                "assert 'REST' in repr(MemoryClient())",
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert result.returncode == 0, result.stderr
