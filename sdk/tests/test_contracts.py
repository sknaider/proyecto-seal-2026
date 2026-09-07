from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from seal_memory import (  # noqa: E402
    ApprovalRequest,
    AsyncSealMemory,
    MemoryRecord,
    MemoryStoreRequest,
    SealMemory,
    SealMemoryError,
    content_hash,
    hash_api_key,
    normalize_category,
    validate_importance,
)


def test_content_hash_matches_consolidation_gate() -> None:
    assert content_hash("same text") == hashlib.sha256(b"same text").hexdigest()
    assert content_hash("same text") != content_hash("same text ")


def test_hash_api_key_never_returns_raw_key() -> None:
    raw = "soul_test_123"
    hashed = hash_api_key(raw)
    assert hashed != raw
    assert hashed == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_approval_request_accepts_one_exact_phrase() -> None:
    parsed = ApprovalRequest.parse(
        "[Matrix] OK ADA aplica lote NEXUS invalidate_exact_duplicate_rows_keep_best_copy count=1"
    )
    assert parsed == ApprovalRequest(
        agent="NEXUS",
        action="invalidate_exact_duplicate_rows_keep_best_copy",
        count=1,
    )


def test_approval_request_rejects_two_phrases_in_one_message() -> None:
    text = (
        "OK ADA aplica lote NEXUS invalidate_exact_duplicate_rows_keep_best_copy count=1 "
        "OK ADA aplica lote DUM invalidate_exact_duplicate_rows_keep_best_copy count=1"
    )
    assert ApprovalRequest.parse(text) is None


def test_memory_store_request_validates_and_adds_content_hash() -> None:
    request = MemoryStoreRequest(
        agent_id="support_bot_v1",
        content="User prefers concise answers.",
        category="Preference",
        importance=8,
        metadata={"source": "unit_test"},
    )
    payload = request.to_payload()
    assert payload["category"] == "preference"
    assert payload["importance"] == 8
    assert payload["metadata"]["source"] == "unit_test"
    assert payload["metadata"]["content_hash"] == content_hash("User prefers concise answers.")


def test_memory_store_request_rejects_bad_category_and_importance() -> None:
    with pytest.raises(ValueError, match="invalid category"):
        normalize_category("secret")
    with pytest.raises(ValueError, match="between 1 and 10"):
        validate_importance(11)


def test_memory_record_from_api_accepts_content_or_memory_field() -> None:
    record = MemoryRecord.from_api(
        {"id": "m1", "memory": "stored text", "metadata": {"content_hash": "abc"}}
    )
    assert record.id == "m1"
    assert record.content == "stored text"
    assert record.content_hash == "abc"


def test_sync_client_store_uses_contract_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_request(self, method, path, params=None, body=None):
        captured.update({"method": method, "path": path, "body": body})
        return {"ok": True}

    monkeypatch.setattr(SealMemory, "_request", fake_request)
    client = SealMemory(api_key="soul_test")
    result = client.store(
        "support_bot_v1",
        "User wants examples first.",
        category="preference",
        importance=7,
    )
    assert result == {"ok": True}
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/memories"
    assert captured["body"]["metadata"]["content_hash"] == content_hash("User wants examples first.")


def test_sync_client_uses_only_canonical_live_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict | None, dict | None]] = []

    def fake_request(self, method, path, params=None, body=None):
        calls.append((method, path, params, body))
        if path.endswith("/42"):
            return {"ok": True, "memory": {"id": 42}}
        return {"ok": True, "memories": []}

    monkeypatch.setattr(SealMemory, "_request", fake_request)
    client = SealMemory(api_key="soul_test")

    client.recall("query", agent_id="agent", limit=3)
    client.search("agent", "query", limit=3)
    client.get_all("agent", limit=4)
    assert client.get(42) == {"id": 42}

    assert [(method, path) for method, path, _, _ in calls] == [
        ("POST", "/v1/recall"),
        ("POST", "/v1/recall"),
        ("GET", "/v1/memories"),
        ("GET", "/v1/memories/42"),
    ]
    assert all("/v1/agents/" not in path for _, path, _, _ in calls)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("add", ("agent", [])),
        ("update", ("agent", 1, "content")),
        ("delete", ("agent", 1)),
        ("register", ("org",)),
        ("boot", ("agent",)),
        ("snapshot", ("agent",)),
        ("reflect", ("agent", "thought")),
        ("thoughts", ("agent",)),
        ("entities", ("agent",)),
        ("chat", ("agent", "hello")),
        ("summary", ("agent",)),
    ],
)
def test_unsupported_sync_methods_fail_before_network(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    args: tuple,
) -> None:
    monkeypatch.setattr(SealMemory, "_request", lambda *a, **k: pytest.fail("network called"))
    client = SealMemory(api_key="soul_test")
    with pytest.raises(SealMemoryError, match=f"unsupported_operation:{method}") as exc_info:
        getattr(client, method)(*args)
    assert exc_info.value.status_code == 501


def test_import_is_clean_and_memory_client_is_lazy() -> None:
    package_root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as cwd:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": home,
            "PYTHONPATH": str(package_root),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys, seal_memory; "
                "assert seal_memory.__version__ == '0.2.0'; "
                "assert 'seal_memory.mem0_compat' not in sys.modules; "
                "from seal_memory import MemoryClient; "
                "assert 'REST' in repr(MemoryClient())",
            ],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
    assert result.returncode == 0, result.stderr


def test_memory_client_is_rest_only_and_does_not_resolve_db_credentials() -> None:
    from seal_memory import MemoryClient

    client = MemoryClient(api_key="soul_test", base_url="http://localhost:8767")
    assert "REST" in repr(client)
    with pytest.raises(NotImplementedError, match="direct_db_disabled"):
        MemoryClient(db_url="postgresql://unsafe")


@pytest.mark.asyncio
async def test_async_client_uses_only_canonical_live_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_request(self, method, path, params=None, json=None):
        calls.append((method, path))
        if path.endswith("/7"):
            return {"ok": True, "memory": {"id": 7}}
        return {"ok": True, "memories": []}

    monkeypatch.setattr(AsyncSealMemory, "_request", fake_request)
    client = AsyncSealMemory(api_key="soul_test")
    await client.store("agent", "content")
    await client.recall("query", agent_id="agent")
    await client.search("agent", "query")
    await client.get_all("agent")
    assert await client.get(7) == {"id": 7}
    await client.close()

    assert calls == [
        ("POST", "/v1/memories"),
        ("POST", "/v1/recall"),
        ("POST", "/v1/recall"),
        ("GET", "/v1/memories"),
        ("GET", "/v1/memories/7"),
    ]


@pytest.mark.asyncio
async def test_async_unsupported_method_fails_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_request(*args, **kwargs):
        pytest.fail("network called")

    monkeypatch.setattr(AsyncSealMemory, "_request", fail_request)
    client = AsyncSealMemory(api_key="soul_test")
    with pytest.raises(SealMemoryError, match="unsupported_operation:add") as exc_info:
        await client.add("agent", [])
    assert exc_info.value.status_code == 501
    await client.close()
