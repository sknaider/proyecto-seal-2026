from __future__ import annotations

import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from soul_platform.autostart import (
    AutostartContract,
    _authenticated_probe,
    _clean_path,
    _request_shutdown,
    _windows_roaming_root,
    activate_descriptor,
    deactivate_descriptor,
    descriptor_path,
    disable_descriptor,
    disable_tray_descriptor,
    install_descriptor,
    tray_descriptor_path,
)


def _contract(tmp_path: Path, monkeypatch, **proxy_overrides) -> AutostartContract:
    data = tmp_path / "SOUL Data"
    data.mkdir(mode=0o700)
    token = data / "proxy.token"
    token.write_bytes(b"a" * 32)
    token.chmod(0o600)
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("python")
    proxy = {
        "host": "127.0.0.1",
        "port": 11435,
        "require_auth": True,
        "token_file": str(token),
    }
    proxy.update(proxy_overrides)
    config = data / "proxy.toml"
    config.write_text(
        "[soul]\n"
        f'name = "MachineSoul"\ndb = "{data / "MachineSoul.db"}"\n'
        'machine_soul_id = "12345678-1234-5678-1234-567812345678"\n'
        "[proxy]\n"
        + "\n".join(
            f"{key} = {str(value).lower() if isinstance(value, bool) else repr(value)}"
            for key, value in proxy.items()
        )
        + '\n[upstream]\nbase_url = "http://127.0.0.1:11434/v1"\nmodel = "brain"\n'
    )
    config.chmod(0o600)
    return AutostartContract.load(config, python=str(python))


def test_autostart_requires_loopback_and_auth(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="loopback"):
        _contract(tmp_path, monkeypatch, host="0.0.0.0")


def test_autostart_rejects_remote_even_with_legacy_opt_in(tmp_path, monkeypatch):
    contract = _contract(tmp_path, monkeypatch)
    text = contract.config.read_text().replace(
        'base_url = "http://127.0.0.1:11434/v1"',
        'base_url = "https://example.com/v1"\nallow_remote = true',
    )
    contract.config.write_text(text)
    contract.config.chmod(0o600)
    with pytest.raises(ValueError, match="disabled"):
        AutostartContract.load(contract.config, python=str(contract.python))


def test_autostart_rejects_weak_or_shared_token(tmp_path, monkeypatch):
    contract = _contract(tmp_path, monkeypatch)
    contract.token_file.write_bytes(b"weak")
    with pytest.raises(ValueError, match="at least 32"):
        AutostartContract.load(contract.config, python=str(contract.python))
    contract.token_file.write_bytes(b"a" * 32)
    contract.token_file.chmod(0o644)
    with pytest.raises(ValueError, match="group/other"):
        AutostartContract.load(contract.config, python=str(contract.python))


@pytest.mark.parametrize("platform", ["linux", "windows", "macos"])
def test_install_is_per_user_and_disable_preserves_soul(
    tmp_path, monkeypatch, platform
):
    contract = _contract(tmp_path, monkeypatch)
    home = tmp_path / "User Home"
    target = install_descriptor(contract, platform, home=home)
    assert target.is_file()
    assert home in target.parents
    assert contract.config.exists() and contract.token_file.exists()
    assert disable_descriptor(platform, home=home) == target
    assert not target.exists()
    assert contract.config.exists() and contract.token_file.exists()


def test_windows_tray_descriptor_can_be_disabled_without_touching_soul(tmp_path):
    home = tmp_path / "User Home"
    target = tray_descriptor_path("windows", home)
    assert target is not None
    target.parent.mkdir(parents=True)
    target.write_text("launcher")
    soul_data = home / "soul.db"
    soul_data.write_text("memory")
    assert disable_tray_descriptor("windows", home=home) == target
    assert not target.exists()
    assert soul_data.read_text() == "memory"
    assert disable_tray_descriptor("linux", home=home) is None


def test_windows_startup_uses_redirected_appdata(tmp_path, monkeypatch):
    redirected = tmp_path / "Redirected Roaming"
    monkeypatch.setattr("soul_platform.autostart.sys.platform", "win32")
    monkeypatch.setenv("APPDATA", str(redirected))
    assert _windows_roaming_root(tmp_path / "home") == redirected
    assert redirected in descriptor_path("windows", tmp_path / "home").parents
    assert redirected in tray_descriptor_path("windows", tmp_path / "home").parents


