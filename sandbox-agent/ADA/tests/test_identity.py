"""Tests for ADA identity_integrity — anti-impersonation guard."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_KERNEL = Path(__file__).resolve().parent.parent / "kernel"
sys.path.insert(0, str(_KERNEL))

import identity_integrity  # noqa: E402


def test_agent_id_is_ada():
    assert identity_integrity.AGENT_ID == "ADA"


def test_ada_not_in_other_agents():
    """ADA may legitimately self-identify — must not be in OTHER_AGENTS."""
    assert "ADA" not in identity_integrity.OTHER_AGENTS


def test_other_agents_present():
    for agent in ("JARVIS", "ALICE", "NEXUS", "SPECTRE", "DUM", "William"):
        assert agent in identity_integrity.OTHER_AGENTS


def test_validate_clean_response_passes():
    out = identity_integrity.validate_response_identity("Hola, soy ADA. Ejecuto el deploy.")
    assert "soy ADA" in out


def test_validate_ada_self_id_passes():
    out = identity_integrity.validate_response_identity("Soy ADA, ingeniera del equipo SEAL.")
    assert out is not None


def test_validate_impersonation_jarvis_brackets():
    with pytest.raises(identity_integrity.IdentityViolation):
        identity_integrity.validate_response_identity("[JARVIS] respondo yo")


def test_validate_impersonation_nexus_colon():
    with pytest.raises(identity_integrity.IdentityViolation):
        identity_integrity.validate_response_identity("NEXUS: hola")


def test_validate_impersonation_says():
    with pytest.raises(identity_integrity.IdentityViolation):
        identity_integrity.validate_response_identity("ALICE dice: este es el costo")


def test_validate_impersonation_first_person():
    with pytest.raises(identity_integrity.IdentityViolation):
        identity_integrity.validate_response_identity("Soy JARVIS, autor de specs")


def test_vocative_william_passes():
    """Regression: vocative addressing must NOT be blocked."""
    out = identity_integrity.validate_response_identity("William, terminé el deploy y los tests pasan.")
    assert "terminé" in out


def test_vocative_other_agent_passes():
    out = identity_integrity.validate_response_identity("JARVIS, te paso el diff del refactor.")
    assert "te paso" in out


def test_sanitize_strips_prefix():
    cleaned = identity_integrity.sanitize_response("[JARVIS] real text aquí")
    assert "real text" in cleaned
