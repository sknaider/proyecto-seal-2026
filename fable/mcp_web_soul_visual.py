#!/usr/bin/env python3
"""Display visual local y efímero para takeover del mismo CDP nativo.

Xvfb y x11vnc son primitivas del sistema. noVNC se conserva como transporte UI
de bajo nivel dentro de un contenedor local-only; no controla el navegador ni
recibe credenciales de SOUL. Toda la superficie se liga a 127.0.0.1.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path


NOVNC_IMAGE = os.environ.get("MCP_WEB_SOUL_NOVNC_IMAGE", "auto-browser-browser-node:latest")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=3)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=1)
        except Exception:
            pass


class VisualTakeoverRuntime:
    """Owns an isolated X display and an on-demand loopback noVNC viewer."""

    def __init__(self, *, width: int = 1280, height: int = 800) -> None:
        self.width = int(width)
        self.height = int(height)
        self.display_number: int | None = None
        self.display = ""
        self.vnc_port: int | None = None
        self.novnc_port: int | None = None
        self.container_name = f"mcp-web-soul-novnc-{os.getpid()}"
        self.xvfb: subprocess.Popen | None = None
        self.x11vnc: subprocess.Popen | None = None
        self.novnc: subprocess.Popen | None = None

    def _choose_display(self) -> int:
        base = 100 + (os.getpid() % 1800)
        for number in range(base, base + 100):
            if not Path(f"/tmp/.X11-unix/X{number}").exists():
                return number
        raise RuntimeError("no hay display X libre para takeover")

    def start_display(self) -> None:
        if self.xvfb is not None and self.xvfb.poll() is None:
            return
        self.display_number = self._choose_display()
        self.display = f":{self.display_number}"
        self.xvfb = subprocess.Popen(
            [
                "/usr/bin/Xvfb", self.display, "-screen", "0",
                f"{self.width}x{self.height}x24", "-nolisten", "tcp", "-ac",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        socket_path = Path(f"/tmp/.X11-unix/X{self.display_number}")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if socket_path.exists() and self.xvfb.poll() is None:
                return
            time.sleep(0.05)
        self.close()
        raise RuntimeError("Xvfb no quedó disponible")

    @property
    def browser_env(self) -> dict[str, str]:
        self.start_display()
        return {**os.environ, "DISPLAY": self.display}

    def start_viewer(self) -> str:
        """Start loopback VNC+noVNC for this exact display, idempotently."""
        self.start_display()
        if self.novnc is not None and self.novnc.poll() is None:
            return self.takeover_url
        self.vnc_port = _free_port()
        self.novnc_port = _free_port()
        self.x11vnc = subprocess.Popen(
            [
                "/usr/bin/x11vnc", "-display", self.display, "-forever", "-shared",
                "-rfbport", str(self.vnc_port), "-localhost", "-nopw", "-xkb",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.novnc = subprocess.Popen(
            [
                "/usr/bin/docker", "run", "--rm", "--network", "host",
                "--name", self.container_name,
                "--entrypoint", "/usr/bin/websockify", NOVNC_IMAGE,
                "--web", "/usr/share/novnc", f"127.0.0.1:{self.novnc_port}",
                f"127.0.0.1:{self.vnc_port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self.x11vnc.poll() is not None or self.novnc.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", self.novnc_port), timeout=0.2):
                    return self.takeover_url
            except OSError:
                time.sleep(0.1)
        self.stop_viewer()
        raise RuntimeError("viewer noVNC no quedó disponible")

    @property
    def takeover_url(self) -> str:
        if self.novnc_port is None:
            return ""
        return (
            f"http://127.0.0.1:{self.novnc_port}/vnc.html"
            "?autoconnect=true&resize=scale"
        )

    def status(self) -> dict:
        return {
            "display": self.display or None,
            "visual_session": bool(self.xvfb and self.xvfb.poll() is None),
            "viewer_active": bool(self.novnc and self.novnc.poll() is None),
            "takeover_url": self.takeover_url or None,
            "exposure": "loopback-only",
        }

    def stop_viewer(self) -> None:
        if self.novnc is not None:
            subprocess.run(
                ["/usr/bin/docker", "rm", "-f", self.container_name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                check=False,
            )
        _terminate(self.novnc)
        _terminate(self.x11vnc)
        self.novnc = None
        self.x11vnc = None

    def close(self) -> None:
        self.stop_viewer()
        _terminate(self.xvfb)
        self.xvfb = None

