#!/usr/bin/env python3
"""Display visual local y efímero para takeover del mismo CDP nativo.

Xvfb y x11vnc son primitivas del sistema. noVNC se conserva como transporte UI
de bajo nivel dentro de un contenedor local-only; no controla el navegador ni
recibe credenciales de SOUL. Toda la superficie se liga a 127.0.0.1.
"""

from __future__ import annotations

import os
import secrets
import signal
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path


NOVNC_IMAGE = os.environ.get(
    "MCP_WEB_SOUL_NOVNC_IMAGE",
    "sha256:94c28114a0c77a66b6032d34f3a32688be8cef8dca0c81086fb99acebafe8fbe",
)


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
        self.network_name = f"mcp-web-soul-visual-{os.getpid()}"
        self._runtime_dir: Path | None = None
        self._xauthority: Path | None = None
        self._vnc_password_file: Path | None = None
        self._token_file: Path | None = None
        self._relay_socket: Path | None = None
        self._viewer_token = ""
        self._viewer_password = ""
        self.xvfb: subprocess.Popen | None = None
        self.x11vnc: subprocess.Popen | None = None
        self.novnc: subprocess.Popen | None = None
        self.relay: subprocess.Popen | None = None

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
        self._runtime_dir = Path(tempfile.mkdtemp(prefix="mcp-web-soul-visual-"))
        os.chmod(self._runtime_dir, 0o700)
        try:
            self._xauthority = self._runtime_dir / "Xauthority"
            subprocess.run(
                [
                    "/usr/bin/xauth", "-f", str(self._xauthority), "add", self.display,
                    "MIT-MAGIC-COOKIE-1", secrets.token_hex(16),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            listed = subprocess.run(
                ["/usr/bin/xauth", "-f", str(self._xauthority), "nlist", self.display],
                check=True, capture_output=True, timeout=5,
            ).stdout
            if len(listed) < 5:
                raise RuntimeError("xauth no produjo cookie para el display")
            subprocess.run(
                ["/usr/bin/xauth", "-f", str(self._xauthority), "nmerge", "-"],
                input=b"ffff" + listed[4:], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
            os.chmod(self._xauthority, 0o600)
            self.xvfb = subprocess.Popen(
                [
                    "/usr/bin/Xvfb", self.display, "-screen", "0",
                    f"{self.width}x{self.height}x24", "-nolisten", "tcp",
                    "-auth", str(self._xauthority),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            socket_path = Path(f"/tmp/.X11-unix/X{self.display_number}")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if socket_path.exists() and self.xvfb.poll() is None:
                    return
                time.sleep(0.05)
            raise RuntimeError("Xvfb no quedó disponible")
        except Exception:
            self.close()
            raise

    @property
    def browser_env(self) -> dict[str, str]:
        self.start_display()
        return {
            **os.environ,
            "DISPLAY": self.display,
            "XAUTHORITY": str(self._xauthority),
        }

    def start_viewer(self) -> str:
        """Start loopback VNC+noVNC for this exact display, idempotently."""
        self.start_display()
        if self.novnc is not None and self.novnc.poll() is None:
            return self.takeover_url
        self.vnc_port = 5900
        self.novnc_port = _free_port()
        if self._runtime_dir is None or self._xauthority is None:
            raise RuntimeError("display visual sin runtime privado")
        self._viewer_token = secrets.token_urlsafe(32)
        # RFB/VNC clásico solo usa los primeros ocho bytes del password.
        self._viewer_password = secrets.token_urlsafe(6)[:8]
        self._vnc_password_file = self._runtime_dir / "vnc.passwd"
        encoded = subprocess.run(
            ["/usr/bin/vncpasswd", "-f"],
            input=(self._viewer_password + "\n").encode("utf-8"),
            capture_output=True,
            check=True,
            timeout=5,
        ).stdout
        self._vnc_password_file.write_bytes(encoded)
        os.chmod(self._vnc_password_file, 0o600)
        self._token_file = self._runtime_dir / "websockify.tokens"
        self._token_file.write_text(
            f"{self._viewer_token}: 127.0.0.1:{self.vnc_port}\n",
            encoding="utf-8",
        )
        os.chmod(self._token_file, 0o600)
        if not NOVNC_IMAGE.startswith("sha256:"):
            self.stop_viewer()
            raise RuntimeError("MCP_WEB_SOUL_NOVNC_IMAGE debe estar pinneada por sha256")
        try:
            relay_dir = self._runtime_dir / "relay"
            relay_dir.mkdir(mode=0o700)
            self._relay_socket = relay_dir / "websockify.sock"
            self.novnc = subprocess.Popen(
                [
                    "/usr/bin/docker", "run", "--rm", "--network", "none",
                    "--name", self.container_name,
                    "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges",
                    "--pids-limit", "64", "--memory", "128m", "--cpus", "0.5",
                    "--user", f"{os.getuid()}:{os.getgid()}", "--env", "HOME=/tmp",
                    "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
                    "--mount", "type=bind,src=/tmp/.X11-unix,dst=/tmp/.X11-unix,readonly",
                    "--mount", f"type=bind,src={self._xauthority},dst=/run/seal/Xauthority,readonly",
                    "--mount", f"type=bind,src={self._vnc_password_file},dst=/run/seal/vnc.passwd,readonly",
                    "--mount", f"type=bind,src={self._token_file},dst=/run/seal/tokens,readonly",
                    "--mount", f"type=bind,src={relay_dir},dst=/run/relay",
                    "--entrypoint", "/bin/sh", NOVNC_IMAGE, "-ec",
                    (
                        f"/usr/bin/x11vnc -display {self.display} -forever -shared -rfbport 5900 "
                        "-localhost -noshm -rfbauth /run/seal/vnc.passwd "
                        "-auth /run/seal/Xauthority -xkb "
                        ">&2 & vnc_pid=$!; "
                        "trap 'kill $vnc_pid 2>/dev/null || true' EXIT TERM INT; "
                        "/usr/bin/websockify --web /usr/share/novnc --file-only "
                        "--token-plugin TokenFile --token-source /run/seal/tokens "
                        "--unix-listen=/run/relay/websockify.sock --unix-listen-mode=0600"
                    ),
                ],
                stdout=subprocess.DEVNULL,
                # Es un daemon de vida larga: un PIPE sin consumidor puede
                # llenarse y congelar x11vnc/websockify. La salud se prueba por
                # los endpoints RFB/HTTP, no por texto de stderr.
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            socket_deadline = time.monotonic() + 5
            while time.monotonic() < socket_deadline:
                if self._relay_socket.exists() or self.novnc.poll() is not None:
                    break
                time.sleep(0.05)
            if not self._relay_socket.exists():
                raise RuntimeError("viewer container sin socket Unix")
            self.relay = subprocess.Popen(
                [
                    "/usr/bin/socat", f"TCP-LISTEN:{self.novnc_port},bind=127.0.0.1,reuseaddr,fork",
                    f"UNIX-CONNECT:{self._relay_socket}",
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True,
            )
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.novnc.poll() is not None or self.relay.poll() is not None:
                    break
                try:
                    with socket.create_connection(
                        ("127.0.0.1", self.novnc_port), timeout=0.3,
                    ) as probe:
                        probe.sendall(
                            (
                                "GET /vnc.html HTTP/1.0\r\n"
                                "Host: 127.0.0.1\r\nConnection: close\r\n\r\n"
                            ).encode()
                        )
                        web_ready = b"200 OK" in probe.recv(256)
                        vnc_ready = subprocess.run(
                            [
                                "/usr/bin/docker", "exec", self.container_name, "/usr/bin/python3", "-c",
                                (
                                    "import socket; s=socket.create_connection(('127.0.0.1',5900),.3); "
                                    "assert s.recv(12).startswith(b'RFB '); s.close()"
                                ),
                            ],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            timeout=1, check=False,
                        ).returncode == 0
                        if web_ready and vnc_ready:
                            return self.takeover_url
                except OSError:
                    time.sleep(0.1)
            raise RuntimeError("viewer noVNC no quedo disponible")
        except Exception:
            self.stop_viewer()
            raise

    @property
    def takeover_url(self) -> str:
        if self.novnc_port is None:
            return ""
        query = urllib.parse.urlencode({
            "autoconnect": "true",
            "resize": "scale",
            "token": self._viewer_token,
            "password": self._viewer_password,
        })
        return f"http://127.0.0.1:{self.novnc_port}/vnc.html?{query}"

    def status(self, *, include_credentials: bool = False) -> dict:
        return {
            "display": self.display or None,
            "visual_session": bool(self.xvfb and self.xvfb.poll() is None),
            "viewer_active": bool(self.novnc and self.novnc.poll() is None),
            "takeover_url": self.takeover_url if include_credentials else None,
            "exposure": "loopback-authenticated",
            "x11_access_control": self._xauthority is not None,
            "vnc_password_required": self._vnc_password_file is not None,
            "websocket_token_required": self._token_file is not None,
            "vnc_direct_exposed": False,
            "container_network": "none",
        }

    def stop_viewer(self) -> None:
        if self.novnc is not None:
            subprocess.run(
                ["/usr/bin/docker", "rm", "-f", self.container_name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                check=False,
            )
        _terminate(self.novnc)
        _terminate(self.relay)
        _terminate(self.x11vnc)
        self.novnc = None
        self.relay = None
        self.x11vnc = None
        self.vnc_port = None
        self.novnc_port = None
        self._viewer_token = ""
        self._viewer_password = ""
        self._vnc_password_file = None
        self._token_file = None
        self._relay_socket = None

    def close(self) -> None:
        self.stop_viewer()
        _terminate(self.xvfb)
        self.xvfb = None
        if self._runtime_dir is not None:
            shutil.rmtree(self._runtime_dir, ignore_errors=True)
        self._runtime_dir = None
        self._xauthority = None
