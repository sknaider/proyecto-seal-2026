from __future__ import annotations

from pathlib import Path

from mcp_web_soul_visual import VisualTakeoverRuntime


def test_visual_display_and_viewer_live_then_cleanup():
    runtime = VisualTakeoverRuntime(width=640, height=480)
    try:
        runtime.start_display()
        assert runtime.status()["visual_session"] is True
        assert Path(f"/tmp/.X11-unix/X{runtime.display_number}").exists()
        url = runtime.start_viewer()
        assert url.startswith("http://127.0.0.1:")
        assert runtime.status()["viewer_active"] is True
        assert runtime.status()["exposure"] == "loopback-only"
    finally:
        display_number = runtime.display_number
        runtime.close()
    assert runtime.status()["visual_session"] is False
    assert not Path(f"/tmp/.X11-unix/X{display_number}").exists()
