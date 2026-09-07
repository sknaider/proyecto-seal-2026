from __future__ import annotations

import json
import grp
import os
import socket
import stat
import subprocess
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE_MANIFEST = ROOT / "fable/mcp_web_soul_bundle.sha256"
ARTIFACTS = tuple(
    line.split(None, 1)[1].strip()
    for line in BUNDLE_MANIFEST.read_text(encoding="utf-8").splitlines()
    if line.strip()
) + ("fable/mcp_web_soul_bundle.sha256",)


def _privileged_stat(path: Path) -> tuple[int, int, str, str]:
    raw = subprocess.check_output(
        ["sudo", "-n", "stat", "-Lc", "%f %a %U %G", str(path)], text=True
    ).strip()
    mode_hex, permissions, owner, group = raw.split()
    return int(mode_hex, 16), int(permissions, 8), owner, group


def test_release_artifacts_exist_and_are_git_indexed():
    missing = [raw for raw in ARTIFACTS if not (ROOT / raw).is_file()]
    assert missing == []
    indexed = set(
        subprocess.check_output(
            ["git", "ls-files", "--", *ARTIFACTS], cwd=ROOT, text=True
        ).splitlines()
    )
    assert indexed == set(ARTIFACTS)
    required_closed_set = {
        "fable/config/mcp-web-soul.codex.toml",
        "fable/config/mcp-web-soul.mcp.json",
        "fable/deploy_mcp_web_soul_release.sh",
        "fable/mcp_web_soul_control.py",
        "fable/mcp_web_soul_profiles.py",
        "fable/mcp_web_soul_reconcile.py",
        "fable/sql/mcp_web_soul_operator_boundary.sql",
    }
    assert required_closed_set <= set(ARTIFACTS)


def test_versioned_units_bind_witness_to_dedicated_operator_identity():
    witness = (ROOT / "fable/systemd/seal-audit-witness.service").read_text()
    operator = (ROOT / "fable/systemd/seal-mcp-web-soul-operator.service").read_text()

    assert "User=seal-audit-witness" in witness
    assert "Group=seal-audit-witness" in witness
    assert "SupplementaryGroups=seal-audit-client" in witness
    assert "--socket-group seal-audit-client" in witness
    assert "--allow-user seal-mcp-web-operator" in witness
    assert "--allow-user seal-mcp-web-cdp" in witness
    assert "--bind-stream seal-mcp-web-operator=audit:0cf0c41f" in witness
    assert "--bind-stream seal-mcp-web-cdp=audit:4f292c4b" in witness
    assert "--allow-user dadito" not in witness
    assert "Group=dadito\n" not in witness
    assert "User=seal-mcp-web-operator" in operator
    assert "SupplementaryGroups=seal-audit-client seal-mcp-web-control" in operator
    assert "SupplementaryGroups=seal-audit-client dadito" not in operator
    assert "LoadCredential=operator.dsn:" in operator
    assert "EnvironmentFile=/home/dadito" not in operator
    assert "MCP_WEB_SOUL_CONTROL_PATH=/var/lib/seal-mcp-web-cdp/control/control.sqlite3" in operator
    runtime = "/opt/seal/mcp-web-soul/current/bin/python3"
    assert f"ExecStart={runtime} /usr/local/libexec/seal/" in operator
    assert f"ExecStart={runtime} /usr/local/libexec/seal/" in witness
    assert "RuntimeDirectoryMode=0750" in witness
    assert "Environment=MCP_WEB_SOUL_OPERATOR_REQUIRE_CURSOR=1" in operator


def test_sql_operator_boundary_uses_canonical_superuser_not_display_name():
    sql = (ROOT / "fable/sql/mcp_web_soul_operator_boundary.sql").read_text()
    function_defs = sql.split("REVOKE ALL", 1)[0]
    assert "display_name" not in function_defs
    assert function_defs.count("su.id = 1") == 1
    assert function_defs.count("u.id = 1") == 2  # direct user plus the su.id substring
    assert function_defs.count("role, '')") == 2
    assert "= 'superuser'" in function_defs
    assert "basic/display-name/session spoof authenticated" in sql


