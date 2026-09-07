"""Hook Stop del extractor (cura H7, veredicto FABLE #147281): agente solo del roster por SEAL_AGENT,
transcript solo el de la sesión que disparó el hook, cuerpo etiquetado por defecto."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory"))
import turn_extract_stop_hook as H  # noqa: E402


def test_positive_roster_agent_from_env():
    assert H.resolve_agent({"SEAL_AGENT": "ADA"}) == "ADA"
    assert H.resolve_agent({"SEAL_AGENT": "nexus"}) == "NEXUS"


def test_negative_unknown_or_empty_agent_never_falls_back_to_ada():
    """qa_negative (H7): FABLE, ALICE-V2 o vacío -> '' (no se extrae). Nunca 'ADA' por cwd."""
    for v in ("FABLE", "ALICE-V2", "", "  ", "SPECTRE"):
        assert H.resolve_agent({"SEAL_AGENT": v, "PWD": "/home/dadito/IA/proyecto-seal"}) == ""
    assert not hasattr(H, "AGENT_CWD_MAP") and not hasattr(H, "detect_agent_from_cwd")


def test_control_transcript_only_from_stdin(tmp_path):
    """qa_control (H7): sin transcript_path no hay extracción; con uno válido, ese y no el más reciente."""
    assert H.resolve_transcript({}) == ""
    assert H.resolve_transcript({"transcript_path": str(tmp_path / "no.jsonl")}) == ""
    t = tmp_path / "s.jsonl"; t.write_text("{}\n")
    assert H.resolve_transcript({"transcript_path": str(t)}) == str(t)
    src = (ROOT / "memory" / "turn_extract_stop_hook.py").read_text(encoding="utf-8")
    assert "glob" not in src and "getmtime" not in src


def test_unit_runtime_instance_default_is_claude_body():
    assert H.runtime_instance_for("ADA", {}) == "ADA_CLAUDE"
    assert H.runtime_instance_for("ADA", {"SEAL_RUNTIME_INSTANCE": "ADA_CLAUDE_X"}) == "ADA_CLAUDE_X"
