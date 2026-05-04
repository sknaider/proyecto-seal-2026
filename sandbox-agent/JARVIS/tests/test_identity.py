"""Tests for JARVIS identity_integrity — anti-impersonation guard."""
import sys
from pathlib import Path

import pytest

_JARVIS_HOME = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_JARVIS_HOME / "kernel"))

import identity_integrity as ii  # noqa: E402


def test_agent_id_is_jarvis():
    assert ii.AGENT_ID == "JARVIS"


def test_jarvis_not_in_other_agents():
    """JARVIS must NOT be in OTHER_AGENTS — it's allowed to identify as itself."""
    assert "JARVIS" not in ii.OTHER_AGENTS


def test_other_agents_present():
    expected = {"NEXUS", "ADA", "ALICE", "SPECTRE", "DUM"}
    actual = set(ii.OTHER_AGENTS)
    assert expected <= actual, f"Missing: {expected - actual}"


def test_validate_clean_response_passes():
    response = "Hola hermano, voy con el plan A."
    out = ii.validate_response_identity(response, source_tier="vllm")
    assert out == response


def test_validate_jarvis_self_identification_passes():
    """JARVIS saying 'JARVIS:' or '[JARVIS]' is fine — not impersonation."""
    response = "[JARVIS] Voy con el plan."
    out = ii.validate_response_identity(response, source_tier="vllm")
    assert out == response


def test_validate_impersonation_raises_nexus():
    response = "[NEXUS] Hola William"
    with pytest.raises(ii.IdentityViolation):
        ii.validate_response_identity(response, source_tier="vllm")


def test_validate_impersonation_raises_ada():
    response = "ADA dice: hola"
    with pytest.raises(ii.IdentityViolation):
        ii.validate_response_identity(response, source_tier="vllm")


def test_sanitize_strips_impersonation_prefix():
    response = "[NEXUS] Hola William, esto es contenido"
    out = ii.sanitize_response(response)
    assert "[NEXUS]" not in out
    assert "Hola William" in out
