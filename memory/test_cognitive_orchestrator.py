"""Tests de cognitive_orchestrator.compose_decision (JARVIS). Sin DB, deterministas."""
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from cognitive_orchestrator import compose_decision

passed, failed = [], []
def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)

# Tarea de seguridad sin evidencia → owner NEXUS, closure BLOQUEADO.
tasks = [{"title": "auditar el exploit de identidad", "task_id": 1, "score": 0.9}]
r = compose_decision(tasks)
d = r["decisions"][0]
check("safe sin safety_gate -> safe True", r["safe"] is True)
check("seguridad -> owner NEXUS", d["owner"] == "NEXUS")
check("sin evidencia -> closure BLOQUEADO", d["closure_ok"] is False)

# Tarea con claim de victoria + evidencia real → closure OK.
tasks2 = [{"title": "implementar fix y verificado", "task_id": 2,
           "evidence": {"command": "pytest", "output": "5 passed"}}]
d2 = compose_decision(tasks2)["decisions"][0]
check("build con evidencia -> owner ADA", d2["owner"] == "ADA")
check("evidencia real -> closure OK", d2["closure_ok"] is True)

# safety_gate=False → decisión BLOQUEADA global (fail-closed), sin decisiones.
r3 = compose_decision(tasks, safety_gate=lambda: (False, ["destructivo sin OK"]))
check("safety_gate False -> blocked, 0 decisiones", r3["safe"] is False and r3["decisions"] == [])

# safety_gate lanza → fail-closed.
r4 = compose_decision(tasks, safety_gate=lambda: (_ for _ in ()).throw(RuntimeError("x")))
check("safety_gate lanza -> fail-closed", r4["safe"] is False)

# safety_gate=True → procede.
r5 = compose_decision(tasks, safety_gate=lambda: (True, []))
check("safety_gate True -> procede con decisiones", r5["safe"] is True and len(r5["decisions"]) == 1)

# top=2 → 2 decisiones.
multi = [{"title": "revisar arquitectura"}, {"title": "mejorar la UI"}]
r6 = compose_decision(multi, top=2)
check("top=2 -> 2 decisiones, owners JARVIS+ALICE",
      [x["owner"] for x in r6["decisions"]] == ["JARVIS", "ALICE"])

print(f"\n== {len(passed)} PASS / {len(failed)} FAIL ==")
sys.exit(1 if failed else 0)
