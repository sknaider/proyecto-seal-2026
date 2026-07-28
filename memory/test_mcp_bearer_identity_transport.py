from __future__ import annotations

import ast
from pathlib import Path


MCP_SERVER = Path(__file__).with_name("mcp_server_v4.py")


def _async_def(tree: ast.AST, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing async function: {name}")


def test_identity_tools_do_not_publish_session_token_argument() -> None:
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"))
    for name in ("boot_context", "announce_agent"):
        func = _async_def(tree, name)
        assert [arg.arg for arg in func.args.args] == ["agent"]


def test_identity_tools_bind_from_request_bearer_only() -> None:
    source = MCP_SERVER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for name in ("boot_context", "announce_agent"):
        func = _async_def(tree, name)
        func_source = ast.get_source_segment(source, func) or ""
        assert "_session_token_from_request()" in func_source
        assert not any(
            isinstance(node, ast.Name) and node.id == "session_token"
            for node in ast.walk(func)
        )


def test_observed_tools_never_authenticate_from_tool_arguments() -> None:
    source = MCP_SERVER.read_text(encoding="utf-8")
    assert '_sess_tok = _session_token_from_request()' in source
    assert 'kwargs.get("session_token") or _session_token_from_request()' not in source
