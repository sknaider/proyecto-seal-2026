from __future__ import annotations

import hashlib
import json

import pytest

from soul_platform.bootstrap import initialize
from soul_platform.mcp_stdio import (
    MCPStdioServer,
    ProcessIdentity,
    _current_server_executable,
    enroll_client,
    ensure_client_grants,
    verify_client_grant,
)
from soul_platform.proxy import ProxySettings


async def test_mcp_handshake_lists_scoped_tools_and_calls_runner():
    calls = []

    async def runner(name, arguments):
        calls.append((name, arguments))
        return {"content": [{"type": "text", "text": "ok"}]}

    server = MCPStdioServer(runner)
    initialized = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    listing = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    called = await server.handle(
        {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "soul_memory_search", "arguments": {"query": "Valeria"}},
        }
    )
    assert initialized["result"]["serverInfo"]["name"] == "soul-local"
    tools = {item["name"]: item for item in listing["result"]["tools"]}
    assert set(tools) == {"soul_boot_context", "soul_memory_search", "soul_memory_store"}
    assert tools["soul_memory_store"]["annotations"]["readOnlyHint"] is False
    assert called["result"]["content"][0]["text"] == "ok"
    assert calls == [("soul_memory_search", {"query": "Valeria"})]


async def test_mcp_rejects_unknown_method_and_invalid_request():
    async def runner(_name, _arguments):
        raise AssertionError("runner should not be called")

    server = MCPStdioServer(runner)
    missing = await server.handle({"jsonrpc": "2.0", "id": 9, "method": "unknown"})
    assert missing["error"]["code"] == -32601
    with pytest.raises(ValueError, match="JSON-RPC"):
        await server.handle({"id": 1, "method": "ping"})


async def test_mcp_tools_require_fresh_initialize_session():
    async def runner(_name, _arguments):
        return {}

    server = MCPStdioServer(runner)
    with pytest.raises(ValueError, match="session"):
        await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    initialized = await server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}
    )
    assert initialized["result"]["_meta"]["soulAttachSession"]
    listing = await server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    assert listing["result"]["tools"]


def test_client_grant_is_machine_bound_and_fail_closed(tmp_path):
    result = initialize(
        root=tmp_path / "soul", upstream_kind="ollama",
        upstream_base_url="http://127.0.0.1:11434/v1", upstream_model="brain",
        enable_autostart=False,
    )
    settings = ProxySettings.from_toml(result.config)
    grant = ensure_client_grants(settings)
    parent = tmp_path / "codex.exe"
    server = tmp_path / "soul-mcp-stdio.exe"
    parent.write_bytes(b"approved-codex")
    server.write_bytes(b"approved-mcp")
    enroll_client(
        settings,
        "codex",
        parent_executable=parent,
        server_executable=server,
        config_path=result.config,
    )
    identity = ProcessIdentity(
        executable=str(parent.resolve()),
        executable_sha256=hashlib.sha256(parent.read_bytes()).hexdigest(),
        owner=f"uid:{__import__('os').getuid()}",
        session=f"sid:{__import__('os').getsid(0)}",
    )
    verify_client_grant(
        settings,
        "codex",
        config_path=result.config,
        server_executable=server,
        process_identity=identity,
    )
    # Exact re-enrollment is idempotent, but a different executable cannot
    # seize the existing client id.
    enroll_client(
        settings,
        "codex",
        parent_executable=parent,
        server_executable=server,
        config_path=result.config,
    )
    evil = tmp_path / "evil.exe"
    evil.write_bytes(b"same-user-impostor")
    with pytest.raises(ValueError, match="immutable binding"):
        enroll_client(
            settings,
            "codex",
            parent_executable=evil,
            server_executable=server,
            config_path=result.config,
        )
    with pytest.raises(ValueError, match="parent"):
        verify_client_grant(
            settings,
            "codex",
            config_path=result.config,
            server_executable=server,
            process_identity=ProcessIdentity(
                executable=str(tmp_path / "fake.exe"),
                executable_sha256="0" * 64,
                owner=identity.owner,
                session=identity.session,
            ),
        )
    with pytest.raises(ValueError, match="not granted"):
        verify_client_grant(
            settings,
            "claude",
            config_path=result.config,
            server_executable=server,
            process_identity=identity,
        )
    raw = json.loads(grant.read_text())
    raw["machine_soul_id"] = "other"
    grant.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="differs"):
        verify_client_grant(
            settings,
            "codex",
            config_path=result.config,
            server_executable=server,
            process_identity=identity,
        )
    with pytest.raises(ValueError, match="unsupported"):
        verify_client_grant(settings, "unknown")


