#!/usr/bin/env python3
"""
test_soul_runtime_adapter.py — F2 groundwork (adaptador). Oráculos ruidosos, exit!=0 si falla.
"""
import sys
from soul_event_interface import LEARNING_EVENTS
from soul_runtime_adapter import (
    ClaudeCodeAdapter, LocalRuntimeAdapter, get_adapter, ADAPTERS,
)


def main() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(f"[{'PASS' if cond else 'FAIL'}] {name}")
        fails += 0 if cond else 1

    cc = ClaudeCodeAdapter()
    loc = LocalRuntimeAdapter()

    # 1. Claude Code: evento nativo -> sobre SOUL correcto.
    env = cc.to_envelope("Stop", "JARVIS", "s1", "2026-08-05T00:00:00", {"k": 1})
    check("ClaudeCode Stop -> on_turn_end", env.soul_event == "on_turn_end")
    check("sobre lleva agent/runtime/payload",
          env.agent == "JARVIS" and env.runtime == "claude_code" and env.payload == {"k": 1})

    # 2. Runtime local: nombre nativo distinto -> MISMO soul_event (portabilidad).
    env2 = loc.to_envelope("turn_complete", "ADA", "s2", "2026-08-05T00:00:01")
    check("Local turn_complete -> on_turn_end (mismo soul_event, otro runtime)",
          env2.soul_event == "on_turn_end" and env2.runtime == "local_llama")

    # 3. Evento sin mapear FALLA RUIDOSO en ambos.
    for ad, name in ((cc, "claude"), (loc, "local")):
        try:
            ad.to_envelope("EventoQueNoExiste", "X", "s", "t")
            check(f"{name}: evento no mapeado lanza", False)
        except ValueError:
            check(f"{name}: evento no mapeado lanza", True)

    # 4. PARIDAD por construcción: ambos runtimes emiten los 4 críticos de aprendizaje.
    for ad in (cc, loc):
        missing = [e for e in LEARNING_EVENTS if e not in ad.emits()]
        check(f"{ad.runtime_name} emite los 4 críticos de aprendizaje", not missing)

    # 5. agent cae a SEAL_AGENT/UNKNOWN si no se pasa (sin romper).
    import os
    os.environ.pop("SEAL_AGENT", None)
    env3 = cc.to_envelope("SessionStart", None, "s", "t")
    check("sin agent explícito -> UNKNOWN (no crashea)", env3.agent == "UNKNOWN")

    # 6. get_adapter conocido/desconocido.
    check("get_adapter('claude_code') ok", get_adapter("claude_code").runtime_name == "claude_code")
    try:
        get_adapter("runtime_fantasma")
        check("get_adapter desconocido lanza", False)
    except ValueError:
        check("get_adapter desconocido lanza", True)

    print()
    if fails:
        print(f"RESULTADO: {fails} fallo(s) ❌")
        return 1
    print("RESULTADO: TODO PASS ✅ — adaptador traduce, falla ruidoso y mantiene paridad de aprendizaje")
    return 0


if __name__ == "__main__":
    sys.exit(main())
