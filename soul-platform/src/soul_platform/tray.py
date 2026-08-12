"""Desktop tray controller for the persistent SOUL machine proxy.

The tray is deliberately a thin, user-space control surface.  It never owns
the proxy process itself: the OS-native autostart descriptor remains the
source of truth, so closing the tray does not kill the machine soul.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from soul_platform.autostart import (
    AutostartContract,
    PlatformName,
    _current_platform,
    _loopback_base_url,
    _safe_descriptor_parent,
    activate_descriptor,
    deactivate_descriptor,
    install_descriptor,
    tray_descriptor_path,
)
from soul_platform.bootstrap import default_root, initialize, switch_upstream
from soul_platform.proxy import ProxySettings, _assert_no_symlink_components

DEFAULT_OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
DEFAULT_OLLAMA_BASE = "http://127.0.0.1:11434/v1"
MAX_DISCOVERY_BYTES = 1_048_576
MAX_STATUS_BYTES = 65_536
WINDOWS_MUTEX_ALREADY_EXISTS = 183
WINDOWS_UI_MESSAGE = 0x8000 + 0x51A


def _safe_model_name(value: object) -> str:
    model = str(value or "").strip()
    if not model or len(model) > 256 or any(char in model for char in "\x00\r\n"):
        raise ValueError("model name must contain 1..256 safe characters")
    return model


def discover_ollama_models(
    *, url: str = DEFAULT_OLLAMA_TAGS, timeout: float = 3.0
) -> list[str]:
    """Return Ollama model names in stable order; unavailable Ollama is empty."""
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return []
            raw = response.read(MAX_DISCOVERY_BYTES + 1)
            if len(raw) > MAX_DISCOVERY_BYTES:
                return []
            payload = json.loads(raw)
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        return []
    models: list[str] = []
    seen: set[str] = set()
    for item in payload.get("models", []) if isinstance(payload, dict) else []:
        try:
            name = _safe_model_name(item.get("name") if isinstance(item, dict) else "")
        except ValueError:
            continue
        if name not in seen:
            seen.add(name)
            models.append(name)
    return models


def copy_to_clipboard(text: str) -> bool:
    """Copy through an argv-only native command; never invoke a shell."""
    commands: list[tuple[list[str], bytes]]
    if sys.platform.startswith("win"):
        system_root = os.environ.get("SystemRoot")
        if not system_root:
            return False
        clip = Path(system_root) / "System32" / "clip.exe"
        if not clip.is_file() or clip.is_symlink():
            return False
        commands = [([str(clip)], text.encode("utf-16le"))]
    elif sys.platform == "darwin":
        commands = [(["pbcopy"], text.encode())]
    else:
        commands = [
            (["wl-copy"], text.encode()),
            (["xclip", "-selection", "clipboard"], text.encode()),
            (["xsel", "--clipboard", "--input"], text.encode()),
        ]
    for command, payload in commands:
        try:
            subprocess.run(command, input=payload, check=True, timeout=5)
            return True
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            continue
    return False


@dataclass(frozen=True)
class TrayStatus:
    configured: bool
    active: bool
    ready: bool
    model: str | None
    endpoint: str
    detail: str


class SoulTrayController:
    """Safe controller shared by the GUI and the headless diagnostic command."""

    def __init__(
        self,
        *,
        root: Path | None = None,
        platform: PlatformName | None = None,
        home: Path | None = None,
        python: str | None = None,
        opener: Callable[[Path], None] | None = None,
    ) -> None:
        self.platform = platform or _current_platform()
        requested_home = (home or Path.home()).expanduser()
        _assert_no_symlink_components(requested_home, "home")
        self.home = requested_home.resolve()
        requested_root = (
            root or default_root(self.platform, home=self.home)
        ).expanduser()
        _assert_no_symlink_components(requested_root, "SOUL root")
        self.root = requested_root.resolve()
        self.config = self.root / "proxy.toml"
        self.python = python or sys.executable
        self._opener = opener or self._open_native

    def _settings(self) -> ProxySettings:
        return ProxySettings.from_toml(self.config)

    @staticmethod
    def _json(url: str, *, timeout: float = 1.0) -> tuple[int, dict]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_STATUS_BYTES + 1)
                if len(raw) > MAX_STATUS_BYTES:
                    return 0, {}
                payload = json.loads(raw)
                return response.status, payload if isinstance(payload, dict) else {}
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read(MAX_STATUS_BYTES + 1)
                payload = {} if len(raw) > MAX_STATUS_BYTES else json.loads(raw)
            except (ValueError, TypeError):
                payload = {}
            return exc.code, payload if isinstance(payload, dict) else {}
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            return 0, {}

    def status(self) -> TrayStatus:
        if not self.config.is_file() or self.config.is_symlink():
            return TrayStatus(
                False, False, False, None, "http://127.0.0.1:11435/v1", "sin configurar"
            )
        try:
            settings = self._settings()
        except (OSError, ValueError) as exc:
            return TrayStatus(
                True, False, False, None, "", f"configuración inválida: {exc}"
            )
        base_url = _loopback_base_url(settings.host, settings.port)
        endpoint = f"{base_url}/v1"
        code, health = self._json(f"{base_url}/health")
        active = (
            code == 200
            and health.get("ok") is True
            and health.get("machine_soul_id") == settings.machine_soul_id
            and health.get("baseline_hash") == settings.baseline_hash
        )
        if not active:
            detail = "apagada" if code == 0 else "otro proceso ocupa el puerto"
            return TrayStatus(
                True, False, False, settings.upstream_model, endpoint, detail
            )
        ready_code, ready_payload = self._json(f"{base_url}/ready")
        ready = ready_code == 200 and ready_payload.get("ready") is True
        return TrayStatus(
            True,
            True,
            ready,
            settings.upstream_model,
            endpoint,
            "lista" if ready else "alma activa; cerebro no disponible",
        )

    def turn_on(self, model: str) -> TrayStatus:
        model = _safe_model_name(model)
        previous = self._settings() if self.config.exists() else None
        previous_status = self.status() if previous else None
        changed = False
        try:
            if previous:
                if (
                    previous.upstream_kind != "ollama"
                    or previous.upstream_base_url != DEFAULT_OLLAMA_BASE
                    or previous.upstream_model != model
                ):
                    switch_upstream(
                        self.config,
                        upstream_kind="ollama",
                        upstream_base_url=DEFAULT_OLLAMA_BASE,
                        upstream_model=model,
                        restart=False,
                        platform=self.platform,
                        home=self.home,
                    )
                    changed = True
                contract = AutostartContract.load(self.config, python=self.python)
                install_descriptor(contract, self.platform, home=self.home)
                activate_descriptor(contract, self.platform, home=self.home)
            else:
                initialize(
                    root=self.root,
                    upstream_kind="ollama",
                    upstream_base_url=DEFAULT_OLLAMA_BASE,
                    upstream_model=model,
                    python=self.python,
                    platform=self.platform,
                    home=self.home,
                    enable_autostart=True,
                    activate_autostart=True,
                )
            result = self.status()
            if not result.active or not result.ready:
                raise RuntimeError(f"SOUL did not become ready: {result.detail}")
            return result
        except Exception as original:
            rollback_errors = self._rollback_failed_turn_on(
                previous=previous,
                previous_was_active=bool(previous_status and previous_status.active),
                changed=changed,
            )
            if rollback_errors:
                raise RuntimeError(
                    f"SOUL start failed ({original}); rollback also failed: "
                    + "; ".join(rollback_errors)
                ) from original
            raise

    def _rollback_failed_turn_on(
        self,
        *,
        previous: ProxySettings | None,
        previous_was_active: bool,
        changed: bool,
    ) -> list[str]:
        """Stop a failed candidate and restore the prior brain/state."""
        errors: list[str] = []
        if self.config.is_file() and not self.config.is_symlink():
            try:
                candidate = AutostartContract.load(self.config, python=self.python)
                deactivate_descriptor(candidate, self.platform, home=self.home)
            except Exception as exc:  # noqa: BLE001 - preserve original failure too
                errors.append(f"candidate stop: {exc}")
        if previous is not None and changed:
            try:
                switch_upstream(
                    self.config,
                    upstream_kind=previous.upstream_kind,
                    upstream_base_url=previous.upstream_base_url,
                    upstream_model=previous.upstream_model,
                    allow_remote=previous.upstream_allow_remote,
                    restart=False,
                    platform=self.platform,
                    home=self.home,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"config restore: {exc}")
        if previous is not None and previous_was_active:
            try:
                restored = AutostartContract.load(self.config, python=self.python)
                install_descriptor(restored, self.platform, home=self.home)
                activate_descriptor(restored, self.platform, home=self.home)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"prior brain restart: {exc}")
        return errors

    def turn_off(self) -> TrayStatus:
        if not self.config.is_file() or self.config.is_symlink():
            return self.status()
        contract = AutostartContract.load(self.config, python=self.python)
        deactivate_descriptor(contract, self.platform, home=self.home)
        result = self.status()
        if result.active:
            raise RuntimeError("SOUL proxy remained active after shutdown")
        return result

    def endpoint(self) -> str:
        return self.status().endpoint

    def token(self) -> str:
        return self._settings().read_token()

    def open_data_folder(self) -> None:
        _assert_no_symlink_components(self.root, "SOUL root")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        _assert_no_symlink_components(self.root, "SOUL root")
        self._opener(self.root)

    @staticmethod
    def _open_native(path: Path) -> None:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=True, timeout=10)
        else:
            subprocess.run(["xdg-open", str(path)], check=True, timeout=10)


class _SingleInstance:
    """Advisory per-user tray lock. Failure is safe: no second tray starts."""

    def __init__(self, path: Path, *, windows: bool | None = None) -> None:
        self.path = path
        self.windows = os.name == "nt" if windows is None else windows
        self.handle = None
        self._windows_mutex = None

    def acquire(self) -> bool:
        _assert_no_symlink_components(self.path.parent, "tray lock parent")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _assert_no_symlink_components(self.path.parent, "tray lock parent")
        if self.path.is_symlink():
            raise ValueError("tray lock must never be a symlink")
        if self.windows:
            digest = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()
            self._windows_mutex = _acquire_windows_mutex(f"Local\\SOUL-Tray-{digest}")
            return self._windows_mutex is not None
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, 0o600)
        handle = os.fdopen(descriptor, "r+b")
        if self.path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self._windows_mutex is not None:
            _release_windows_mutex(self._windows_mutex)
            self._windows_mutex = None
            return
        if self.handle is None:
            return
        try:
            import fcntl

            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _acquire_windows_mutex(name: str):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, True, name)
    last_error = ctypes.get_last_error()
    if not handle:
        raise OSError(last_error, "CreateMutexW failed")
    if last_error == WINDOWS_MUTEX_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return kernel32, handle


def _release_windows_mutex(mutex) -> None:
    kernel32, handle = mutex
    released = kernel32.ReleaseMutex(handle)
    release_error = ctypes.get_last_error() if not released else 0
    closed = kernel32.CloseHandle(handle)
    close_error = ctypes.get_last_error() if not closed else 0
    if release_error:
        raise OSError(release_error, "ReleaseMutex failed")
    if close_error:
        raise OSError(close_error, "CloseHandle failed")


def install_tray_autostart(
    *,
    home: Path | None = None,
    python: str | None = None,
    platform: PlatformName | None = None,
) -> Path:
    if (platform or _current_platform()) != "windows":
        raise RuntimeError("tray autostart is currently supported on Windows")
    requested_home = (home or Path.home()).expanduser()
    _assert_no_symlink_components(requested_home, "home")
    resolved_home = requested_home.resolve()
    target = tray_descriptor_path("windows", resolved_home)
    assert target is not None
    _safe_descriptor_parent(target, resolved_home)
    _assert_no_symlink_components(target, "tray autostart descriptor")
    executable = Path(python or sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        raise ValueError("pythonw.exe is required for hidden tray autostart")
    quoted = str(pythonw).replace('"', '""')
    payload = (
        'Option Explicit\r\nDim shell\r\nSet shell = CreateObject("WScript.Shell")\r\n'
        f'shell.Run Chr(34) & "{quoted}" & Chr(34) & '
        '" -m soul_platform.tray", 0, False\r\n'
    ).encode()
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


def _desktop_self_check() -> dict[str, bool]:
    found = {
        "pillow": importlib.util.find_spec("PIL") is not None,
        "pystray": importlib.util.find_spec("pystray") is not None,
    }
    if not all(found.values()):
        return found
    from PIL import Image

    found["pillow_image"] = Image is not None
    if sys.platform.startswith("win"):
        import pystray

        found["pystray_import"] = pystray is not None
    return found


def _icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((5, 5, 59, 59), fill=(124, 58, 237, 255))
    draw.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 255))
    return image


def _install_ui_dispatch(icon) -> None:
    """Install a UI-thread callback queue for the pinned pystray Win32 backend."""
    if not sys.platform.startswith("win"):
        raise RuntimeError("the visual tray is currently supported on Windows only")
    handlers = getattr(icon, "_message_handlers", None)
    if not isinstance(handlers, dict):
        raise TypeError("unsupported pystray Win32 backend")
    callbacks: queue.Queue[Callable[[], None]] = queue.Queue()

    def dispatch(_wparam, _lparam) -> None:
        while True:
            try:
                callback = callbacks.get_nowait()
            except queue.Empty:
                return
            callback()

    handlers[WINDOWS_UI_MESSAGE] = dispatch
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    post_message = user32.PostMessageW
    post_message.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    post_message.restype = wintypes.BOOL

    def post(callback: Callable[[], None]) -> None:
        hwnd = getattr(icon, "_hwnd", None)
        if not hwnd:
            raise RuntimeError("tray message window is not ready")
        callbacks.put(callback)
        if not post_message(hwnd, WINDOWS_UI_MESSAGE, 0, 0):
            try:
                callbacks.get_nowait()
            except queue.Empty:
                pass
            raise OSError(ctypes.get_last_error(), "PostMessageW failed")

    icon._soul_post_ui = post


def _run_tray_work(
    icon,
    busy: threading.Lock,
    operation: Callable[[], object],
    complete: Callable[[object | None, Exception | None], None],
) -> threading.Thread:
    """Run bounded I/O off the Win32 loop and marshal every UI touch back."""

    def run() -> None:
        result: object | None = None
        error: Exception | None = None
        try:
            result = operation()
        except Exception as exc:  # noqa: BLE001 - shown locally by the UI callback
            error = exc

        def apply() -> None:
            try:
                complete(result, error)
            finally:
                busy.release()

        try:
            icon._soul_post_ui(apply)
        except (OSError, RuntimeError):
            # Posting can fail only while the tray is stopping. Never leave the
            # action lock held; do not touch pystray from this worker thread.
            busy.release()

    worker = threading.Thread(target=run, name="soul-tray-action", daemon=True)
    worker.start()
    return worker


def build_menu(controller: SoulTrayController, icon):
    import pystray

    busy = getattr(icon, "_soul_busy", None)
    if busy is None:
        busy = icon._soul_busy = threading.Lock()

    def notify(message: str) -> None:
        icon.notify(message, "SOUL")

    def rebuild() -> None:
        icon.menu = build_menu(controller, icon)

    def background(operation: Callable[[], TrayStatus], success: str) -> None:
        if not busy.acquire(blocking=False):
            notify("SOUL ya está procesando otra acción")
            return

        def complete(result: object | None, error: Exception | None) -> None:
            if error is None and isinstance(result, TrayStatus):
                icon._soul_state = result
                notify(f"{success}: {result.detail}")
            else:
                notify(f"No se pudo completar: {error or 'resultado inválido'}")
            rebuild()

        _run_tray_work(icon, busy, operation, complete)

    def state_label(_item) -> str:
        state = icon._soul_state
        if state.ready:
            return f"🟢 Alma ACTIVA · {state.model}"
        if state.active:
            return f"🟡 Alma activa · {state.detail}"
        return "⚪ Alma apagada"

    def toggle(_icon, _item) -> None:
        state = icon._soul_state
        if state.active:
            background(controller.turn_off, "Alma apagada; memoria preservada")
            return
        models = icon._soul_models
        model = state.model or (models[0] if models else None)
        if not model:
            notify("No encontré modelos en Ollama")
            return
        background(lambda: controller.turn_on(model), f"Alma encendida con {model}")

    def choose(model: str):
        def callback(_icon, _item) -> None:
            background(lambda: controller.turn_on(model), f"Cerebro cambiado a {model}")

        return callback

    def copy_endpoint(_icon, _item) -> None:
        value = icon._soul_state.endpoint
        notify(
            "Endpoint copiado"
            if value and copy_to_clipboard(value)
            else f"Endpoint: {value}"
        )

    def copy_token(_icon, _item) -> None:
        try:
            copied = copy_to_clipboard(controller.token())
        except (OSError, ValueError):
            notify("Primero encendé/configurá el alma")
            return
        notify(
            "Token local copiado; tratálo como secreto"
            if copied
            else "No pude usar el portapapeles"
        )

    def refresh(_icon, _item) -> None:
        if not busy.acquire(blocking=False):
            notify("SOUL ya está procesando otra acción")
            return

        def operation() -> tuple[TrayStatus, list[str]]:
            return controller.status(), discover_ollama_models()

        def complete(result: object | None, error: Exception | None) -> None:
            if error is None and isinstance(result, tuple):
                icon._soul_state, icon._soul_models = result
                notify("Modelos y estado actualizados")
            else:
                notify(f"No se pudo actualizar: {error or 'resultado inválido'}")
            rebuild()

        _run_tray_work(icon, busy, operation, complete)

    models = icon._soul_models
    model_items = [
        pystray.MenuItem(
            model,
            choose(model),
            checked=lambda _item, value=model: icon._soul_state.model == value,
            radio=True,
        )
        for model in models
    ]
    if not model_items:
        model_items = [pystray.MenuItem("(Ollama sin modelos)", None, enabled=False)]
    return pystray.Menu(
        pystray.MenuItem(state_label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Prender / apagar alma", toggle),
        pystray.MenuItem("Elegir cerebro", pystray.Menu(*model_items)),
        pystray.MenuItem("Actualizar modelos", refresh),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Copiar endpoint para apps", copy_endpoint),
        pystray.MenuItem("Copiar token local", copy_token),
        pystray.MenuItem(
            "Abrir carpeta SOUL", lambda _ic, _it: controller.open_data_folder()
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Cerrar esta interfaz", lambda tray, _it: tray.stop()),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="soul-tray")
    parser.add_argument(
        "--check",
        action="store_true",
        help="print live status/models; exit nonzero unless the soul is ready",
    )
    parser.add_argument("--check-desktop", action="store_true")
    parser.add_argument("--install-autostart", action="store_true")
    parser.add_argument("--remove-autostart", action="store_true")
    args = parser.parse_args(argv)
    controller = SoulTrayController()
    if args.check_desktop:
        payload = _desktop_self_check()
        print(json.dumps(payload, sort_keys=True))
        return 0 if all(payload.values()) else 2
    if args.install_autostart:
        target = install_tray_autostart(home=controller.home, python=controller.python)
        print(target)
        return 0
    if args.remove_autostart:
        from soul_platform.autostart import disable_tray_descriptor

        target = disable_tray_descriptor(controller.platform, home=controller.home)
        print(target or "tray autostart not used on this platform")
        return 0
    if args.check:
        payload = asdict(controller.status())
        payload["ollama_models"] = discover_ollama_models()
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if payload["ready"] is True else 3
    if not sys.platform.startswith("win"):
        print(
            "The visual SOUL tray currently supports Windows only; "
            "use soul-tray-cli --check on this platform.",
            file=sys.stderr,
        )
        return 2
    try:
        import pystray
    except ImportError:
        print(
            "Install the desktop extra: pip install 'soul-platform[desktop]'",
            file=sys.stderr,
        )
        return 2
    instance = _SingleInstance(controller.root / "tray.lock")
    if not instance.acquire():
        print("SOUL tray is already running", file=sys.stderr)
        return 0
    try:
        icon = pystray.Icon("soul", _icon_image(), "SOUL — el alma de tu máquina")
        _install_ui_dispatch(icon)
        icon._soul_state = controller.status()
        icon._soul_models = discover_ollama_models()
        icon.menu = build_menu(controller, icon)
        icon.run()
        return 0
    finally:
        instance.release()


if __name__ == "__main__":
    raise SystemExit(main())