def test_windows_console_launcher_resolves_hidden_exe_suffix(tmp_path, monkeypatch):
    launcher = tmp_path / "soul-mcp-stdio.exe"
    launcher.write_bytes(b"launcher")
    monkeypatch.setattr("sys.argv", [str(tmp_path / "soul-mcp-stdio")])
    assert _current_server_executable() == launcher.resolve()


def test_sessionless_v2_migrates_once_without_open_rotation(tmp_path):
    result = initialize(
        root=tmp_path / "soul", upstream_kind="ollama",
        upstream_base_url="http://127.0.0.1:11434/v1", upstream_model="brain",
        enable_autostart=False,
    )
    settings = ProxySettings.from_toml(result.config)
    parent = tmp_path / "codex.exe"
    server = tmp_path / "soul-mcp-stdio.exe"
    parent.write_bytes(b"codex-v1")
    server.write_bytes(b"mcp-v1")
    original = enroll_client(
        settings, "codex", parent_executable=parent,
        server_executable=server, config_path=result.config,
    )
    grant = settings.soul_db.parent / "client-grants.json"
    raw = json.loads(grant.read_text())
    del raw["clients"]["codex"]["session"]
    grant.write_text(json.dumps(raw))
    server.write_bytes(b"mcp-v2")
    migrated = enroll_client(
        settings, "codex", parent_executable=parent,
        server_executable=server, config_path=result.config,
    )
    assert migrated["migrated_from"] == "sessionless-v2"
    assert migrated["dormant_hash_finalized"] is True
    assert migrated["previous_server_sha256"] == original["server_sha256"]
    raw = json.loads(grant.read_text())
    del raw["clients"]["codex"]["dormant_hash_finalized"]
    raw["clients"]["codex"]["server_sha256"] = "0" * 64
    grant.write_text(json.dumps(raw))
    repaired = enroll_client(
        settings, "codex", parent_executable=parent,
        server_executable=server, config_path=result.config,
    )
    assert repaired["dormant_hash_finalized"] is True
    assert repaired["previous_server_sha256"] == "0" * 64
    server.write_bytes(b"mcp-v3")
    with pytest.raises(ValueError, match="immutable binding"):
        enroll_client(
            settings, "codex", parent_executable=parent,
            server_executable=server, config_path=result.config,
        )


def test_explicit_enrollment_migrates_legacy_grants_with_exact_backup(tmp_path):
    result = initialize(
        root=tmp_path / "soul", upstream_kind="ollama",
        upstream_base_url="http://127.0.0.1:11434/v1", upstream_model="brain",
        enable_autostart=False,
    )
    settings = ProxySettings.from_toml(result.config)
    grant = settings.soul_db.parent / "client-grants.json"
    legacy = (
        json.dumps(
            {
                "schema": "soul.client-grants.v1",
                "machine_soul_id": settings.machine_soul_id,
                "clients": {"codex": {"enabled": True}, "claude": {"enabled": True}},
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    grant.write_bytes(legacy)
    parent = tmp_path / "node.exe"
    server = tmp_path / "soul-mcp-stdio.exe"
    parent.write_bytes(b"approved-node")
    server.write_bytes(b"approved-mcp")

    with pytest.raises(ValueError, match="differs"):
        ensure_client_grants(settings)
    enroll_client(
        settings,
        "codex",
        parent_executable=parent,
        server_executable=server,
        config_path=result.config,
    )

    migrated = json.loads(grant.read_text())
    assert migrated["schema"] == "soul.client-grants.v2"
    assert set(migrated["clients"]) == {"codex"}
    backups = list(grant.parent.glob("client-grants.json.v1.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == legacy
