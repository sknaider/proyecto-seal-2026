from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


os.environ.setdefault("SEAL_DB_URL", "postgresql://test:test@127.0.0.1:1/test")
sys.path.insert(0, str(Path(__file__).parent))

import agent_self_reflect


def test_fallback_relationship_note_is_evidence_bounded() -> None:
    window = {
        "agent": "ADA",
        "ev_count": 1,
        "events": [{"content": "JARVIS revisó el cambio; William fijó el criterio."}],
        "messages": [{"content": "Sin otras personas nombradas."}],
    }
    note = agent_self_reflect._deterministic_relationship_note(window)
    assert note == "Ventana con referencias explícitas a: William, JARVIS."
    assert agent_self_reflect._deterministic_relationship_note(
        {"agent": "ADA", "events": [], "messages": []}
    ) == ""
    assert agent_self_reflect._normalize_relationship_note("Vacio", window) == note
    assert agent_self_reflect._normalize_relationship_note("  Trabajo verificable.  ", window) == (
        "Trabajo verificable."
    )


def test_write_reflection_persists_relationship_note() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple]] = []

        async def execute(self, sql: str, *args):
            self.calls.append((sql, args))
            return "INSERT 0 1"

        async def fetchval(self, *_args):
            return 1  # evita insertar una opinión adicional

    conn = FakeConnection()
    reflection = {
        "valence": 0.2,
        "arousal": 0.4,
        "key_moment": "Cerré el frente.",
        "opinion": "",
        "opinion_topic": "operacional",
        "relationship_note": "Trabajé con ALICE sobre evidencia compartida.",
        "diary_entry": "Cerré el frente con evidencia.",
        "reasoning_trace": "",
    }
    wrote = asyncio.run(
        agent_self_reflect.write_reflection(
            conn, "ADA", reflection, {"ev_count": 1}
        )
    )
    emotional_sql, emotional_args = conn.calls[0]
    assert "relationship_note" in emotional_sql
    assert emotional_args[4] == reflection["relationship_note"]
    assert wrote["relationship_note"] is True


def test_gemma_empty_marker_uses_evidence_bounded_fallback(monkeypatch) -> None:
    response_payload = {
        "choices": [{
            "message": {"content": (
                '{"diary_entry":"ok","key_moment":"ok","valence":0.1,'
                '"arousal":0.2,"opinion":"ok","opinion_topic":"ops",'
                '"relationship_note":"Vacio","reasoning_trace":""}'
            )}
        }]
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return response_payload

    class FakeClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(agent_self_reflect.httpx, "AsyncClient", FakeClient)
    window = {
        "agent": "ADA",
        "hours": 6,
        "ev_count": 1,
        "mem_count": 0,
        "events": [{"type": "audit", "content": "ALICE verificó el resultado.", "ts": "now"}],
        "messages": [],
    }
    result = asyncio.run(agent_self_reflect.gemma_reflect(window))
    assert result["relationship_note"] == "Ventana con referencias explícitas a: ALICE."