def test_stdio_wrapper_and_configs_use_dedicated_identity(tmp_path):
    wrapper = (ROOT / "fable/seal_cdp_mcp_wrapper.sh").read_text()
    sudoers = (ROOT / "fable/sudoers/seal-mcp-web-cdp").read_text()
    config = json.loads((ROOT / "fable/config/mcp-web-soul.mcp.json").read_text())
    codex = (ROOT / "fable/config/mcp-web-soul.codex.toml").read_text()

    assert "/usr/bin/env -i" in wrapper
    assert "SEAL_AGENT=shared-host" in wrapper
    assert 'SEAL_AGENT="$SEAL_AGENT"' not in wrapper
    assert "MCP_WEB_SOUL_STATE_ROOT=/var/lib/seal-mcp-web-cdp/state" in wrapper
    assert "/opt/seal/mcp-web-soul/current/bin/python3 /usr/local/libexec/seal/seal_cdp_mcp.py" in wrapper
    assert "/home/dadito" not in wrapper
    assert 'env_keep += "SEAL_AGENT"' not in sudoers
    assert "NOPASSWD:" in sudoers and 'wrapper ""' in sudoers
    assert "SETENV" not in sudoers

    installer = (ROOT / "fable/install_mcp_web_soul_cdp.sh").read_text()
    assert "usermod -g seal-mcp-web-cdp -G seal-audit-client,seal-mcp-web-control,seal-mcp-web-cdp-readers" in installer
    assert "usermod -a -G seal-mcp-web-cdp-readers dadito" in installer
    assert "systemctl restart" not in installer
    assert "/run/seal-mcp-web-soul-cutover/legacy-disabled" in installer
    assert "systemctl stop seal-audit-witness.service" in installer
    assert 'chmod 0660 "$path"' in installer
    assert 'm 2750' in installer and 'm 0700' in installer

    expected = [
        "-n", "-u", "seal-mcp-web-cdp",
        "/usr/local/libexec/seal/seal-cdp-mcp-wrapper",
    ]
    assert config["mcpServers"]["mcp-web-soul"] == {
        "command": "/usr/bin/sudo", "args": expected,
    }
    assert 'command = "/usr/bin/sudo"' in codex
    assert 'args = ["-n", "-u", "seal-mcp-web-cdp", "/usr/local/libexec/seal/seal-cdp-mcp-wrapper"]' in codex

    subprocess.run(["sh", "-n", str(ROOT / "fable/seal_cdp_mcp_wrapper.sh")], check=True)
    subprocess.run(["sh", "-n", str(ROOT / "fable/install_mcp_web_soul_cdp.sh")], check=True)
    subprocess.run(["/usr/sbin/visudo", "-cf", str(ROOT / "fable/sudoers/seal-mcp-web-cdp")], check=True)
    assert '${SEAL_AGENT' not in wrapper
    assert "legacy shared-UID mcp-web-soul entrypoint is disabled" in (ROOT / "fable/seal_cdp_mcp.py").read_text()

    reader_group = grp.getgrgid(os.getgid()).gr_name
    probe = (
        "import pathlib,stat,seal_cdp_mcp as s; "
        "p=s._managed_artifact_path('probe/result.bin'); p.write_bytes(b'ok'); "
        "s._publish_artifact(p); "
        "assert stat.S_IMODE(p.stat().st_mode)==0o640; "
        "assert p.parent.stat().st_mode & 0o777==0o750"
    )
    subprocess.run(
        ["/usr/bin/python3", "-c", probe],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(ROOT / "fable"),
            "SEAL_AGENT": "ADA",
            "MCP_WEB_SOUL_STATE_ROOT": str(tmp_path / "state"),
            "MCP_WEB_SOUL_CONTROL_ROOT": str(tmp_path / "control"),
            "MCP_WEB_SOUL_CONTROL_GROUP": reader_group,
            "MCP_WEB_SOUL_READER_GROUP": reader_group,
        },
        check=True,
    )


def test_fresh_libexec_closed_set_imports_without_repository_fallback(tmp_path):
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    installed_modules = (
        "seal_cdp_mcp.py",
        "seal_cdp.py",
        "mcp_web_soul_proxy.py",
        "mcp_web_soul_operator.py",
        "mcp_web_soul_reconcile.py",
        "mcp_web_soul_control.py",
        "mcp_web_soul_security.py",
        "mcp_web_soul_visual.py",
        "mcp_web_soul_profiles.py",
        "mcp_web_soul_witness.py",
    )
    for name in installed_modules:
        shutil.copyfile(ROOT / "fable" / name, libexec / name)
    probe = (
        "import sys; "
        f"sys.path.insert(0, {str(libexec)!r}); "
        "import seal_cdp, seal_cdp_mcp, mcp_web_soul_operator, mcp_web_soul_reconcile"
    )
    release_python = Path(
        os.environ.get(
            "SEAL_PRODUCTION_PYTHON",
            "/home/dadito/IA/seal-spark/.venv/bin/python3",
        )
    )
    subprocess.run(
        [str(release_python), "-I", "-c", probe],
        cwd=tmp_path,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "SEAL_AGENT": "import-probe",
            "MCP_WEB_SOUL_STATE_ROOT": str(tmp_path / "state"),
            "MCP_WEB_SOUL_CONTROL_ROOT": str(tmp_path / "control"),
        },
        check=True,
    )