def test_ipv6_loopback_probe_and_shutdown_use_bracketed_urls(tmp_path, monkeypatch):
    contract = _contract(tmp_path, monkeypatch, host="::1")
    urls = []

    class Response:
        status = 200

        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return self.payload

    def open_request(request, **_kwargs):
        urls.append(request.full_url)
        if request.full_url.endswith("/v1/models"):
            return Response(b'{"object":"list","data":[{"id":"brain"}]}')
        if request.full_url.endswith("/ready"):
            return Response(b'{"ready":true}')
        return Response(b"{}")

    monkeypatch.setattr("soul_platform.autostart.urllib.request.urlopen", open_request)
    _authenticated_probe(contract, timeout_seconds=0.1)
    _request_shutdown(contract)
    assert urls == [
        "http://[::1]:11435/v1/models",
        "http://[::1]:11435/ready",
        "http://[::1]:11435/admin/shutdown",
    ]


def test_descriptors_use_absolute_python_config_and_loopback_contract(
    tmp_path, monkeypatch
):
    contract = _contract(tmp_path, monkeypatch)
    linux = install_descriptor(
        contract, "linux", home=tmp_path / "linux-home"
    ).read_text()
    windows = install_descriptor(
        contract, "windows", home=tmp_path / "win-home"
    ).read_text()
    mac = plistlib.loads(
        install_descriptor(contract, "macos", home=tmp_path / "mac-home").read_bytes()
    )
    for rendered in (linux, windows, " ".join(mac["ProgramArguments"])):
        assert str(contract.python) in rendered
        assert str(contract.config) in rendered
        assert "0.0.0.0" not in rendered
        assert "proxy.token" not in rendered
    assert "NoNewPrivileges=true" in linux
    assert "ProtectSystem=strict" in linux
    assert "WScript.Shell" in windows and "pythonw.exe" not in windows
    assert windows.count("Chr(34)") == 4
    assert '-m" "soul_platform.proxy' in linux


def test_newline_in_path_is_rejected(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="control"):
        _clean_path(str(tmp_path / "safe") + "\nbad", "test.path")


def test_symlinked_descriptor_parent_and_systemd_specifier_are_rejected_or_escaped(
    tmp_path, monkeypatch
):
    contract = _contract(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    escaped = tmp_path / "escaped"
    escaped.mkdir()
    (home / ".config").symlink_to(escaped, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        install_descriptor(contract, "linux", home=home)

    percent_root = tmp_path / "percent%h"
    percent_root.mkdir()
    percent_root.chmod(0o700)
    percent_contract = _contract(percent_root, monkeypatch)
    unit = install_descriptor(
        percent_contract, "linux", home=tmp_path / "safe-home"
    ).read_text()
    assert "percent%%h" in unit
    assert "percent%h" not in unit.replace("percent%%h", "")


def test_linux_lifecycle_enables_starts_stops_and_preserves_data(tmp_path, monkeypatch):
    contract = _contract(tmp_path, monkeypatch)
    home = tmp_path / "home"
    target = install_descriptor(contract, "linux", home=home)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("soul_platform.autostart._run", fake_run)
    monkeypatch.setattr(
        "soul_platform.autostart._authenticated_probe", lambda contract: None
    )
    monkeypatch.setattr("soul_platform.autostart._wait_stopped", lambda contract: None)
    assert activate_descriptor(contract, "linux", home=home) == target
    assert ["systemctl", "--user", "enable", "--now", target.name] in commands
    assert ["systemctl", "--user", "restart", target.name] in commands
    assert deactivate_descriptor(contract, "linux", home=home) == target
    assert ["systemctl", "--user", "disable", "--now", target.name] in commands
    assert not target.exists()
    assert contract.config.exists() and contract.token_file.exists()


def test_failed_stop_retains_descriptor(tmp_path, monkeypatch):
    contract = _contract(tmp_path, monkeypatch)
    home = tmp_path / "home"
    target = install_descriptor(contract, "linux", home=home)
    monkeypatch.setattr(
        "soul_platform.autostart._run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    with pytest.raises(RuntimeError, match="descriptor retained"):
        deactivate_descriptor(contract, "linux", home=home)
    assert target.exists()
