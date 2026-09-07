"""Tests del harness adversarial E3 (SSAI SHADOW) — lo vuelve CI-cubierto y durable.

No re-implementan la detección (los unit tests de `tests/test_ssai_shadow_*` la cubren);
verifican que el HARNESS de evidencia se comporte fail-closed y produzca el artefacto
JSON reproducible que el gate T6 exige (no evidencia efímera).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docs.thesis.ssai.experiments import e3_crypto_manipulation as e3


# ── unit: cada ataque es detectado por su mecanismo esperado ──────────────────
def _run_all(tmp_path: Path) -> list[e3.AttackOutcome]:
    return [fn(tmp_path) for fn in e3.ATTACKS]


def test_every_attack_is_detected_and_control_is_clean(tmp_path: Path) -> None:
    outcomes = _run_all(tmp_path)
    attacks = [o for o in outcomes if o.surface != "none"]
    controls = [o for o in outcomes if o.surface == "none"]
    assert len(attacks) == 7 and len(controls) == 1
    # los 7 ataques detectados
    assert all(o.detected for o in attacks), [o.attack for o in attacks if not o.detected]
    # el control honesto NO se marca (detected==True aquí = limpio aceptado, sin falso positivo)
    assert controls[0].detected, "control honesto marcado como ataque = FALSO POSITIVO"


def test_each_attack_records_evaluated_bytes_digest(tmp_path: Path) -> None:
    for o in _run_all(tmp_path):
        assert o.evaluated_sha256, f"{o.attack} sin sha256 de bytes evaluados"
        # sha256 hex o el 'sha256:'-ref del manifest digest
        d = o.evaluated_sha256.split(":", 1)[-1]
        assert len(d) == 64 and all(c in "0123456789abcdef" for c in d)


# ── qa_positive: el caso estrella deja verify() ciego y solo el witness caza ───
def test_insider_recompute_is_blind_to_verify_and_caught_by_witness(tmp_path: Path) -> None:
    o = e3.attack_insider_recompute_fork(tmp_path)
    assert o.detected and o.detector == "witness"
    assert "ACCEPT(blind)" in o.verify_ok_after, "verify() debería aprobar la cadena recomputada"
    assert "fork" in o.detail.lower()


def test_rollback_is_caught_by_witness_not_verify(tmp_path: Path) -> None:
    o = e3.attack_rollback_truncate(tmp_path)
    assert o.detected and o.detector == "witness"


# ── qa_negative: si un ataque NO se detecta, el harness debe FALLAR (fail-closed) ──
def test_a_missed_attack_makes_the_run_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                            capsys: pytest.CaptureFixture) -> None:
    """Inyecta un 'ataque' que NO detecta nada; main() debe devolver exit≠0."""
    def undetected(_tmp: Path) -> e3.AttackOutcome:
        return e3.AttackOutcome("injected_miss", "event", False, "verify",
                                "ACCEPT(!!)", 1.0, "0" * 64, "no detectado")
    monkeypatch.setattr(e3, "ATTACKS", e3.ATTACKS + [undetected])
    out = tmp_path / "ev.json"
    monkeypatch.setattr("sys.argv", ["e3", "--json", str(out)])
    rc = e3.main()
    assert rc == 1, "un ataque no detectado DEBE fallar cerrado"


def test_a_false_positive_control_makes_the_run_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Un control honesto marcado como ataque (falso positivo) debe fallar cerrado."""
    def false_positive(_tmp: Path) -> e3.AttackOutcome:
        return e3.AttackOutcome("FP_CONTROL", "none", False, "verify+witness",
                                "REJECT(!!)", 1.0, "0" * 64, "falso positivo")
    monkeypatch.setattr(e3, "ATTACKS", [false_positive] + e3.ATTACKS)
    out = tmp_path / "ev.json"
    monkeypatch.setattr("sys.argv", ["e3", "--json", str(out)])
    assert e3.main() == 1


# ── qa_control: la corrida real produce el artefacto JSON durable y pasa ───────
def test_full_run_emits_reproducible_json_and_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = tmp_path / "E3_EVIDENCE.json"
    monkeypatch.setattr("sys.argv", ["e3", "--json", str(out)])
    rc = e3.main()
    assert rc == 0, "la corrida real debe pasar (7/7 + control)"
    assert out.exists(), "no se produjo el artefacto JSON durable"
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["schema"] == e3.EVIDENCE_SCHEMA
    assert doc["framework_state"] == "SHADOW/TOFU_UNANCHORED"
    assert doc["summary"] == {
        "attacks_total": 7,
        "attacks_detected": 7,
        "control_ok_no_false_positive": True,
        "fail_closed_pass": True,
    }
    assert len(doc["outcomes"]) == 8
    # cada outcome lleva su sha256 de bytes evaluados y latencia
    for o in doc["outcomes"]:
        assert o["evaluated_sha256"]
        assert isinstance(o["latency_us"], (int, float))