def test_cutover_barrier_prevents_fastmcp_start_in_subprocess(tmp_path):
    barrier = tmp_path / "legacy-disabled"
    barrier.write_text("active\n", encoding="utf-8")
    started = tmp_path / "mcp-started"
    probe = (
        "from pathlib import Path; import seal_cdp_mcp as s; "
        "s._CUTOVER_BARRIER=Path(r'" + str(barrier) + "'); "
        "s.mcp.run=lambda:Path(r'" + str(started) + "').write_text('started'); "
        "s._run_server()"
    )
    result = subprocess.run(
        ["/usr/bin/python3", "-c", probe],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(ROOT / "fable"),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "mcp-web-soul cutover is active" in result.stderr or (
        "legacy shared-UID mcp-web-soul entrypoint is disabled" in result.stderr
    )
    assert not started.exists()


def test_absent_cutover_barrier_allows_fastmcp_start_in_subprocess(tmp_path):
    barrier = tmp_path / "absent"
    started = tmp_path / "mcp-started"
    probe = (
        "from pathlib import Path; import seal_cdp_mcp as s; "
        "s._CUTOVER_BARRIER=Path(r'" + str(barrier) + "'); "
        "s.mcp.run=lambda:Path(r'" + str(started) + "').write_text('started'); "
        "s._run_server()"
    )
    subprocess.run(
        ["/usr/bin/python3", "-c", probe],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PYTHONPATH": str(ROOT / "fable"),
        },
        check=True,
    )
    assert started.read_text(encoding="utf-8") == "started"


def test_live_daemon_bytes_match_versioned_artifacts():
    assert (ROOT / "fable/mcp_web_soul_deploy.py").read_bytes() == Path(
        "/usr/local/libexec/seal/mcp_web_soul_deploy.py"
    ).read_bytes()
    assert (ROOT / "fable/mcp_web_soul_witness.py").read_bytes() == Path(
        "/usr/local/libexec/seal/mcp_web_soul_witness.py"
    ).read_bytes()
    assert (ROOT / "fable/systemd/seal-audit-witness.service").read_bytes() == Path(
        "/etc/systemd/system/seal-audit-witness.service"
    ).read_bytes()
    assert (ROOT / "fable/systemd/seal-mcp-web-soul-operator.service").read_bytes() == (
        Path("/etc/systemd/system/seal-mcp-web-soul-operator.service")
    ).read_bytes()
    for name in (
        "seal_cdp_mcp.py",
        "seal_cdp.py",
        "mcp_web_soul_operator.py",
        "mcp_web_soul_control.py",
        "mcp_web_soul_security.py",
        "mcp_web_soul_visual.py",
        "mcp_web_soul_profiles.py",
    ):
        assert (ROOT / "fable" / name).read_bytes() == (
            Path("/usr/local/libexec/seal") / name
        ).read_bytes()


def test_runtime_dependencies_are_exactly_pinned_and_installed():
    requirements = ROOT / "fable/requirements-mcp-web-soul.txt"
    pins = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert pins == sorted(set(pins))
    production_python = Path(
        os.environ.get(
            "SEAL_PRODUCTION_PYTHON",
            "/home/dadito/IA/seal-spark/.venv/bin/python3",
        )
    )
    assert production_python.is_file()
    for pin in pins:
        package, expected = pin.split("==", 1)
        observed = subprocess.check_output(
            [
                str(production_python),
                "-c",
                "from importlib.metadata import version; "
                f"print(version({package!r}))",
            ],
            text=True,
        ).strip()
        assert observed == expected


def test_live_security_daemons_and_permissions():
    assert subprocess.run(
        ["systemctl", "is-active", "--quiet", "seal-audit-witness.service"],
        check=False,
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
    assert socket_group == "seal-audit-client"
    assert stat.S_ISDIR(state_mode) and state_permissions == 0o700
    state_modes = subprocess.check_output(
        [
            "sudo",
            "-n",
            "find",
            str(state),
            "-maxdepth",
            "1",
            "-type",
            "f",
            "-printf",
            "%m\\n",
        ],
        text=True,
    ).splitlines()
    assert state_modes
    assert set(state_modes) == {"600"}

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        with pytest.raises(PermissionError):
            client.connect(str(witness_socket))

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

    cdp_groups = set(subprocess.check_output(["id", "-nG", "seal-mcp-web-cdp"], text=True).split())
    assert {"seal-audit-client", "seal-mcp-web-control", "seal-mcp-web-cdp-readers"} <= cdp_groups
    assert cdp_groups.isdisjoint({"dadito", "sudo", "docker", "incus", "lxd"})
    assert "dadito" not in grp.getgrnam("seal-audit-client").gr_mem

    assert (ROOT / "fable/seal_cdp_mcp_wrapper.sh").read_bytes() == Path(
        "/usr/local/libexec/seal/seal-cdp-mcp-wrapper"
    ).read_bytes()
    live_sudoers = subprocess.check_output(
        ["sudo", "-n", "cat", "/etc/sudoers.d/seal-mcp-web-cdp"]
    )
    assert (ROOT / "fable/sudoers/seal-mcp-web-cdp").read_bytes() == live_sudoers
