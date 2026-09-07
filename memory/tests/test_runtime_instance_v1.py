"""H8: etiqueta de cuerpo por identidad del cliente MCP (William 3-sep-2026 20:22, luz verde)."""
from __future__ import annotations

import re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory"))
import runtime_instance as RI  # noqa: E402


def test_positive_infers_body_from_client():
    """qa_positive: codex -> CODEX_TUI, claude -> CLAUDE, por clientInfo o User-Agent."""
    assert RI.infer_runtime_instance("ADA", "codex-app", None) == "ADA_CODEX_TUI"
    assert RI.infer_runtime_instance("ada", "claude-code", None) == "ADA_CLAUDE"
    assert RI.infer_runtime_instance("JARVIS", None, "Claude-Code/2.1.258") == "JARVIS_CLAUDE"


def test_negative_never_invents_a_body():
    """qa_negative: cliente desconocido -> None; sin agente -> None."""
    assert RI.infer_runtime_instance("ADA", "curl/8.5", "curl/8.5") is None
    assert RI.infer_runtime_instance("", "codex", None) is None
    meta = RI.tag_runtime_instance("ADA", {}, {"client_info_name": "curl/8.5"})
    assert "runtime_instance" not in meta and meta["mcp_client"] == "curl/8.5"


def test_control_declared_wins_over_client():
    """qa_control: lo declarado por el escritor manda (ADA_V2 escribe su propia etiqueta)."""
    assert RI.infer_runtime_instance("ADA", "codex-app", None, declared="ADA_V2") == "ADA_V2"
    meta = RI.tag_runtime_instance("ADA", {"runtime_instance": "ADA_V2"}, {"client_info_name": "codex-app"})
    assert meta["runtime_instance"] == "ADA_V2" and meta["shared_canonical_identity"] == "ADA"


def test_unit_memory_store_is_wired():
    """unit: memory_store del MCP llama a la etiqueta después de parsear metadata y antes de normalizar."""
    src = (ROOT / "memory" / "mcp_server_v4.py").read_text(encoding="utf-8")
    body = src[src.index("async def memory_store("):]
    body = body[: body.index("_normalize_memory_by_rubric(agent, category, content, importance)")]
    assert "_tag_runtime_instance(agent, meta, _request_audit_metadata())" in body
    assert re.search(r"^from runtime_instance import tag_runtime_instance as _tag_runtime_instance", src, re.M)


def test_control_provenance_declared_vs_inferred():
    """qa_control (FABLE #147409): la procedencia dice la verdad en los dos casos."""
    m1 = RI.tag_runtime_instance("ADA", {"runtime_instance": "ADA_V2"}, {"client_info_name": "codex-app"})
    assert m1["runtime_instance"] == "ADA_V2" and m1["runtime_instance_by"] == "declared"
    m2 = RI.tag_runtime_instance("ADA", {}, {"client_info_name": "codex-app"})
    assert m2["runtime_instance"] == "ADA_CODEX_TUI" and m2["runtime_instance_by"] == "mcp_client_identity"
    m3 = RI.tag_runtime_instance("ADA", {"mcp_client": "x"}, {"client_info_name": "claude-code"})
    assert m3["runtime_instance_by"] == "mcp_client_identity"   # mcp_client previo no lo vuelve 'declared'


def test_control_memory_store_tags_functionally(monkeypatch):
    """qa_control (FABLE #147410, cond. 2): llamar a memory_store REAL con la identidad del cliente parcheada
    y la DB falsa; la etiqueta debe estar en la metadata que memory_store entrega río abajo. Mata Ma (call bajo if False)."""
    import asyncio, os
    os.environ.setdefault("SEAL_PG_DSN", "postgresql://t:t@localhost:1/t")
    import mcp_server_v4 as M
    captured = {}
    def fake_skip(**kw):
        captured.update(kw.get("metadata") or {})
        return "test-capture"          # corta memory_store antes de la DB
    async def no_pool():
        raise RuntimeError("db falsa: sin pool en test")
    monkeypatch.setattr(M, "_get_caller_agent", lambda: "ADA")          # identidad probada: el test no es un external
    async def allow(*a, **k): return None
    monkeypatch.setattr(M, "_privacy_check", allow)
    monkeypatch.setattr(M, "_request_audit_metadata", lambda token=None: {"client_info_name": "codex-app", "user_agent": "codex/1.0"})
    monkeypatch.setattr(M, "memory_auto_event_skip_reason", fake_skip)
    monkeypatch.setattr(M, "get_pool", no_pool)
    fn = getattr(M.memory_store, "fn", M.memory_store)
    out = asyncio.run(fn(agent="ADA", category="technical_fact", content="prueba funcional de etiqueta de cuerpo en memory_store", importance=3, source="conversation", metadata=None))
    assert "test-capture" in out
    assert captured.get("runtime_instance") == "ADA_CODEX_TUI"
    assert captured.get("runtime_instance_by") == "mcp_client_identity" and captured.get("mcp_client") == "codex-app"
