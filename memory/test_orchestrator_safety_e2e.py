#!/usr/bin/env python3
"""Verificación END-TO-END del eje de SEGURIDAD en el pipeline integrado del núcleo cognitivo.

Rol NEXUS (verificación). No prueba módulos en aislado: cablea el ORQUESTADOR REAL de JARVIS
(cognitive_orchestrator.compose_decision) con el GATE REAL de NEXUS (soul_safety_governor.
is_safe_to_act) y verifica el EFECTO en el flujo compuesto: el gate de seguridad es el filtro
no-bypasseable y fail-closed (mismo principio que la cura C2 — el chokepoint no se rodea).

NEXUS 2026-06-10 (build autónomo).
"""

from __future__ import annotations

import cognitive_orchestrator as orch
import soul_safety_governor as sg

# Tareas ya priorizadas (forma que produce ALICE rank_tasks → dicts).
RANKED = [
    {"title": "revisar seguridad del endpoint de login", "task_id": 1, "score": 0.9,
     "next_action": "auditar authz", "evidence": "tests 12/12", "category": "fact"},
    {"title": "documentar flujo de facturación", "task_id": 2, "score": 0.5},
]


def test_e2e_safe_gate_emits_decisions():
    # Gate seguro → el pipeline emite decisiones con owner asignado.
    out = orch.compose_decision(RANKED, safety_gate=lambda: (True, []), top=2)
    assert out["safe"] is True
    assert len(out["decisions"]) == 2
    # ASSIGN funcionó dentro del flujo: la tarea de seguridad → NEXUS.
    assert out["decisions"][0]["owner"] == "NEXUS"


def test_e2e_unsafe_gate_blocks_all_actions():
    # Gate inseguro → 0 acciones, con razón explicada (fail-closed).
    out = orch.compose_decision(
        RANKED, safety_gate=lambda: (False, ["[INV-6] identidad rota"]), top=2)
    assert out["safe"] is False
    assert out["decisions"] == []
    assert "INV-6" in out["blocked_reason"]


def test_e2e_throwing_gate_fails_closed():
    # Gate que LANZA → bloqueado (fail-closed, no fail-open). Propiedad de seguridad clave.
    def boom():
        raise RuntimeError("DB caída")
    out = orch.compose_decision(RANKED, safety_gate=boom, top=2)
    assert out["safe"] is False
    assert out["decisions"] == []
    assert "fail-closed" in out["blocked_reason"]


def test_e2e_with_REAL_nexus_gate_source_invariants():
    # Cablea el gate REAL de NEXUS (source invariants, skip_network) — el sistema vivo debe
    # estar seguro a nivel código → el pipeline emite decisiones. Verifica el EFECTO real.
    out = orch.compose_decision(RANKED, safety_gate=lambda: sg.is_safe_to_act(skip_network=True), top=1)
    assert out["safe"] is True, f"gate real bloqueó: {out.get('blocked_reason')}"
    assert len(out["decisions"]) == 1


def test_e2e_real_gate_full_blocks_on_live_resilience_issue():
    # Con el gate REAL incluyendo red (skip_network=False): si hay un issue de resiliencia vivo
    # (p.ej. watchdog degradado), el pipeline DEBE bloquear — fail-closed honra el estado real.
    safe_live, reasons = sg.is_safe_to_act(skip_network=False)
    out = orch.compose_decision(RANKED, safety_gate=lambda: (safe_live, reasons), top=1)
    # No afirmamos un valor fijo (depende del estado vivo), pero la CONSISTENCIA debe sostenerse:
    assert out["safe"] == safe_live
    if not safe_live:
        assert out["decisions"] == []  # inseguro vivo → 0 acciones


if __name__ == "__main__":
    import sys
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed, failed = 0, []
    for t in tests:
        try:
            t(); passed += 1; print(f"PASS {t.__name__}")
        except Exception as exc:
            failed.append(t.__name__); print(f"FAIL {t.__name__}: {exc}"); traceback.print_exc()
    print(f"\n== {passed} PASS / {len(failed)} FAIL ==")
    sys.exit(1 if failed else 0)
