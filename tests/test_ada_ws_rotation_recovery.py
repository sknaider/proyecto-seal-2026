from __future__ import annotations

import ast
import importlib.util
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MESSAGES = Path(os.environ.get("ADA_WS_MESSAGES_DIR", ROOT / "messages"))


def _load_module(name: str, path: Path):
    sys.path.insert(0, str(MESSAGES))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_qa_positive_ws_listener_reloads_token_inside_each_reconnect_loop() -> None:
    tree = ast.parse((MESSAGES / "ws_listener.py").read_text())

    for function_name in ("listen_aiohttp", "listen_websockets"):
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name
        )
        reconnect_loop = next(node for node in ast.walk(function) if isinstance(node, ast.While))
        reload_calls = [
            node
            for node in ast.walk(reconnect_loop)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_load_agent_token"
        ]
        cached_before_loop = [
            node
            for node in function.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(name, ast.Name) and name.id == "agent_token"
                for name in ast.walk(node)
            )
        ]

        assert len(reload_calls) == 1, function_name
        assert cached_before_loop == [], function_name


def test_qa_negative_agent_bridge_passes_required_rate_state_to_filter(tmp_path, monkeypatch) -> None:
    bridge = _load_module("agent_bridge_rotation_test", MESSAGES / "agent_bridge.py")
    bridge.INBOX_DIR = tmp_path
    observed: list[dict] = []

    def fake_filter(line: str, agent: str, rate_state: dict) -> str:
        assert agent == "ADA"
        observed.append(rate_state)
        return line

    monkeypatch.setattr(bridge, "filter_line", fake_filter)
    state: dict = {}
    message = {
        "id": "rotation-test",
        "from": "William",
        "to": "ADA",
        "type": "conversation",
        "channel": "web_chat",
        "message": "prueba",
    }

    bridge.write_inbox("ADA", message, state)
    bridge.write_inbox("ADA", message, state)

    assert observed == [state, state]
    assert (tmp_path / "seal_inbox_ADA.jsonl").read_text().count("rotation-test") == 2


def test_qa_control_the_old_two_argument_filter_call_is_rejected() -> None:
    monitor_filter = _load_module(
        "seal_monitor_filter_rotation_control", MESSAGES / "seal_monitor_filter.py"
    )

    with pytest.raises(TypeError, match="rate_state"):
        monitor_filter.filter_line("{}", "ADA")
