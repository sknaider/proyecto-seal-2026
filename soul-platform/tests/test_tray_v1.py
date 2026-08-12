from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

import pytest
from soul_platform import tray
from soul_platform.bootstrap import render_config
from soul_platform.proxy import ProxySettings


@pytest.fixture
def settings_factory():
    def make(root: Path) -> ProxySettings:
        root.mkdir(parents=True, mode=0o700)
        if os.name != "nt":
            os.chmod(root, 0o700)
        token = root / "proxy.token"
        token.write_text("t" * 48, encoding="utf-8")
        if os.name != "nt":
            os.chmod(token, 0o600)
        settings = ProxySettings(
            soul_name="MachineSoul",
            soul_db=root / "MachineSoul.db",
            machine_soul_id=str(uuid.uuid4()),
            host="127.0.0.1",
            port=11435,
            require_auth=True,
            token_file=token,
            upstream_kind="ollama",
            upstream_base_url=tray.DEFAULT_OLLAMA_BASE,
            upstream_model="brain-a",
        )
        config = root / "proxy.toml"
        config.write_text(render_config(settings), encoding="utf-8")
        if os.name != "nt":
            os.chmod(config, 0o600)
        return settings

    return make


def test_discover_models_is_stable_deduplicated_and_skips_unsafe(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, *_args):
            return json.dumps(
                {
                    "models": [
                        {"name": "gemma3:4b"},
                        {"name": "gemma3:4b"},
                        {"name": "qwen2.5:7b"},
                        {"name": "bad\nmodel"},
                        {},
                    ]
                }
            ).encode()

    monkeypatch.setattr(tray.urllib.request, "urlopen", lambda *_a, **_k: Response())
    assert tray.discover_ollama_models() == ["gemma3:4b", "qwen2.5:7b"]


def test_discover_models_fails_closed_when_ollama_is_unavailable(monkeypatch):
    def unavailable(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setattr(tray.urllib.request, "urlopen", unavailable)
    assert tray.discover_ollama_models() == []


def test_discover_models_rejects_oversized_response(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, limit):
            return b"x" * limit

    monkeypatch.setattr(tray.urllib.request, "urlopen", lambda *_a, **_k: Response())
    assert tray.discover_ollama_models() == []


def test_status_without_config_is_honestly_unconfigured(tmp_path):
    state = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    ).status()
    assert state.configured is False
    assert state.active is False and state.ready is False
    assert state.endpoint == "http://127.0.0.1:11435/v1"


def test_loopback_url_brackets_ipv6_literal():
    assert tray._loopback_base_url("::1", 11435) == "http://[::1]:11435"
    assert tray._loopback_base_url("127.0.0.1", 11435) == "http://127.0.0.1:11435"


def test_windows_ui_dispatch_marshals_worker_completion_to_message_loop(monkeypatch):
    class PostMessage:
        argtypes = None
        restype = None

        def __call__(self, *_args):
            return 1

    class User32:
        PostMessageW = PostMessage()

    class Icon:
        def __init__(self):
            self._message_handlers = {}
            self._hwnd = 123

    icon = Icon()
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.setattr(
        tray.ctypes, "WinDLL", lambda *_a, **_k: User32(), raising=False
    )
    tray._install_ui_dispatch(icon)
    busy = threading.Lock()
    assert busy.acquire(blocking=False)
    completed = []
    worker = tray._run_tray_work(
        icon,
        busy,
        lambda: "network result",
        lambda result, error: completed.append((result, error)),
    )
    assert worker is not None
    worker.join(timeout=2)
    assert completed == []
    assert busy.locked()
    icon._message_handlers[tray.WINDOWS_UI_MESSAGE](0, 0)
    assert completed == [("network result", None)]
    assert not busy.locked()


def test_visual_tray_fails_honestly_outside_windows(monkeypatch, capsys, tmp_path):
    controller = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    )
    monkeypatch.setattr(tray, "SoulTrayController", lambda: controller)
    monkeypatch.setattr(tray.sys, "platform", "linux")
    assert tray.main([]) == 2
    assert "Windows only" in capsys.readouterr().err


def _controller_with_settings(tmp_path, settings_factory):
    root = tmp_path / "SOUL"
    settings = settings_factory(root)
    controller = tray.SoulTrayController(root=root, platform="linux", home=tmp_path)
    return controller, settings


def test_status_requires_identity_and_baseline_not_just_http_200(
    tmp_path, settings_factory, monkeypatch
):
    controller, settings = _controller_with_settings(tmp_path, settings_factory)

    def foreign(url, **_kwargs):
        assert url.endswith("/health")
        return 200, {
            "ok": True,
            "machine_soul_id": "00000000-0000-0000-0000-000000000000",
            "baseline_hash": settings.baseline_hash,
        }

    monkeypatch.setattr(controller, "_json", foreign)
    state = controller.status()
    assert state.active is False
    assert state.detail == "otro proceso ocupa el puerto"


