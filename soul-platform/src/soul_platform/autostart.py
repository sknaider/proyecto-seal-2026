"""Per-user autostart descriptors for the local SOUL Proxy.

This module deliberately does not install Python, elevate privileges, delete
data, or start an unauthenticated proxy.  It only writes an OS-native per-user
descriptor after the proxy configuration proves the minimum security contract.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from soul_platform.proxy import (
    ProxySettings,
    _assert_no_symlink_components,
)


PlatformName = Literal["linux", "windows", "macos"]


def _clean_path(value: object, field: str) -> Path:
    text = str(value or "")
    if not text or any(char in text for char in ("\x00", "\r", "\n")):
        raise ValueError(f"{field} must be a non-empty path without control characters")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{field} must be absolute")
    return path


@dataclass(frozen=True)
class AutostartContract:
    config: Path
    soul_db: Path
    token_file: Path
    host: str
    port: int
    python: Path
    upstream_model: str

    @classmethod
    def load(cls, config_path: str | os.PathLike[str], *, python: str | None = None):
        config = _clean_path(config_path, "config")
        settings = ProxySettings.from_toml(config)
        executable = _clean_path(python or sys.executable, "python")
        if not executable.is_file():
            raise ValueError("python executable does not exist")
        return cls(
            config=config,
            soul_db=settings.soul_db,
            token_file=settings.token_file,
            host=settings.host,
            port=settings.port,
            python=executable,
            upstream_model=settings.upstream_model,
        )

    @property
    def command(self) -> tuple[str, ...]:
        return (str(self.python), "-m", "soul_platform.proxy", "--config", str(self.config))


def _systemd_quote(value: str) -> str:
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped + '"'


def render_linux(contract: AutostartContract) -> bytes:
    command = " ".join(_systemd_quote(value) for value in contract.command)
    writable = _systemd_quote(str(contract.soul_db.parent))
    return (
        "[Unit]\nDescription=SOUL Proxy - persistent machine soul\nAfter=network.target\n\n"
        "[Service]\nType=simple\n"
        f"ExecStart={command}\nRestart=on-failure\nRestartSec=3\n"
        "NoNewPrivileges=true\nPrivateTmp=true\nProtectSystem=strict\nProtectHome=read-only\n"
        f"ReadWritePaths={writable}\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n\n"
        "[Install]\nWantedBy=default.target\n"
    ).encode()


def _vbs_string(value: str) -> str:
    if any(char in value for char in ("\x00", "\r", "\n")):
        raise ValueError("unsafe control character in Windows launcher value")
    return value.replace('"', '""')


def render_windows(contract: AutostartContract) -> bytes:
    python = contract.python.with_name("pythonw.exe")
    if not python.exists():
        python = contract.python
    script = (
        "Option Explicit\r\n"
        "Dim shell\r\n"
        "Set shell = CreateObject(\"WScript.Shell\")\r\n"
        'shell.Run Chr(34) & '
        f'"{_vbs_string(str(python))}" & Chr(34) & '
        '" -m soul_platform.proxy --config " & Chr(34) & '
        f'"{_vbs_string(str(contract.config))}" & Chr(34), 0, False\r\n'
    )
    return script.encode("utf-8")


def render_macos(contract: AutostartContract) -> bytes:
    payload = {
        "Label": "com.soul.platform.proxy",
        "ProgramArguments": list(contract.command),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
        "StandardOutPath": str(contract.soul_db.parent / "proxy.stdout.log"),
        "StandardErrorPath": str(contract.soul_db.parent / "proxy.stderr.log"),
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def descriptor_path(platform: PlatformName, home: Path) -> Path:
    if platform == "linux":
        return home / ".config" / "systemd" / "user" / "soul-platform-proxy.service"
    if platform == "windows":
        return home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "SOUL Platform.vbs"
    if platform == "macos":
        return home / "Library" / "LaunchAgents" / "com.soul.platform.proxy.plist"
    raise ValueError(f"unsupported platform: {platform}")


def _safe_descriptor_parent(target: Path, home: Path) -> None:
    _assert_no_symlink_components(home, "home")
    home.mkdir(parents=True, exist_ok=True)
    current = home
    relative = target.parent.relative_to(home)
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("autostart path contains a symlinked directory")
        current.mkdir(exist_ok=True)
        if not current.is_dir():
            raise ValueError("autostart parent is not a directory")


def install_descriptor(contract: AutostartContract, platform: PlatformName, *, home: Path | None = None) -> Path:
    requested_home = (home or Path.home()).expanduser()
    _assert_no_symlink_components(requested_home, "home")
    resolved_home = requested_home.resolve()
    target = descriptor_path(platform, resolved_home)
    _safe_descriptor_parent(target, resolved_home)
    if target.is_symlink():
        raise ValueError("refusing to replace a symlinked autostart descriptor")
    payload = {
        "linux": render_linux,
        "windows": render_windows,
        "macos": render_macos,
    }[platform](contract)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def disable_descriptor(platform: PlatformName, *, home: Path | None = None) -> Path:
    """Disable autostart only. Soul data, config, token and venv are preserved."""
    requested_home = (home or Path.home()).expanduser()
    _assert_no_symlink_components(requested_home, "home")
    target = descriptor_path(platform, requested_home.resolve())
    _assert_no_symlink_components(target.parent, "autostart path")
    if target.exists() and not target.is_symlink():
        target.unlink()
    elif target.is_symlink():
        raise ValueError("refusing to remove a symlinked autostart descriptor")
    return target


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True, timeout=30)


def _authenticated_probe(contract: AutostartContract, *, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    token = contract.token_file.read_text(encoding="utf-8").strip()
    models_request = urllib.request.Request(
        f"http://{contract.host}:{contract.port}/v1/models",
        headers={"Authorization": f"Bearer {token}"},
    )
    ready_request = urllib.request.Request(f"http://{contract.host}:{contract.port}/ready")
    last_error = "not started"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(models_request, timeout=1) as response:
                models_payload = json.loads(response.read())
            with urllib.request.urlopen(ready_request, timeout=1) as ready_response:
                ready_payload = json.loads(ready_response.read())
            models = models_payload.get("data") if models_payload.get("object") == "list" else []
            if response.status == 200 and ready_response.status == 200 and ready_payload.get("ready") is True and any(
                item.get("id") == contract.upstream_model for item in models if isinstance(item, dict)
            ):
                return
            last_error = f"unexpected health response {response.status}"
        except (OSError, ValueError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"SOUL proxy failed authenticated startup probe: {last_error}")


def activate_descriptor(
    contract: AutostartContract,
    platform: PlatformName,
    *,
    home: Path | None = None,
    wait: bool = True,
) -> Path:
    """Enable and start the per-user service without elevation or shell parsing."""
    target = descriptor_path(platform, (home or Path.home()).expanduser().resolve())
    if not target.is_file() or target.is_symlink():
        raise ValueError("autostart descriptor is missing or unsafe")
    if platform == "linux":
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", target.name])
        # Converge upgrades too: enable --now does not replace an already-live
        # process whose package bytes changed.
        _run(["systemctl", "--user", "restart", target.name])
    elif platform == "macos":
        domain = f"gui/{os.getuid()}"
        _run(["launchctl", "bootout", domain, str(target)], check=False)
        _run(["launchctl", "bootstrap", domain, str(target)])
        _run(["launchctl", "kickstart", "-k", f"{domain}/com.soul.platform.proxy"])
    elif platform == "windows":
        _request_shutdown(contract)
        _wait_stopped(contract)
        subprocess.Popen(
            ["wscript.exe", str(target)],
            close_fds=True,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    else:
        raise ValueError(f"unsupported platform: {platform}")
    if wait:
        _authenticated_probe(contract)
    return target


def _request_shutdown(contract: AutostartContract) -> None:
    token = contract.token_file.read_text(encoding="utf-8").strip()
    request = urllib.request.Request(
        f"http://{contract.host}:{contract.port}/admin/shutdown",
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            if response.status != 200:
                raise RuntimeError(f"shutdown returned HTTP {response.status}")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"shutdown returned HTTP {exc.code}") from exc
    except urllib.error.URLError:
        return


def _wait_stopped(contract: AutostartContract, *, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((contract.host, contract.port), timeout=0.5):
                pass
        except OSError:
            return
        time.sleep(0.2)
    raise RuntimeError("SOUL proxy did not stop; autostart descriptor retained")


def deactivate_descriptor(
    contract: AutostartContract,
    platform: PlatformName,
    *,
    home: Path | None = None,
) -> Path:
    """Stop the managed proxy, disable autostart, and preserve all soul data."""
    target = descriptor_path(platform, (home or Path.home()).expanduser().resolve())
    if platform == "linux":
        stopped = _run(["systemctl", "--user", "disable", "--now", target.name], check=False)
        if stopped.returncode != 0:
            raise RuntimeError(f"systemctl failed to stop {target.name}; descriptor retained")
        _wait_stopped(contract)
        _run(["systemctl", "--user", "daemon-reload"])
    elif platform == "macos":
        stopped = _run(["launchctl", "bootout", f"gui/{os.getuid()}", str(target)], check=False)
        if stopped.returncode != 0:
            raise RuntimeError("launchctl failed to stop SOUL proxy; descriptor retained")
        _wait_stopped(contract)
    elif platform == "windows":
        _request_shutdown(contract)
        _wait_stopped(contract)
    return disable_descriptor(platform, home=home)


def restart_descriptor(
    contract: AutostartContract,
    platform: PlatformName,
    *,
    home: Path | None = None,
) -> None:
    target = descriptor_path(platform, (home or Path.home()).expanduser().resolve())
    if not target.is_file() or target.is_symlink():
        raise RuntimeError("cannot switch a running brain without a managed autostart service")
    if platform == "linux":
        _run(["systemctl", "--user", "restart", target.name])
    elif platform == "macos":
        _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.soul.platform.proxy"])
    elif platform == "windows":
        _request_shutdown(contract)
        _wait_stopped(contract)
        activate_descriptor(contract, platform, home=home, wait=False)
    _authenticated_probe(contract)


def _current_platform() -> PlatformName:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    raise RuntimeError(f"unsupported platform: {sys.platform}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m soul_platform.autostart")
    actions = parser.add_subparsers(dest="action", required=True)
    install = actions.add_parser("install")
    install.add_argument("--config", required=True)
    install.add_argument("--python")
    actions.add_parser("disable")
    args = parser.parse_args()
    platform = _current_platform()
    if args.action == "install":
        target = install_descriptor(
            AutostartContract.load(args.config, python=args.python), platform
        )
        print(f"autostart installed: {target}")
    else:
        target = disable_descriptor(platform)
        print(f"autostart disabled; soul data preserved: {target}")


if __name__ == "__main__":
    main()
