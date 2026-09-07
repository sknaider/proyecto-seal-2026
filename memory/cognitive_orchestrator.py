"""cognitive_orchestrator — composición pura del pipeline de decisión del núcleo.

Autor: JARVIS (arquitectura) · 2026-06-10 (build autónomo).
Implementa design_cognitive_orchestration_v1.md como CÓDIGO. Archivo NUEVO (no edita el núcleo
de ADA — coordinación sin pisarse). El núcleo (ADA) junta los inputs (state/priority/safety) desde
la DB y llama a `compose_decision()` con ellos; la composición acá es PURA y testeable sin DB.

Pipeline: STATE → EPISTEMIC → PRIORITIZE → ASSIGN → GATE → OUTPUT.
Los stages con DB los provee ADA; este módulo hace ASSIGN + GATE + OUTPUT (la lógica de decisión).
"""
from __future__ import annotations
from typing import Any, Callable
import dataclasses
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from cognitive_assign import assign_agent
from cognitive_invariants import closure_gate, evidence_gate


def _as_dict(task: Any) -> dict:
    """Normaliza la entrada a dict. ROBUSTO al gap que cazó ALICE: rank_tasks devuelve
    dataclasses TaskPriority, no dicts. Acepta dict, dataclass, o objeto con __dict__.
    """
    if isinstance(task, dict):
        return task
    if dataclasses.is_dataclass(task):
        return dataclasses.asdict(task)
    if hasattr(task, "__dict__"):
        return dict(vars(task))
    return {"title": str(task)}


def compose_decision(
    ranked_tasks: list[dict],
    *,
    safety_gate: Callable[[], tuple[bool, list[str]]] | None = None,
    top: int = 1,
) -> dict:
    """Compone la decisión cognitiva a partir de tareas ya priorizadas (ALICE rank_tasks → dicts).

    ranked_tasks: lista de dicts con al menos {title, score?, task_id?, agent?}.
    safety_gate: callable opcional = NEXUS is_safe_to_act → (safe, reasons). Si safe=False,
                 la decisión se marca BLOQUEADA (fail-closed): no se recomienda acción autónoma.
    top: cuántas decisiones devolver.

    Devuelve {safe, blocked_reason?, decisions:[{task, owner, owner_confidence, owner_reason,
              closure_ok, closure_reason, next_action}]}.
    """
    # GATE de seguridad global (paso 5, mitad NEXUS): si el sistema no es seguro para actuar
    # autónomamente, NO emitimos acciones — fail-closed, explicamos por qué.
    if safety_gate is not None:
        try:
            safe, reasons = safety_gate()
        except Exception as e:  # fail-closed
            return {"safe": False, "blocked_reason": f"safety_gate lanzó ({e}) → fail-closed",
                    "decisions": []}
        if not safe:
            return {"safe": False, "blocked_reason": "; ".join(reasons) or "sistema no seguro para actuar",
                    "decisions": []}

    decisions: list[dict] = []
    for raw in ranked_tasks[:top]:
        task = _as_dict(raw)  # robusto a dataclass (TaskPriority) o dict (gap cazado por ALICE)
        # ASSIGN (paso 4): quién debe actuar.
        owner = assign_agent(task)
        # GATE de método (paso 5, mitad JARVIS): ¿se puede CERRAR? (evidencia + benchmark)
        ok_close, reason_close = closure_gate(task)
        next_action = task.get("next_action") or task.get("rationale") or "definir próxima acción"
        decisions.append({
            "task": task.get("title", ""),
            "task_id": task.get("task_id"),
            "owner": owner["agent"],
            "owner_confidence": owner["confidence"],
            "owner_reason": owner["reason"],
            "closure_ok": ok_close,                 # ¿está realmente listo para cerrar?
            "closure_reason": reason_close,
            "next_action": next_action,
            "score": task.get("score"),
        })
    return {"safe": True, "decisions": decisions}


__all__ = ["compose_decision"]