def test_status_reports_ready_only_after_health_and_brain_probe(
    tmp_path, settings_factory, monkeypatch
):
    controller, settings = _controller_with_settings(tmp_path, settings_factory)

    def healthy(url, **_kwargs):
        if url.endswith("/health"):
            return 200, {
                "ok": True,
                "machine_soul_id": settings.machine_soul_id,
                "baseline_hash": settings.baseline_hash,
            }
        return 200, {"ready": True}

    monkeypatch.setattr(controller, "_json", healthy)
    state = controller.status()
    assert state.active is True and state.ready is True
    assert state.model == settings.upstream_model


def test_status_rejects_oversized_local_response(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, limit):
            return b"x" * limit

    monkeypatch.setattr(tray.urllib.request, "urlopen", lambda *_a, **_k: Response())
    assert tray.SoulTrayController._json("http://127.0.0.1/health") == (0, {})


def test_turn_on_existing_config_uses_managed_descriptor_and_verifies_effect(
    tmp_path, settings_factory, monkeypatch
):
    controller, settings = _controller_with_settings(tmp_path, settings_factory)
    calls = []
    monkeypatch.setattr(
        tray, "switch_upstream", lambda *_a, **_k: calls.append("switch")
    )
    monkeypatch.setattr(
        tray, "install_descriptor", lambda *_a, **_k: calls.append("install")
    )
    monkeypatch.setattr(
        tray, "activate_descriptor", lambda *_a, **_k: calls.append("activate")
    )
    monkeypatch.setattr(
        controller,
        "status",
        lambda: tray.TrayStatus(
            True, True, True, "gemma3:4b", "http://127.0.0.1:11435/v1", "lista"
        ),
    )
    result = controller.turn_on("gemma3:4b")
    assert result.ready is True
    assert calls == ["switch", "install", "activate"]
    assert (
        settings.soul_db.exists() is False
    )  # controller never purges or rewrites user data


def test_failed_brain_switch_restores_previous_active_brain(
    tmp_path, settings_factory, monkeypatch
):
    controller, _settings = _controller_with_settings(tmp_path, settings_factory)
    states = iter(
        [
            tray.TrayStatus(True, True, True, "brain-a", "endpoint", "lista"),
        ]
    )
    monkeypatch.setattr(controller, "status", lambda: next(states))
    switched = []
    monkeypatch.setattr(
        tray,
        "switch_upstream",
        lambda _config, **kwargs: switched.append(kwargs["upstream_model"]),
    )
    installed = []
    monkeypatch.setattr(
        tray, "install_descriptor", lambda *_a, **_k: installed.append(True)
    )
    activations = []

    def activate(*_args, **_kwargs):
        activations.append(True)
        if len(activations) == 1:
            raise RuntimeError("new brain unavailable")

    monkeypatch.setattr(tray, "activate_descriptor", activate)
    monkeypatch.setattr(tray, "deactivate_descriptor", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="new brain unavailable"):
        controller.turn_on("brain-b")
    assert switched == ["brain-b", "brain-a"]
    assert len(installed) == 2 and len(activations) == 2


def test_failed_first_start_preserves_identity_but_disables_candidate(
    tmp_path, monkeypatch
):
    controller = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    )
    root = controller.root

    def initialized(**_kwargs):
        root.mkdir(mode=0o700)
        (root / "proxy.toml").write_text("created")
        raise RuntimeError("brain unavailable")

    monkeypatch.setattr(tray, "initialize", initialized)
    monkeypatch.setattr(controller, "_rollback_failed_turn_on", lambda **_kwargs: [])
    with pytest.raises(RuntimeError, match="brain unavailable"):
        controller.turn_on("brain-a")
    assert (root / "proxy.toml").read_text() == "created"


def test_turn_on_rejects_control_characters_before_any_mutation(tmp_path, monkeypatch):
    controller = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    )
    called = []
    monkeypatch.setattr(tray, "initialize", lambda **_kwargs: called.append(True))
    with pytest.raises(ValueError, match="model name"):
        controller.turn_on("gemma\nmalicioso")
    assert called == []


