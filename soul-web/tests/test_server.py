from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from soul_web.server import Handler, SoulWebHTTPServer
from soul_web.service import ChatResult


class Conversations:
    def status_counts(self):
        return {"turn:completed": 3, "extraction:failure": 1}


class Core:
    database = "C:/canonical/alma.db"

    def list_memories(self, limit):
        return ["memoria"]

    def count(self):
        return 7


class Ollama:
    def list_chat_models(self):
        return ["gemma"]


class Service:
    conversations = Conversations()
    core = Core()
    ollama = Ollama()

    def chat(self, **kwargs):
        return ChatResult("respuesta", kwargs["model"], "turn", 1, 1, 1, "success")


@pytest.fixture
def live_server():
    server = SoulWebHTTPServer(("127.0.0.1", 0), Handler, service=Service())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_server_is_loopback_only():
    with pytest.raises(ValueError, match="loopback"):
        SoulWebHTTPServer(("0.0.0.0", 0), Handler, service=Service())


def test_health_status_and_chat_contract(live_server):
    with urllib.request.urlopen(live_server + "/health") as response:
        health = json.load(response)
        assert health["ok"] is True
        assert health["service"] == "soul-memory-web"
        assert health["version"] == "0.1.4"
        assert health["build_id"] == "soul-memory-web-0.1.4"
        assert health["pid"] > 0
        assert health["database"] == Core.database
    with urllib.request.urlopen(live_server + "/api/status") as response:
        status = json.load(response)
        assert status["counts"]["extraction:failure"] == 1
        assert status["core_memory_count"] == 7

    raw = json.dumps(
        {"model": "gemma", "message": "hola", "session_id": "session"}
    ).encode()
    request = urllib.request.Request(
        live_server + "/api/chat",
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        result = json.load(response)
    assert result["ok"] is True
    assert result["reply"] == "respuesta"
    assert result["extraction_status"] == "success"


def test_post_rejects_oversized_body_before_read(live_server):
    request = urllib.request.Request(
        live_server + "/api/chat",
        data=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": "999999"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request)
    assert caught.value.code == 400
