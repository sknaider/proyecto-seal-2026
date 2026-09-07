"""Tests de cognitive_assign.py (JARVIS, build autónomo). Deterministas, sin DB."""
import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from cognitive_assign import assign_agent

passed, failed = [], []
def check(name, cond):
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name)

check("seguridad -> NEXUS", assign_agent("auditar vulnerabilidad de privacidad")["agent"] == "NEXUS")
check("exploit -> NEXUS", assign_agent("revisar el exploit de impersonacion")["agent"] == "NEXUS")
check("construir/test -> ADA", assign_agent("implementar el endpoint y correr los tests")["agent"] == "ADA")
check("arquitectura -> JARVIS", assign_agent("revisar el alcance y el contrato de arquitectura")["agent"] == "JARVIS")
check("UI/producto -> ALICE", assign_agent("mejorar la UI y la usabilidad del dashboard")["agent"] == "ALICE")
check("guardia -> DUM", assign_agent("vigilar la temperatura de la GPU y alertar")["agent"] == "DUM")
check("ambiguo -> default JARVIS low", assign_agent("hacer algo con el sistema")["confidence"] == "low")
check("owner explicito 'NEXUS:' gana", assign_agent("NEXUS: revisa la UI")["agent"] == "NEXUS")
check("para alice", assign_agent("esto es para ALICE")["agent"] == "ALICE")

# dict input
r = assign_agent({"title": "Fix de seguridad", "description": "cerrar el spoofing de identidad"})
check("dict input seguridad -> NEXUS", r["agent"] == "NEXUS")

# prioridad: seguridad gana sobre UI cuando ambos aparecen
r = assign_agent("rediseñar la UI del modulo de seguridad y auditoria")
check("seguridad+UI -> gana NEXUS (prioridad), reporta ambos", r["agent"] == "NEXUS" and "ALICE" in r["matched"])

print(f"\n== {len(passed)} PASS / {len(failed)} FAIL ==")
sys.exit(1 if failed else 0)