def test_first_turn_on_uses_safe_bootstrap_and_requires_ready_effect(
    tmp_path, monkeypatch
):
    controller = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    )
    captured = {}

    def fake_initialize(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(tray, "initialize", fake_initialize)
    monkeypatch.setattr(
        controller,
        "status",
        lambda: tray.TrayStatus(
            True, True, True, "brain-a", "http://127.0.0.1:11435/v1", "lista"
        ),
    )
    assert controller.turn_on("brain-a").ready is True
    assert captured["root"] == controller.root
    assert captured["upstream_base_url"] == tray.DEFAULT_OLLAMA_BASE
    assert captured["enable_autostart"] is True
    assert captured["activate_autostart"] is True


def test_single_instance_rejects_symlink_lock(tmp_path):
    target = tmp_path / "real.lock"
    target.write_text("0")
    link = tmp_path / "tray.lock"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError, match="symlink"):
        tray._SingleInstance(link).acquire()


def test_windows_single_instance_uses_named_mutex_without_touching_lock_file(
    tmp_path, monkeypatch
):
    names = []
    monkeypatch.setattr(
        tray, "_acquire_windows_mutex", lambda name: names.append(name) or None
    )
    lock_path = tmp_path / "SOUL" / "tray.lock"
    assert tray._SingleInstance(lock_path, windows=True).acquire() is False
    assert names[0].startswith("Local\\SOUL-Tray-")
    assert not lock_path.exists()


def test_windows_clipboard_uses_absolute_system32_binary(tmp_path, monkeypatch):
    system_root = tmp_path / "Windows"
    clip = system_root / "System32" / "clip.exe"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"stub")
    calls = []
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", str(system_root))
    monkeypatch.setattr(
        tray.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or None,
    )
    assert tray.copy_to_clipboard("secret") is True
    assert calls[0][0] == [str(clip)]
    assert calls[0][1]["input"] == "secret".encode("utf-16le")


def test_windows_tray_autostart_uses_pythonw_and_hidden_vbs(tmp_path):
    python = tmp_path / "venv" / "Scripts" / "python.exe"
    pythonw = python.with_name("pythonw.exe")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    pythonw.write_bytes(b"")
    target = tray.install_tray_autostart(
        home=tmp_path, python=str(python), platform="windows"
    )
    payload = target.read_text()
    assert str(pythonw) in payload
    assert "-m soul_platform.tray" in payload
    assert ", 0, False" in payload


def test_turn_off_preserves_config_and_requires_observed_shutdown(
    tmp_path, settings_factory, monkeypatch
):
    controller, _settings = _controller_with_settings(tmp_path, settings_factory)
    original = controller.config.read_bytes()
    monkeypatch.setattr(tray, "deactivate_descriptor", lambda *_a, **_k: None)
    monkeypatch.setattr(
        controller,
        "status",
        lambda: tray.TrayStatus(
            True, False, False, "brain", "http://127.0.0.1:11435/v1", "apagada"
        ),
    )
    result = controller.turn_off()
    assert result.active is False
    assert controller.config.read_bytes() == original


def test_check_mode_is_nonzero_until_soul_is_ready(monkeypatch, capsys, tmp_path):
    controller = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    )
    monkeypatch.setattr(tray, "SoulTrayController", lambda: controller)
    monkeypatch.setattr(tray, "discover_ollama_models", lambda: ["brain-a"])
    assert tray.main(["--check"]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["configured"] is False
    assert payload["ollama_models"] == ["brain-a"]


def test_check_mode_returns_zero_only_for_ready_soul(monkeypatch, capsys, tmp_path):
    controller = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    )
    monkeypatch.setattr(tray, "SoulTrayController", lambda: controller)
    monkeypatch.setattr(
        controller,
        "status",
        lambda: tray.TrayStatus(True, True, True, "brain-a", "endpoint", "lista"),
    )
    monkeypatch.setattr(tray, "discover_ollama_models", lambda: ["brain-a"])
    assert tray.main(["--check"]) == 0
    assert json.loads(capsys.readouterr().out)["ready"] is True


def test_desktop_check_fails_when_dependency_is_missing(monkeypatch, capsys, tmp_path):
    controller = tray.SoulTrayController(
        root=tmp_path / "SOUL", platform="linux", home=tmp_path
    )
    monkeypatch.setattr(tray, "SoulTrayController", lambda: controller)
    monkeypatch.setattr(
        tray, "_desktop_self_check", lambda: {"pillow": True, "pystray": False}
    )
    assert tray.main(["--check-desktop"]) == 2
    assert json.loads(capsys.readouterr().out)["pystray"] is False


def test_pyproject_packages_tray_entrypoint_and_desktop_dependencies():
    import tomllib

    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())[
        "project"
    ]
    assert project["scripts"]["soul-tray-cli"] == "soul_platform.tray:main"
    assert project["gui-scripts"]["soul-tray"] == "soul_platform.tray:main"
    desktop = project["optional-dependencies"]["desktop"]
    assert "pystray==0.19.5" in desktop
    assert "pillow==12.3.0" in desktop
