"""Tests de cognitive_invariants.py — gates de método (JARVIS, build autónomo).
Aplica el propio invariante E1: no declaro el módulo 'listo' sin probar el EFECTO.
Sin DB, deterministas."""
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from cognitive_invariants import (
    evidence_gate, recall_gate, benchmark_gate, closure_gate,
)

passed, failed = [], []
def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)

# ---- evidence_gate ----
ok, _ = evidence_gate({"item_type": "fact", "claim": "x", "evidence": None})
check("E1 fact sin evidencia -> BLOQUEADO", ok is False)

ok, _ = evidence_gate({"item_type": "fact", "evidence": {"command": "ls", "output": "a b"}})
check("E2 fact con command+output -> OK", ok is True)

ok, _ = evidence_gate({"item_type": "decision", "claim": "se aprobó X"})
check("E3 decision (no fact, no victoria) -> OK sin evidencia", ok is True)

ok, _ = evidence_gate({"claim": "listo, ya está cerrado"})
check("E4 claim de victoria 'listo/cerrado' sin evidencia -> BLOQUEADO", ok is False)

ok, _ = evidence_gate({"claim": "verificado", "evidence": {"test": "t1", "result": "5 pass"}})
check("E5 'verificado' con test+result -> OK", ok is True)

ok, _ = evidence_gate({"item_type": "fix", "evidence": {"foo": "bar"}})
check("E6 fix con evidencia BASURA (sin par probatorio) -> BLOQUEADO", ok is False)

ok, _ = evidence_gate({"item_type": "fact", "evidence": {"url": "http://x", "status": 200}})
check("E7 fact con url+status -> OK", ok is True)

# ---- recall_gate ----
ok, _ = recall_gate(123, lambda k: {"id": k, "content": "hola"})
check("R1 recall devuelve algo -> OK", ok is True)

ok, _ = recall_gate(123, lambda k: None)
check("R2 recall vacío -> BLOQUEADO", ok is False)

ok, _ = recall_gate(123, lambda k: (_ for _ in ()).throw(RuntimeError("db down")))
check("R3 recall lanza -> BLOQUEADO (fail-closed)", ok is False)

ok, _ = recall_gate(None, lambda k: "x")
check("R4 sin clave -> BLOQUEADO", ok is False)

# ---- benchmark_gate ----
ok, _ = benchmark_gate({"metric": "recall@5", "suite": "s1", "before": 0.7, "after": 0.85})
check("B1 mejora real -> OK", ok is True)

ok, _ = benchmark_gate({"metric": "recall@5", "suite": "s1", "before": 0.85, "after": 0.70})
check("B2 regresión -> BLOQUEADO", ok is False)

ok, _ = benchmark_gate({"before": 1, "after": 2})
check("B3 sin metric+suite -> BLOQUEADO", ok is False)

ok, _ = benchmark_gate({"metric": "m", "suite": "s", "before": 0.5, "after": 0.5})
check("B4 igual sin nota -> BLOQUEADO", ok is False)

ok, _ = benchmark_gate({"metric": "m", "suite": "s", "before": 0.5, "after": 0.5, "note": "estable a propósito"})
check("B5 igual CON nota -> OK", ok is True)

# ---- closure_gate (compuesto) ----
ok, _ = closure_gate({"item_type": "fix", "evidence": {"command": "pytest", "output": "5 passed"}})
check("C1 cierre con evidencia, sin claim de mejora -> OK", ok is True)

ok, _ = closure_gate({"claim": "listo", "evidence": {"command": "x", "output": "y"},
                      "improvement": {"metric": "m", "suite": "s", "before": 1, "after": 0}})
check("C2 cierre con evidencia PERO mejora en regresión -> BLOQUEADO", ok is False)

print(f"\n== {len(passed)} PASS / {len(failed)} FAIL ==")
sys.exit(1 if failed else 0)
