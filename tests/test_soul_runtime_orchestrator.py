"""Adversarial tests for the SOUL F2 local-runtime orchestrator."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "memory"
sys.path.insert(0, str(MEMORY))

from soul_event_interface import HookRegistration, hooks_for  # noqa: E402
import soul_runtime_orchestrator as f2  # noqa: E402


def test_matchers_use_runtime_regex_semantics() -> None:
    registry = [
        HookRegistration("on_tool_call", "edit.py", matcher="Edit|Write|MultiEdit"),
        HookRegistration("on_tool_call", "all.py", matcher=".*"),
        HookRegistration("on_tool_call", "read.py", matcher="Read"),
    ]
    selected = hooks_for(registry, "on_tool_call", "ADA", {"tool_name": "Edit"})
    assert [hook.script_path for hook in selected] == ["all.py", "edit.py"]


def test_matchers_cover_boot_source_and_fail_closed_without_candidate() -> None:
    registry = [
        HookRegistration("on_boot", "startup.sh", matcher="startup"),
        HookRegistration("on_boot", "compact.sh", matcher="compact"),
    ]
    assert [h.script_path for h in hooks_for(registry, "on_boot", "ADA", {"source": "startup"})] == ["startup.sh"]
    assert hooks_for(registry, "on_boot", "ADA", {}) == []


def test_matcher_dot_star_applies_without_candidate() -> None:
    registry = [HookRegistration("on_turn_end", "extract.py", matcher=".*")]
    assert [h.script_path for h in hooks_for(registry, "on_turn_end", "ADA", {})] == ["extract.py"]


def test_malformed_regex_uses_historical_glob_fallback() -> None:
    registry = [HookRegistration("on_file_change", "watch.py", matcher="*.jsonl")]
    selected = hooks_for(registry, "on_file_change", "ADA", {"path": "/tmp/ada.jsonl"})
    assert [hook.script_path for hook in selected] == ["watch.py"]


def test_literal_dot_does_not_widen_and_all_payload_candidates_are_considered() -> None:
    registry = [HookRegistration("on_file_change", "watch.py", matcher="ada.jsonl")]
    assert hooks_for(registry, "on_file_change", "ADA", {"path": "/tmp/adaXjsonl"}) == []
    selected = hooks_for(
        registry,
        "on_file_change",
        "ADA",
        {"source": "generic", "path": "/tmp/ada.jsonl"},
    )
    assert [hook.script_path for hook in selected] == ["watch.py"]


def test_invalid_explicit_regex_fails_closed() -> None:
    registry = [HookRegistration("on_tool_call", "bad.py", matcher="Edit|(")]
    assert hooks_for(registry, "on_tool_call", "ADA", {"tool_name": "Edit"}) == []


def test_private_dsn_rejects_weak_mode(tmp_path: Path) -> None:
    path = tmp_path / "runtime.dsn"
    path.write_text("postgresql://runtime:secret@localhost/db", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(f2.RuntimeIdentityError, match="0600"):
        f2.read_private_dsn(path)


def test_private_dsn_accepts_owner_only_regular_file(tmp_path: Path) -> None:
    path = tmp_path / "runtime.dsn"
    path.write_text("postgresql://runtime:secret@localhost/db", encoding="utf-8")
    path.chmod(0o600)
    assert f2.read_private_dsn(path).startswith("postgresql://runtime:")


def test_hook_environment_replaces_all_privileged_db_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEAL_DB_DSN", "postgresql://seal:superuser@localhost/db")
    monkeypatch.setenv("SEAL_DB_URL", "postgresql://seal:superuser@localhost/db")
    monkeypatch.setenv("PGPASSWORD", "superuser")
    monkeypatch.setenv("SEAL_SESSION_TOKEN", "wrong-agent-token")
    executor = f2.HookExecutor("ADA", "postgresql://mcp_runtime_ada:restricted@localhost/db")
    env = executor._environment("session")
    assert env["SEAL_PG_DSN"].startswith("postgresql://mcp_runtime_ada:")
    assert env["SEAL_DB_DSN"] == env["SEAL_PG_DSN"]
    assert env["SEAL_DB_URL"] == env["SEAL_PG_DSN"]
    assert "PGPASSWORD" not in env
    assert "SEAL_SESSION_TOKEN" not in env
    assert "SEAL_TOKENS_DIR" not in env
    assert env["PATH"] == f2.TRUSTED_PATH
    assert env["SEAL_AGENT"] == "ADA"


def test_child_pg_dsn_cannot_fall_back_to_superuser_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """All aliases must resolve to the runtime role before a hook imports seal_secrets."""
    monkeypatch.setenv("SEAL_DB_DSN", "postgresql://seal:superuser@localhost/db")
    executor = f2.HookExecutor("ADA", "postgresql://mcp_runtime_ada:restricted@localhost/db")
    env = executor._environment("session")
    assert {env[name] for name in ("SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN")} == {
        "postgresql://mcp_runtime_ada:restricted@localhost/db"
    }


def test_local_adapter_covers_all_nine_soul_events() -> None:
    from soul_event_interface import SOUL_EVENTS
    from soul_runtime_adapter import get_adapter

    assert get_adapter("local_llama").emits() == set(SOUL_EVENTS)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1/chat/completions",
        "http://192.168.68.200/v1/chat/completions",
        "http://user:pass@127.0.0.1:8899/v1/chat/completions",
        "http://127.0.0.1:8899/v1/chat/completions?leak=1",
        "http://127.0.0.1:8899/arbitrary",
    ],
)
def test_runtime_rejects_non_loopback_or_unapproved_llm_urls(url: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        f2.SoulRuntimeOrchestrator("ADA", llm_url=url)


def test_hook_path_escape_is_rejected(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('{}')", encoding="utf-8")
    executor = f2.HookExecutor("ADA", "postgresql://restricted", allowed_roots=[allowed])
    with pytest.raises(f2.HookPathError, match="escaped"):
        executor._resolve_script(str(outside))


def test_hook_fd_remains_bound_when_path_is_replaced(tmp_path: Path) -> None:
    script = tmp_path / "hook.py"
    script.write_text("print('trusted')\n", encoding="utf-8")
    executor = f2.HookExecutor("ADA", "postgresql://restricted", allowed_roots=[tmp_path])
    _resolved, fd = executor._open_script(str(script))
    try:
        script.unlink()
        script.write_text("print('replaced')\n", encoding="utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        assert os.read(fd, 4096) == b"print('trusted')\n"
    finally:
        os.close(fd)


def test_hook_executes_with_restricted_identity_and_extracts_context(tmp_path: Path) -> None:
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,os,sys\n"
        "event=json.load(sys.stdin)\n"
        "assert os.environ['SEAL_PG_DSN']=='postgresql://restricted'\n"
        "assert os.environ['SEAL_DB_DSN']=='postgresql://restricted'\n"
        "assert os.environ['SEAL_DB_URL']=='postgresql://restricted'\n"
        "print(json.dumps({'hookSpecificOutput':{'additionalContext':event['prompt']}}))\n",
        encoding="utf-8",
    )
    executor = f2.HookExecutor("ADA", "postgresql://restricted", allowed_roots=[tmp_path])
    effect = executor.run(
        HookRegistration("on_prompt", str(script)),
        {
            "session_id": "s1", "soul_event": "on_prompt", "runtime": "local_llama",
            "payload": {"prompt": "recuerdo portátil"},
        },
    )
    assert effect.ok is True
    assert effect.context() == "recuerdo portátil"


class FakeConnection:
    def __init__(self, *, superuser: bool = False, resolved_agent: str = "ADA") -> None:
        self.superuser = superuser
        self.resolved_agent = resolved_agent
        self.closed = False

    async def fetchrow(self, _sql: str):
        return {
            "session_user": "mcp_runtime_ada",
            "current_user": "mcp_runtime_ada",
            "resolved_agent": self.resolved_agent,
            "superuser": self.superuser,
        }

    async def fetch(self, _sql: str, soul_event: str):
        return [
            {
                "soul_event": soul_event, "script_path": "/repo/hook.py",
                "kind": "learning", "agent": None, "enabled": True,
                "ordering": 100, "matcher": None,
            }
        ]

    async def close(self) -> None:
        self.closed = True


def test_store_rejects_superuser_even_if_name_looks_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dsn = tmp_path / "ada.dsn"
    dsn.write_text("postgresql://mcp_runtime_ada:x@localhost/db", encoding="utf-8")
    dsn.chmod(0o600)
    conn = FakeConnection(superuser=True)

    async def fake_connect(_dsn: str):
        return conn

    monkeypatch.setattr(f2, "connect_postgres", fake_connect)
    async def exercise() -> None:
        async with f2.RuntimeHookStore("ADA", dsn):
            pass

    with pytest.raises(f2.RuntimeIdentityError, match="non-superuser"):
        asyncio.run(exercise())
    assert conn.closed is True


def test_store_rejects_cross_agent_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dsn = tmp_path / "ada.dsn"
    dsn.write_text("postgresql://mcp_runtime_ada:x@localhost/db", encoding="utf-8")
    dsn.chmod(0o600)
    conn = FakeConnection(resolved_agent="NEXUS")

    async def fake_connect(_dsn: str):
        return conn

    monkeypatch.setattr(f2, "connect_postgres", fake_connect)
    async def exercise() -> None:
        async with f2.RuntimeHookStore("ADA", dsn):
            pass

    with pytest.raises(f2.RuntimeIdentityError, match="non-superuser"):
        asyncio.run(exercise())


def test_store_accepts_exact_runtime_identity_and_loads_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    dsn = tmp_path / "ada.dsn"
    dsn.write_text("postgresql://mcp_runtime_ada:x@localhost/db", encoding="utf-8")
    dsn.chmod(0o600)
    conn = FakeConnection()

    async def fake_connect(_dsn: str):
        return conn

    monkeypatch.setattr(f2, "connect_postgres", fake_connect)
    async def exercise() -> None:
        async with f2.RuntimeHookStore("ADA", dsn) as store:
            rows = await store.load("on_prompt")
            assert store.identity is not None
            assert store.identity.superuser is False
            assert rows == [HookRegistration("on_prompt", "/repo/hook.py")]

    asyncio.run(exercise())


def test_unknown_agent_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        f2.SoulRuntimeOrchestrator("seal")


def test_runtime_transcript_is_hashed_owner_only_jsonl(tmp_path: Path) -> None:
    orchestrator = f2.SoulRuntimeOrchestrator(
        "ADA", session_id="../../unsafe/session", state_root=tmp_path / "state",
    )
    orchestrator._append_transcript("user", "hola")
    orchestrator._append_transcript("assistant", "respuesta")
    assert stat.S_IMODE(orchestrator.transcript_path.stat().st_mode) == 0o600
    rows = [json.loads(line) for line in orchestrator.transcript_path.read_text().splitlines()]
    assert rows == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "respuesta"},
    ]


def test_runtime_state_rejects_symlink_parent(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)
    with pytest.raises(f2.RuntimeIdentityError, match="symlink"):
        f2.SoulRuntimeOrchestrator("ADA", state_root=linked)


def test_complete_reinjects_boot_context_and_multi_turn_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    orchestrator = f2.SoulRuntimeOrchestrator("ADA", state_root=tmp_path / "state")
    observed_messages: list[list[dict[str, str]]] = []

    async def fake_emit(native_event: str, _payload: dict) -> dict:
        contexts = {
            "session_start": ["BOOT_IDENTITY"],
            "user_message": ["PROMPT_RECALL"],
            "turn_complete": [],
        }[native_event]
        return {
            "soul_event": native_event,
            "selected": [],
            "effects": [],
            "contexts": contexts,
            "all_ran": True,
        }

    answers = iter(("A1", "A2"))

    def fake_http(_url: str, payload: dict | None, _timeout: float):
        assert payload is not None
        observed_messages.append(payload["messages"])
        return 200, {"choices": [{"message": {"content": next(answers)}}]}

    monkeypatch.setattr(orchestrator, "emit", fake_emit)
    monkeypatch.setattr(f2, "http_json", fake_http)
    asyncio.run(orchestrator.complete("U1"))
    asyncio.run(orchestrator.complete("U2"))

    assert "BOOT_IDENTITY" in observed_messages[0][0]["content"]
    assert "PROMPT_RECALL" in observed_messages[0][0]["content"]
    assert observed_messages[0][1:] == [{"role": "user", "content": "U1"}]
    assert "BOOT_IDENTITY" in observed_messages[1][0]["content"]
    assert observed_messages[1][1:] == [
        {"role": "user", "content": "U1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "U2"},
    ]


def test_memory_extractor_rejects_transcript_outside_allowed_roots(tmp_path: Path) -> None:
    import memory_extraction_hook

    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"role":"user","content":"secret"}\n', encoding="utf-8")
    assert memory_extraction_hook.resolve_transcript({"transcript_path": str(outside)}, "") == ""


def test_local_memory_extractor_rejects_other_agent_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import memory_extraction_hook as hook

    state = tmp_path / "soul-runtime"
    ada_root = state / "ada"
    nexus_root = state / "nexus"
    ada_root.mkdir(parents=True)
    nexus_root.mkdir(parents=True)
    own = ada_root / "turn.jsonl"
    other = nexus_root / "turn.jsonl"
    own.write_text('{"role":"user","content":"own"}\n', encoding="utf-8")
    other.write_text('{"role":"user","content":"other"}\n', encoding="utf-8")
    own.chmod(0o600)
    other.chmod(0o600)
    monkeypatch.setenv("SOUL_RUNTIME", "local_llama")
    monkeypatch.setenv("SEAL_AGENT", "ADA")
    monkeypatch.setattr(hook, "_transcript_roots", lambda _agent=None: (ada_root,))
    assert hook.resolve_transcript({"transcript_path": str(own)}, "", "ADA") == str(own)
    assert hook.resolve_transcript({"transcript_path": str(other)}, "", "ADA") == ""


def test_local_memory_extractor_rejects_group_readable_transcript(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import memory_extraction_hook as hook

    root = tmp_path / "ada"
    root.mkdir()
    transcript = root / "turn.jsonl"
    transcript.write_text('{"role":"user","content":"private"}\n', encoding="utf-8")
    transcript.chmod(0o640)
    monkeypatch.setenv("SOUL_RUNTIME", "local_llama")
    monkeypatch.setenv("SEAL_AGENT", "ADA")
    monkeypatch.setattr(hook, "_transcript_roots", lambda _agent=None: (root,))
    assert hook.read_last_exchange(str(transcript), "ADA") == ""


def test_memory_store_guard_writes_current_schema_without_shadowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the false-green F2 hook: UnboundLocal + retired column."""
    import memory_extraction_hook as hook

    captured: dict[str, object] = {}

    class FakeConn:
        async def fetchval(self, query: str, *args: object):
            if "SELECT id FROM memories" in query:
                return None
            captured["query"] = query
            captured["args"] = args
            return 42

        async def close(self) -> None:
            captured["closed"] = True

    async def fake_connect(_dsn: str) -> FakeConn:
        return FakeConn()

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=fake_connect))
    monkeypatch.setattr(hook, "memory_auto_event_skip_reason", lambda **_kwargs: None)
    monkeypatch.setattr(
        hook,
        "normalize_memory_importance_for_write",
        lambda **_kwargs: SimpleNamespace(importance=7, content="guarded content"),
    )

    assert hook.store_memory_db("ADA", "original content", "feedback", 9, ["test"]) is True
    assert "created_at, metadata" in str(captured["query"])
    assert "provenance" not in str(captured["query"])
    args = captured["args"]
    assert isinstance(args, tuple)
    assert args[2:4] == ("guarded content", 7)
    assert json.loads(str(args[-1]))["tags"] == ["test"]
    assert captured["closed"] is True


def test_autodream_disabled_is_clean_skip_not_hook_failure() -> None:
    script = ROOT / "skills" / "dream" / "autodream_8gates.sh"
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(Path.home()), "SEAL_AGENT": "ADA", "AUTODREAM_ENABLED": "0"}
    completed = subprocess.run(["/bin/bash", str(script)], env=env, text=True, capture_output=True, timeout=5, check=False)
    assert completed.returncode == 0


def test_source_contains_no_privileged_helper() -> None:
    source = Path(f2.__file__).read_text(encoding="utf-8")
    assert re.search(r"^\s*from\s+seal_secrets\s+import\s+pg_dsn", source, re.MULTILINE) is None
    assert re.search(r"(?<![.\w])pg_dsn\s*\(", source) is None
    assert "mcp_runtime_" in source
