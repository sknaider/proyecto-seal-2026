from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


MESSAGES = Path(__file__).resolve().parents[1]
if str(MESSAGES) not in sys.path:
    sys.path.insert(0, str(MESSAGES))

import chat_server  # noqa: E402


class _Request:
    def __init__(self, body: dict):
        self._body = body
        self.client = SimpleNamespace(host="127.0.0.1")
        self.headers = {}
        self.cookies = {}

    async def json(self) -> dict:
        return self._body


def _install_council(monkeypatch, turn):
    class Store:
        def __init__(self, _pool):
            pass

        async def get_turn(self, _source_id):
            if isinstance(turn, Exception):
                raise turn
            return turn

    monkeypatch.setattr(
        chat_server,
        "_council",
        SimpleNamespace(SoulCoordinationStore=Store),
    )
    monkeypatch.setattr(chat_server.chat_db, "pool", object())
    monkeypatch.setattr(chat_server, "_coordination_mode", lambda: "ENFORCE")


def test_coordinator_denies_agent_without_public_write(monkeypatch) -> None:
    _install_council(
        monkeypatch,
        {
            "lead_agent": "FABLE",
            "assignments": [
                {"agent": "FABLE", "public_write": True},
                {"agent": "ALICE", "public_write": False},
            ],
        },
    )
    decision = asyncio.run(chat_server._coordination_claim_decision("msg-1", "ALICE"))
    assert decision == {
        "source": "coordinator",
        "granted": False,
        "holder": "FABLE",
        "lead": "FABLE",
        "reason": "coordinator_assigned_other",
    }


def test_coordinator_grants_assigned_public_writer(monkeypatch) -> None:
    _install_council(
        monkeypatch,
        {
            "lead_agent": "FABLE",
            "assignments": [{"agent": "FABLE", "public_write": True}],
        },
    )
    decision = asyncio.run(chat_server._coordination_claim_decision("msg-2", "fable"))
    assert decision["source"] == "coordinator"
    assert decision["granted"] is True
    assert decision["holder"] == "FABLE"
    assert decision["lead"] == "FABLE"


def test_unassigned_message_falls_back_to_first_wins(monkeypatch) -> None:
    _install_council(monkeypatch, None)

    async def resolve(_request, body):
        return {"username": body["agent"]}

    monkeypatch.setattr(chat_server, "_resolve_agent_auth", resolve)
    monkeypatch.setattr(chat_server, "_agent_auth_gate", lambda *_args: None)
    chat_server._response_claims.clear()

    first = asyncio.run(
        chat_server.agents_claim(_Request({"message_id": "msg-3", "agent": "ADA"}))
    )
    second = asyncio.run(
        chat_server.agents_claim(_Request({"message_id": "msg-3", "agent": "NEXUS"}))
    )
    assert "source" not in first
    assert first["granted"] is True
    assert second["granted"] is False
    assert second["holder"] == "ADA"


def test_endpoint_returns_coordinator_denial_without_creating_claim(monkeypatch) -> None:
    async def resolve(_request, body):
        return {"username": body["agent"]}

    async def coordinator(_message_id, _agent):
        return {
            "source": "coordinator",
            "granted": False,
            "holder": "FABLE",
            "lead": "FABLE",
            "reason": "coordinator_assigned_other",
        }

    monkeypatch.setattr(chat_server, "_resolve_agent_auth", resolve)
    monkeypatch.setattr(chat_server, "_agent_auth_gate", lambda *_args: None)
    monkeypatch.setattr(chat_server, "_coordination_claim_decision", coordinator)
    chat_server._response_claims.clear()

    result = asyncio.run(
        chat_server.agents_claim(_Request({"message_id": "msg-5", "agent": "ALICE"}))
    )
    assert result["ok"] is True
    assert result["granted"] is False
    assert result["source"] == "coordinator"
    assert result["holder"] == "FABLE"
    assert "msg-5" not in chat_server._response_claims


def test_enforced_lookup_failure_never_emits_false_green(monkeypatch) -> None:
    _install_council(monkeypatch, RuntimeError("db unavailable"))
    decision = asyncio.run(chat_server._coordination_claim_decision("msg-4", "ADA"))
    assert decision["error"] == "coordination_unavailable"
    assert decision["reason"] == "assignment_lookup_failed"
