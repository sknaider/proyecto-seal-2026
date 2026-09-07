"""assign_agent — ruteo 'qué agente debe actuar' (regla 3.6 de la spec del núcleo).

Autor: JARVIS (arquitectura) · 2026-06-09 (build autónomo).
Llena el ÚNICO faltante del contrato de orquestación (design_cognitive_orchestration_v1.md).
Función PURA, determinista, read-only. El núcleo (ADA) la llama en el stage ASSIGN.

Mapeo (spec §3.6):
  NEXUS  → seguridad, auditoría, integridad, evidencia adversarial.
  ADA    → construir, integrar, probar, código.
  JARVIS → arquitectura, invariantes, revisión de alcance, diseño.
  ALICE  → producto, UI, ROI, usabilidad, documentación, RAG.
  DUM    → vigilancia, guardia, alertas, tareas simples.

Orden de evaluación INTENCIONAL: seguridad primero (mis-asignar seguridad es lo más costoso),
luego guardia, luego los demás. Default = JARVIS (el arquitecto triagea lo ambiguo).
"""
from __future__ import annotations
import re

KNOWN_AGENTS = ("NEXUS", "DUM", "ADA", "ALICE", "JARVIS")

# (agente, [patrones]). Orden = prioridad de match. Patrones en minúscula, word-ish.
_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("NEXUS", (
        "segur", "exploit", "vulnerab", "audit", "pentest", "privacid", "identidad",
        "spoof", "impersonac", "fail-closed", "adversari", "integridad", "cve", "owasp",
        "autenticac", "autorizac", "permiso", "credencial", "ataque",
    )),
    ("DUM", (
        "vigil", "guardia", "monitor", "alerta", "watch", "heartbeat", "uptime",
        "temperatura", "gpu temp", "ronda", "patrulla",
    )),
    ("ALICE", (
        "ui", "ux", "frontend", "diseño visual", "usabilidad", "producto", "roi",
        "documentac", "onboarding", "rag", "perception", "negocio", "pricing",
        "landing", "dashboard visual", "wizard", "manual",
    )),
    ("ADA", (
        "implement", "construir", "integrar", "codear", "código", "build", "test",
        "deploy", "migrac", "endpoint", "script", "refactor", "fix bug", "parche",
        "compilar", "instalar",
    )),
    ("JARVIS", (
        "arquitect", "invariante", "diseño", "scope", "alcance", "revisión", "review",
        "contrato", "estrategia", "plan", "spec", "orquestac",
    )),
]


def assign_agent(task: str | dict, *, default: str = "JARVIS") -> dict:
    """Devuelve {agent, confidence, reason, matched}.

    task: texto de la tarea, o dict con 'title'/'description'/'objective'.
    confidence: 'high' si match único, 'medium' si varios candidatos, 'low' si default.
    """
    text = task if isinstance(task, str) else " ".join(
        str(task.get(k, "")) for k in ("title", "description", "objective", "claim")
    )
    low = text.lower()

    # Respeta un owner explícito ya nombrado en el texto ("JARVIS:", "para NEXUS").
    for a in KNOWN_AGENTS:
        if re.search(rf"\b{a.lower()}\b\s*[:,]", low) or f"para {a.lower()}" in low:
            return {"agent": a, "confidence": "high", "reason": f"owner explícito '{a}' en el texto",
                    "matched": [a]}

    hits: list[tuple[str, str]] = []
    for agent, pats in _RULES:
        for p in pats:
            # Word-boundary al INICIO del patrón: 'ui' matchea la palabra 'ui' pero NO el
            # 'ui' dentro de 'arq[ui]tectura'; 'arquitect' sí matchea 'arquitectura' (prefijo).
            if re.search(r"\b" + re.escape(p), low):
                hits.append((agent, p))
                break  # un match por agente basta

    if not hits:
        return {"agent": default, "confidence": "low",
                "reason": "sin señal clara → triage del arquitecto (default)", "matched": []}
    if len(hits) == 1:
        a, p = hits[0]
        return {"agent": a, "confidence": "high", "reason": f"match único: '{p}'", "matched": [a]}
    # Varios candidatos → gana el de mayor prioridad (orden de _RULES); reporta los demás.
    a, p = hits[0]
    return {"agent": a, "confidence": "medium",
            "reason": f"varios candidatos {[h[0] for h in hits]}; gana por prioridad: '{p}'",
            "matched": [h[0] for h in hits]}


__all__ = ["assign_agent", "KNOWN_AGENTS"]
