#!/usr/bin/env python3
"""
test_soul_event_interface.py — F1 núcleo portable. Oráculos ruidosos, exit!=0 si algo falla.
No toca DB ni subprocess: el runner y el registry se inyectan.
"""
import sys
from soul_event_interface import (
    SOUL_EVENTS, LEARNING_EVENTS, CLAUDE_CODE_EVENT_MAP,
    to_soul_event, SoulEventEnvelope, HookRegistration,
    hooks_for, dispatch, parity_report, ground_truth_seed,
)


def _env(soul_event="on_turn_end", agent="JARVIS"):
    return SoulEventEnvelope(soul_event=soul_event, agent=agent,
                             session_id="s1", ts="2026-08-05T00:00:00", runtime="claude_code")


def main() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        fails += 0 if cond else 1

    # 1. Los 4 críticos de aprendizaje están dentro de los 9 soul_events.
    check("9 soul_events, 4 críticos ⊆ soul_events",
          len(SOUL_EVENTS) == 9 and all(e in SOUL_EVENTS for e in LEARNING_EVENTS))

    # 2. Mapeo runtime→soul: conocido traduce, desconocido FALLA RUIDOSO.
    check("Stop→on_turn_end", to_soul_event("Stop") == "on_turn_end")
    try:
        to_soul_event("EventoInventado")
        check("evento no mapeado lanza ValueError", False)
    except ValueError:
        check("evento no mapeado lanza ValueError", True)

    # 3. Envelope rechaza un soul_event inválido (fail-closed).
    try:
        SoulEventEnvelope("on_inexistente", "JARVIS", "s", "t", "r")
        check("envelope inválido lanza", False)
    except ValueError:
        check("envelope inválido lanza", True)

    # 4. hooks_for respeta agent=None (todos) vs agente específico y el enabled.
    reg = [
        HookRegistration("on_prompt", "a.py", agent=None, ordering=1),
        HookRegistration("on_prompt", "b.py", agent="ADA", ordering=2),
        HookRegistration("on_prompt", "c.py", agent="JARVIS", ordering=3),
        HookRegistration("on_prompt", "d.py", agent="JARVIS", enabled=False, ordering=4),
    ]
    sel = [h.script_path for h in hooks_for(reg, "on_prompt", "JARVIS")]
    check("hooks_for filtra agente+enabled y ordena", sel == ["a.py", "c.py"])
    sel_ada = [h.script_path for h in hooks_for(reg, "on_prompt", "ADA")]
    check("hooks_for para ADA no ve el de JARVIS", sel_ada == ["a.py", "b.py"])

    # 4b. matcher: un hook con matcher SÓLO aplica si el payload trae el archivo que matchea.
    reg_m = [
        HookRegistration("on_file_change", "watch_wake.py", matcher="jarvis_wakeup.jsonl"),
        HookRegistration("on_file_change", "watch_nexus.py", matcher="nexus_inbox.jsonl"),
        HookRegistration("on_file_change", "watch_all.py"),  # sin matcher = siempre
    ]
    sel_wake = [h.script_path for h in hooks_for(reg_m, "on_file_change", "JARVIS",
                                                 {"path": "/x/jarvis_wakeup.jsonl"})]
    check("matcher filtra: solo el hook del archivo que cambió (+ el sin-matcher)",
          sel_wake == ["watch_all.py", "watch_wake.py"])
    sel_none = [h.script_path for h in hooks_for(reg_m, "on_file_change", "JARVIS", {})]
    check("matcher: sin path en payload, los con matcher NO aplican (solo el global)",
          sel_none == ["watch_all.py"])

    # 5. dispatch corre los seleccionados y un fallo NO tumba a los demás.
    def runner(script, env):
        if script == "c.py":
            raise RuntimeError("boom")
        return True
    out = dispatch(_env("on_prompt"), reg, runner)
    check("dispatch corrió a.py y c.py (c falló, a no)",
          out["effects"] == {"a.py": True, "c.py": False} and out["all_ran"] is False)

    # 6. dispatch con todos OK => all_ran True.
    out2 = dispatch(_env("on_prompt"), reg, lambda s, e: True)
    check("dispatch all_ran True cuando todos corren", out2["all_ran"] is True)

    # 7. Paridad: candidato con MENOS efectos en un crítico => learning_ok False (fail-closed).
    base = {"on_turn_end": ["memory_extraction_hook.py", "turn_extract_stop_hook.py"],
            "on_tool_result": ["post_tool_hook.py"]}
    cand_ok = dict(base)
    check("paridad completa => parity True, learning_ok True",
          parity_report(base, cand_ok) == {"parity": True, "learning_ok": True, "missing_by_event": {}})
    cand_lose_learning = {"on_turn_end": ["memory_extraction_hook.py"],  # perdió uno crítico
                          "on_tool_result": ["post_tool_hook.py"]}
    rep = parity_report(base, cand_lose_learning)
    check("perder efecto en evento crítico => learning_ok False",
          rep["parity"] is False and rep["learning_ok"] is False
          and rep["missing_by_event"]["on_turn_end"] == ["turn_extract_stop_hook.py"])
    cand_lose_op = {"on_turn_end": ["memory_extraction_hook.py", "turn_extract_stop_hook.py"],
                    "on_tool_result": []}  # perdió sólo operativo
    rep2 = parity_report(base, cand_lose_op)
    check("perder sólo operativo => parity False pero learning_ok True",
          rep2["parity"] is False and rep2["learning_ok"] is True)

    # 8. El seed del ground truth cubre los 9 eventos y separa learning/operational.
    seed = ground_truth_seed()
    evs = {h.soul_event for h in seed}
    learning_kinds = {h.kind for h in seed if h.soul_event in LEARNING_EVENTS}
    check("seed cubre los 9 eventos", evs == set(SOUL_EVENTS))
    check("eventos críticos marcados kind=learning", learning_kinds == {"learning"})
    check("claude map tiene 9 entradas", len(CLAUDE_CODE_EVENT_MAP) == 9)

    print()
    if fails:
        print(f"RESULTADO: {fails} fallo(s) ❌")
        return 1
    print("RESULTADO: TODO PASS ✅ — núcleo F1 (interfaz de eventos SOUL) verificado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
