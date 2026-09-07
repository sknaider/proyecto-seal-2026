from __future__ import annotations

import socket
import stat
import subprocess
import urllib.parse
from pathlib import Path

import websocket

from mcp_web_soul_visual import VisualTakeoverRuntime


def test_visual_display_and_viewer_live_then_cleanup():
    runtime = VisualTakeoverRuntime(width=640, height=480)
    try:
        runtime.start_display()
        assert runtime.status()["visual_session"] is True
        assert Path(f"/tmp/.X11-unix/X{runtime.display_number}").exists()
        assert runtime.browser_env["XAUTHORITY"] == str(runtime._xauthority)
        assert stat.S_IMODE(runtime._xauthority.stat().st_mode) == 0o600
        url = runtime.start_viewer()
        assert url.startswith("http://127.0.0.1:")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert len(query["token"][0]) >= 32
        assert len(query["password"][0]) == 8
        assert runtime.status()["viewer_active"] is True
        assert runtime.status()["exposure"] == "loopback-authenticated"
        assert stat.S_IMODE(runtime._vnc_password_file.stat().st_mode) == 0o600
        assert stat.S_IMODE(runtime._token_file.stat().st_mode) == 0o600

        denied = False
        try:
            ws = websocket.create_connection(
                f"ws://127.0.0.1:{runtime.novnc_port}/websockify", timeout=2,
                subprotocols=["binary"],
            )
            try:
                denied = ws.recv() in (b"", "", None)
            except Exception:
                denied = True
            finally:
                ws.close()
        except Exception:
            denied = True
        assert denied is True

        token = urllib.parse.quote(query["token"][0])
        ws = websocket.create_connection(
            f"ws://127.0.0.1:{runtime.novnc_port}/websockify?token={token}",
            timeout=2,
            subprotocols=["binary"],
        )
        try:
            banner = ws.recv()
        finally:
            ws.close()
        if isinstance(banner, str):
            banner = banner.encode()
        assert banner.startswith(b"RFB ")

        assert runtime.status()["vnc_direct_exposed"] is False
        assert runtime.status()["container_network"] == "none"
        assert runtime.status()["takeover_url"] is None
        network_mode = subprocess.check_output(
            ["docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", runtime.container_name],
            text=True,
        ).strip()
        assert network_mode == "none"
    finally:
        display_number = runtime.display_number
        runtime_dir = runtime._runtime_dir
        runtime.close()
    assert runtime.status()["visual_session"] is False
    assert not Path(f"/tmp/.X11-unix/X{display_number}").exists()
    assert runtime_dir is not None and not runtime_dir.exists()
