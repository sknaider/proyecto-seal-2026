from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

import soul_framework
import soul_platform
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_version_contract_and_packaged_source_layout():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == soul_platform.__version__ == version("soul-platform")
    assert (ROOT / "src/soul_platform/agency.py").is_file()
    core_version = tuple(int(part) for part in soul_framework.__version__.split(".")[:3])
    assert core_version == (0, 4, 2), "soul-platform 0.4 pins soul-framework 0.4.2"


def test_build_excludes_all_local_distribution_directories():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    excluded = config["tool"]["hatch"]["build"]["exclude"]
    assert "/dist" in excluded
    assert "/dist-*" in excluded
    assert "/uv.lock" in excluded


def test_release_surface_has_no_seal_internals_or_secret_forms():
    patterns = {
        "home_path": re.compile("/home/" + "dadito"),
        "monorepo": re.compile("proyecto-" + "seal"),
        "internal_role": re.compile(
            "mcp_runtime_(?:" + "ada|alice|jarvis|nexus|fable)"
        ),
        "lan_ip": re.compile(r"\b192\.168\." + r"68\.\d+\b"),
        "credentialed_dsn": re.compile(r"postgres(?:ql)?://[^\s:/]+:[^\s@]+@"),
        "private_key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    }
    files = [
        ROOT / "README.md",
        ROOT / "pyproject.toml",
        ROOT / "installer/soul-install.sh",
    ]
    files.extend(path for path in (ROOT / "src").rglob("*.py") if path.is_file())
    files.extend(path for path in (ROOT / "tests").rglob("*.py") if path.is_file())
    offenders = []
    for path in files:
        content = path.read_text(encoding="utf-8")
        for name, pattern in patterns.items():
            if pattern.search(content):
                offenders.append(f"{name}:{path.relative_to(ROOT)}")
    assert offenders == []
