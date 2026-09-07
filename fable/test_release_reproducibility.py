from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "fable" / "production_manifest.txt"
PRODUCTION_PYTHON = Path(
    os.environ.get("SEAL_PRODUCTION_PYTHON", "/home/dadito/IA/seal-spark/.venv/bin/python3")
)


def _privileged_stat(path: Path) -> tuple[int, int, str, str]:
    raw = subprocess.check_output(
        ["sudo", "-n", "stat", "-Lc", "%f %a %U %G", str(path)], text=True
    ).strip()
    mode_hex, permissions, owner, group = raw.split()
    return int(mode_hex, 16), int(permissions, 8), owner, group


def _paths() -> list[str]:
    return [
        line.strip() for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_production_manifest_files_exist_and_are_git_tracked():
    paths = _paths()
    assert paths == sorted(set(paths))
    missing = [path for path in paths if not (ROOT / path).is_file()]
    assert missing == []
    tracked = set(subprocess.check_output(["git", "ls-files", *paths], cwd=ROOT, text=True).splitlines())
    assert set(paths) == tracked


def test_live_user_units_match_versioned_units():
    unit_root = ROOT / "fable" / "systemd"
    live_root = Path.home() / ".config" / "systemd" / "user"
    names = [
        "spectre-openai-shim.service",
        "seal-fable-checkpoint.service",
        "seal-fable-daily-brief.service",
        "seal-fable-daily-brief.timer",
        "seal-instrumentation-health.service",
        "seal-instrumentation-health.timer",
        "seal-listening-guard.service",
    ]
    for name in names:
        assert (unit_root / name).read_text(encoding="utf-8") == (
            live_root / name
        ).read_text(encoding="utf-8")


def test_live_witness_bytes_match_versioned_artifacts():
    for name in ("seal-audit-witness.service", "seal-mcp-web-soul-operator.service"):
        assert (ROOT / "fable/systemd" / name).read_bytes() == (
            Path("/etc/systemd/system") / name
        ).read_bytes()
    for name in (
        "mcp_web_soul_witness.py",
        "mcp_web_soul_operator.py",
        "mcp_web_soul_control.py",
        "mcp_web_soul_security.py",
    ):
        assert (ROOT / "fable" / name).read_bytes() == (
            Path("/usr/local/libexec/seal") / name
        ).read_bytes()


def test_browser_runtime_dependencies_are_pinned_and_present():
    requirements = ROOT / "fable/requirements-mcp-web-soul.txt"
    pins = [line.strip() for line in requirements.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert pins == sorted(set(pins))
    assert PRODUCTION_PYTHON.is_file()
    installed = json.loads(subprocess.check_output(
        [
            str(PRODUCTION_PYTHON), "-c",
            (
                "import json; from importlib.metadata import version; "
                "print(json.dumps({p: version(p) for p in "
                + repr([pin.split("==", 1)[0] for pin in pins])
                + "}))"
            ),
        ],
        text=True,
    ))
    for pin in pins:
        package, expected = pin.split("==", 1)
        assert installed[package] == expected
    for executable in ("brave-browser", "docker", "socat", "Xvfb", "xauth", "x11vnc"):
        assert shutil.which(executable), executable


def test_live_security_daemons_and_permissions():
    assert subprocess.run(
        ["systemctl", "is-active", "--quiet", "seal-audit-witness.service"], check=False,
    ).returncode == 0
    assert subprocess.run(
        ["systemctl", "is-active", "--quiet", "seal-mcp-web-soul-operator.service"],
        check=False,
    ).returncode == 0
    runtime = Path("/run/seal-audit-witness")
    witness_socket = runtime / "witness.sock"
    state = Path("/var/lib/seal-audit-witness")
    runtime_mode, runtime_permissions, _, _ = _privileged_stat(runtime)
    socket_mode, socket_permissions, _, socket_group = _privileged_stat(witness_socket)
    state_mode, state_permissions, _, _ = _privileged_stat(state)
    assert stat.S_ISDIR(runtime_mode) and runtime_permissions == 0o750
    assert stat.S_ISSOCK(socket_mode) and socket_permissions == 0o660
    assert stat.S_ISDIR(state_mode) and state_permissions == 0o700
    assert socket_group == "seal-audit-client"
    assert subprocess.check_output(
        ["systemctl", "show", "seal-mcp-web-soul-operator.service", "-p", "User", "--value"],
        text=True,
    ).strip() == "seal-mcp-web-operator"

    # The shared human/agent UID must not reach the authenticated witness.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        try:
            client.connect(str(witness_socket))
        except PermissionError:
            pass
        else:  # pragma: no cover - live security regression
            raise AssertionError("shared uid connected to the witness")

    probe = (
        "import json,socket; "
        "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); "
        f"s.connect({str(witness_socket)!r}); "
        "s.sendall(b'{\"op\":\"health\"}\\n'); "
        "r=json.loads(s.recv(4096)); assert r['ok'] is True"
    )
    for user in ("seal-mcp-web-operator", "seal-mcp-web-cdp"):
        subprocess.run(
            ["sudo", "-n", "-u", user, "/usr/bin/python3", "-c", probe],
            check=True,
        )

    groups = set(subprocess.check_output(["id", "-nG", "seal-mcp-web-cdp"], text=True).split())
    assert {"seal-audit-client", "seal-mcp-web-control", "seal-mcp-web-cdp-readers"} <= groups
    assert groups.isdisjoint({"dadito", "sudo", "docker", "incus", "lxd"})

    incomplete = subprocess.check_output(
        [
            "sudo", "-n", "sqlite3", "/var/lib/seal-audit-witness/witness.sqlite3",
            "SELECT count(*) FROM heads h WHERE h.sequence != "
            "(SELECT count(*) FROM events e WHERE e.stream=h.stream);",
        ],
        text=True,
    ).strip()
    assert incomplete == "0"
