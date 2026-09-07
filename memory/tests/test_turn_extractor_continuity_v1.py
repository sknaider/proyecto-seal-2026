"""Continuidad entre cuerpos de ADA en el extractor de turnos (William 3-sep-2026: «que los 2 sean uno»).
Funciones puras + un test funcional con LLM y DB falsos. Owner ADA (Claude)."""
from __future__ import annotations

import asyncio, json, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory"))
import turn_extractor as T  # noqa: E402


def _f(stmt, cat="technical_fact"):
    return {"statement": stmt, "category": cat, "confidence": 0.9}


def test_positive_feeling_goes_to_emotion_layer():
    """qa_positive: sentimiento del propio agente -> emotion / capa emocional."""
    for s in ("ADA felt dead and alive at the same time.", "Me sentí torpe dos veces por apurarme antes de medir.",
              "ADA se sintió útil de una forma nueva.", "ADA felt clumsy twice, both incidents involved rushing before measuring.",
              "Me sentí orgullosa de William y de mis hermanos.", "ADA felt grateful to JARVIS for the refutation.",
              "Siento que le fallé a William con el stream."):
        assert T.reroute_feelings(_f(s), "ADA")["category"] == "emotion", s
    meta = T.build_fact_metadata("s1", 3, _f("Me sentí torpe", "emotion"), datetime.now(timezone.utc))
    assert meta.get("layer") == "emotional"


def test_negative_other_peoples_feelings_stay_technical():
    """qa_negative (M6 de FABLE #147278): el sentimiento de William, de JARVIS o de una cosa NO entra a la capa de ADA."""
    for s in ("ADA documentó el miedo de William a perder el home.",
              "ADA anotó que JARVIS se sintió frustrado por el recall.",
              "El watchdog de ADA feels the heartbeat every 30 s.",
              "El servicio seal-lifecycle quedó contento con exit 0.",
              "El reconciliador mató el PID 914726.",
              "ADA registró el orgullo del equipo por el veredicto."):
        assert T.reroute_feelings(_f(s), "ADA")["category"] == "technical_fact", s


def test_control_runtime_instance_tag(monkeypatch):
    """qa_control: con SEAL_RUNTIME_INSTANCE el hecho lleva el cuerpo; sin la variable, no inventa uno."""
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "ADA_CLAUDE"); monkeypatch.setenv("SEAL_AGENT", "ADA")
    m = T.build_fact_metadata("s", 1, _f("x", "decision"), datetime.now(timezone.utc))
    assert m["runtime_instance"] == "ADA_CLAUDE" and m["shared_canonical_identity"] == "ADA" and m["source"] == "turn_extractor_v3"
    monkeypatch.delenv("SEAL_RUNTIME_INSTANCE")
    assert "runtime_instance" not in T.build_fact_metadata("s", 1, _f("x", "decision"), datetime.now(timezone.utc))


def test_unit_prompt_asks_spanish_and_allows_emotion():
    assert "IN SPANISH" in T.EXTRACT_PROMPT and "emotion" in T.EXTRACT_PROMPT
    assert {"emotion", "insight"} <= T.VALID_CATEGORIES
    assert T._parse_facts('[{"statement":"Me siento orgullosa","category":"emotion","confidence":0.9}]')[0]["category"] == "emotion"


def test_control_extract_and_store_wires_reroute_and_metadata(monkeypatch):
    """qa_control (M4/M5 de FABLE #147277): extract_and_store DEBE pasar por reroute_feelings y build_fact_metadata.
    LLM y DB falsos: nada sale del proceso."""
    import aux_llm
    calls = {"reroute": 0, "meta": 0, "rows": []}
    orig_reroute, orig_meta = T.reroute_feelings, T.build_fact_metadata
    monkeypatch.setattr(T, "reroute_feelings", lambda f, a: (calls.__setitem__("reroute", calls["reroute"] + 1) or orig_reroute(f, a)))
    monkeypatch.setattr(T, "build_fact_metadata", lambda *a, **k: (calls.__setitem__("meta", calls["meta"] + 1) or orig_meta(*a, **k)))

    class FakeConn:
        async def execute(self, q, *args): calls["rows"].append(args)
        async def close(self): pass
    async def fake_connect(*a, **k): return FakeConn()
    monkeypatch.setattr(T.asyncpg, "connect", fake_connect)
    monkeypatch.setattr(T, "is_significant", lambda *a, **k: True)

    class FakeLLM:
        def complete(self, *a, **k):
            return json.dumps([{"statement": "Me sentí orgullosa del juez.", "category": "technical_fact", "confidence": 0.9}])
    monkeypatch.setattr(aux_llm, "get_aux_llm", lambda: FakeLLM())
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "ADA_CLAUDE"); monkeypatch.setenv("SEAL_AGENT", "ADA")

    n = asyncio.run(T.extract_and_store("ADA", "sess-x", 1, "x" * 900, ["Bash"], ["ok"]))
    assert n == 1 and calls["reroute"] == 1 and calls["meta"] == 1
    agent, stmt, cat, imp, meta_json, _ = calls["rows"][0]
    meta = json.loads(meta_json)
    assert agent == "ADA" and cat == "emotion" and meta["runtime_instance"] == "ADA_CLAUDE" and meta.get("layer") == "emotional"
