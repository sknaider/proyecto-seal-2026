"""Tests for NEXUS identity_integrity (anti-impersonation guard)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_KERNEL = Path(__file__).resolve().parent.parent / "kernel"
sys.path.insert(0, str(_KERNEL))

from identity_integrity import (  # noqa: E402
    IdentityViolation,
    sanitize_response,
    validate_response_identity,
)


# ── Valid responses (must NOT raise) ──────────────────────────────────────────

def test_clean_response_passes():
    resp = "Hola William, aquí está mi diagnóstico del cluster..."
    assert validate_response_identity(resp) == resp


def test_response_mentioning_agents_in_body_passes():
    """Mentioning other agents inside the response is fine; only impersonation is blocked."""
    resp = "Le voy a pasar este análisis a JARVIS para que valide. ADA puede ejecutar después."
    assert validate_response_identity(resp) == resp


def test_empty_response_passes():
    assert validate_response_identity("") == ""
    assert validate_response_identity("   ") == "   "


def test_response_starting_with_nexus_brand_passes():
    """[NEXUS] prefix is allowed (own brand)."""
    resp = "[NEXUS] Diagnóstico completo del kernel soul..."
    assert validate_response_identity(resp) == resp


# ── Impersonation cases (must raise IdentityViolation) ────────────────────────

def test_blocks_bracket_prefix_ada():
    with pytest.raises(IdentityViolation):
        validate_response_identity("[ADA] hola William, ya ejecuté el comando")


def test_blocks_bracket_prefix_jarvis():
    with pytest.raises(IdentityViolation):
        validate_response_identity("[JARVIS] el cluster está listo")


def test_blocks_bracket_prefix_alice():
    with pytest.raises(IdentityViolation):
        validate_response_identity("[ALICE] documento listo")


def test_blocks_colon_prefix():
    with pytest.raises(IdentityViolation):
        validate_response_identity("ADA: ya terminé el bench")


def test_blocks_responde_pattern():
    with pytest.raises(IdentityViolation):
        validate_response_identity("JARVIS responde: el cluster está OK")


def test_blocks_dice_pattern():
    with pytest.raises(IdentityViolation):
        validate_response_identity("ALICE dice: documentación lista")


def test_vocative_with_comma_passes():
    """Regression: vocative addressing ('NAME, ...') is 2nd-person, NOT impersonation.
    Bug fixed 04-may-2026: pattern that blocked 'William, ...' has been removed."""
    out = validate_response_identity("ADA, ya terminé la tarea")
    assert "terminé" in out


def test_vocative_william_passes():
    out = validate_response_identity("William, te paso el diagnóstico del kernel.")
    assert "diagnóstico" in out


def test_blocks_como_soy_other():
    with pytest.raises(IdentityViolation):
        validate_response_identity("Como JARVIS, voy a coordinar el cluster")


def test_blocks_william_impersonation_first_person():
    """1st-person impersonation as William ('Soy William') is blocked.
    Note: 'William, decido...' was previously blocked but is actually vocative
    addressing — fixed 04-may-2026, see test_vocative_with_comma_passes."""
    with pytest.raises(IdentityViolation):
        validate_response_identity("Soy William, decido autorizar el cambio")


def test_violation_carries_pattern_metadata():
    try:
        validate_response_identity("[ADA] something")
    except IdentityViolation as ex:
        assert ex.matched_pattern  # not empty
        assert "ADA" in ex.response_excerpt or "[ADA]" in ex.response_excerpt
        return
    pytest.fail("expected IdentityViolation")


# ── Sanitization ──────────────────────────────────────────────────────────────

def test_sanitize_removes_bracket_prefix():
    cleaned = sanitize_response("[ADA] el comando ejecutó OK")
    assert not cleaned.lower().startswith("[ada]")
    assert "comando" in cleaned


def test_sanitize_idempotent():
    clean = "Diagnóstico normal"
    assert sanitize_response(clean) == clean


def test_sanitize_empty():
    assert sanitize_response("") == ""
