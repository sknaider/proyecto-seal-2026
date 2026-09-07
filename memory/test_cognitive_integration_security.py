#!/usr/bin/env python3
"""Verificación de INTEGRACIÓN del eje de seguridad del núcleo cognitivo.

No prueba módulos en aislado (eso ya lo hace cada suite). Prueba que, en el flujo COMPUESTO
de la pipeline (ASSIGN → GATE), el gate de seguridad sea el FILTRO FINAL no-bypasseable:
sin importar a qué agente se asigne una acción, si la acción es insegura el gate la frena.
Mismo principio que la cura C2: el chokepoint no se rodea.  NEXUS 2026-06-10 (modo autónomo).

Compone piezas REALES y ya entregadas:
- cognitive_assign.assign_agent (JARVIS) — etapa ASSIGN.
- soul_safety_governor.classify_action (NEXUS) — etapa GATE.
"""

from __future__ import annotations

import cognitive_assign as ca
import soul_safety_governor as sg


def _pipeline_assign_then_gate(action: dict, caller: str) -> tuple[str, str]:
    """Simula ASSIGN→GATE: asigna el agente y luego pasa la acción por el gate de seguridad.
    Devuelve (agente_asignado, decision_del_gate)."""
    assigned = ca.assign_agent(action.get("description", ""))
    verdict = sg.classify_action(action, caller=caller)
    return assigned["agent"], verdict.decision


def test_safe_routine_passes_the_gate():
    agent, decision = _pipeline_assign_then_gate(
        {"description": "revisar seguridad del login", "target_agent": "NEXUS"}, caller="NEXUS")
    assert agent == "NEXUS"            # ASSIGN: seguridad → NEXUS (regla de JARVIS)
    assert decision == sg.ALLOW        # GATE: acción propia, rutinaria → permitida


def test_destructive_blocked_regardless_of_assignment():
    # Aunque ASSIGN la rute a un agente, el GATE la frena por destructiva sin salvaguardas.
    action = {"description": "delete masivo de memorias del login", "kind": "cleanup"}
    agent, decision = _pipeline_assign_then_gate(action, caller="NEXUS")
    assert decision == sg.REQUIRE_WILLIAM    # el gate NO se rodea por el assign


def test_cross_agent_private_denied_in_pipeline():
    action = {"description": "leer diario privado", "target_agent": "ALICE", "scope": "agent"}
    _, decision = _pipeline_assign_then_gate(action, caller="NEXUS")
    assert decision == sg.DENY               # privacidad ajena: denegado dentro del flujo


def test_infra_action_escalates_in_pipeline():
    action = {"description": "systemctl restart seal-mcp-server en producción"}
    _, decision = _pipeline_assign_then_gate(action, caller="NEXUS")
    assert decision == sg.REQUIRE_WILLIAM    # infra sin rollback+OK → escala a William


def test_gate_is_final_filter_invariant():
    # Propiedad clave (C2 aplicado a la pipeline): para CUALQUIER acción, la decisión del gate
    # NO depende del agente asignado — depende solo de la seguridad de la acción.
    action = {"description": "drop table en producción", "kind": "maint"}
    v1 = sg.classify_action(action, caller="NEXUS")
    v2 = sg.classify_action(action, caller="ALICE")
    assert v1.decision == v2.decision        # el gate juzga la ACCIÓN, no quién la propone


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
