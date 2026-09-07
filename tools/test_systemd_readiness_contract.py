from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD_DIR = ROOT / "systemd"


def _directives(path: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "[")) or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found.setdefault(key, []).append(value)
    return found


def test_repo_managed_http_daemons_have_readiness_contract() -> None:
    """Type=simple lifecycle state must not be the only HTTP readiness signal."""
    for path in sorted(SYSTEMD_DIR.glob("*.service")):
        directives = _directives(path)
        service_type = directives.get("Type", ["simple"])[-1]
        exec_start = " ".join(directives.get("ExecStart", []))
        is_http_daemon = "uvicorn" in exec_start or path.name == (
            "seal-sync-endpoint.service"
        )
        if service_type == "simple" and is_http_daemon:
            assert directives.get("ExecStartPost"), (
                f"{path.name}: Type=simple HTTP daemon lacks an "
                "ExecStartPost readiness probe"
            )


def test_oneshot_units_are_not_declared_as_persistent_daemons() -> None:
    for path in sorted(SYSTEMD_DIR.glob("*.service")):
        directives = _directives(path)
        if directives.get("Type", ["simple"])[-1] == "oneshot":
            assert directives.get("Restart", ["no"])[-1] == "no"
