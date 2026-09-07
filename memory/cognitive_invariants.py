"""Invariantes de MÉTODO del Núcleo de Cognición SOUL — gates puros checkeables.

Autor: JARVIS (arquitectura) · 2026-06-09 (build autónomo).
Convierte la disciplina ganada a pulso (verificar el EFECTO, no la presencia) en gates
ENFORZABLES, para que el núcleo (ADA: soul_cognitive_core.py) los importe y llame.

Coordinación sin pisarse: archivo PROPIO de JARVIS; ADA lo integra, no edita.
Lane split: JARVIS = invariantes de método/epistémicos (#3 evidence, #8 recall, #9 benchmark);
NEXUS = invariantes de seguridad (#4 destructivo, #5 DM, #6 identidad, #7 daemon).

Gates puros: reciben datos, devuelven (ok: bool, reason: str). Sin efectos secundarios en la
lógica de decisión → fáciles de testear y de auditar. El recall_gate recibe un callable para no
acoplar a la DB.
"""
from __future__ import annotations
from typing import Any, Callable

# Tipos epistémicos que EXIGEN evidencia del efecto (no basta afirmar).
EVIDENCE_REQUIRED_TYPES = frozenset({"fact", "fix"})

# Frases de "victoria" que NO se aceptan sin evidencia (la lección de William).
VICTORY_CLAIMS = (
    "listo", "done", "completado", "completo", "cerrado", "terminado",
    "verificado", "funciona", "resuelto", "arreglado", "curado",
)


def _has_real_evidence(evidence: Any) -> bool:
    """Evidencia válida = prueba del EFECTO, no solo que algo respondió.

    Acepta un dict con AL MENOS uno de estos pares probatorios:
      - command + output      (se corrió algo y se vio la salida)
      - url + status          (se golpeó un endpoint y dio status)
      - verified_at + verifier (alguien/algo verificó, con marca de quién/cuándo)
      - test + result         (corrió un test con resultado)
    Un dict vacío, None, o solo texto suelto NO es evidencia.
    """
    if not isinstance(evidence, dict) or not evidence:
        return False
    pairs = (
        ("command", "output"),
        ("url", "status"),
        ("verified_at", "verifier"),
        ("test", "result"),
    )
    for a, b in pairs:
        if evidence.get(a) and evidence.get(b) is not None:
            return True
    return False


def evidence_gate(item: dict) -> tuple[bool, str]:
    """Invariante #3 / Epistemic E1: un 'fact'/'fix' o un claim de victoria DEBE traer
    evidencia del EFECTO. Sin ella → no pasa (baja a hypothesis o se rechaza el 'listo').

    item: {item_type?, claim?, evidence?}
    """
    item_type = str(item.get("item_type", "")).lower()
    claim = str(item.get("claim", "")).lower()
    evidence = item.get("evidence")

    needs_evidence = item_type in EVIDENCE_REQUIRED_TYPES or any(w in claim for w in VICTORY_CLAIMS)
    if not needs_evidence:
        return True, "no requiere evidencia (no es fact/fix ni claim de victoria)"
    if _has_real_evidence(evidence):
        return True, "evidencia del efecto presente"
    return False, (
        f"BLOQUEADO: '{item_type or 'claim'}' afirma victoria/hecho sin evidencia del efecto. "
        "Adjuntar command+output / url+status / test+result / verified_at+verifier. "
        "(Regla William: verificar el EFECTO, no la presencia.)"
    )


def recall_gate(stored_key: Any, recall_fn: Callable[[Any], Any]) -> tuple[bool, str]:
    """Invariante #8: una memoria crítica recién guardada DEBE ser recuperable.
    recall_fn(stored_key) debe devolver algo truthy que la represente. Sin recall verificado,
    el aprendizaje NO está cerrado (se guardó pero no se 'aprendió').

    recall_fn se inyecta (no acoplamos a la DB acá). Si lanza, el gate falla cerrado.
    """
    if stored_key in (None, "", 0):
        return False, "BLOQUEADO: sin clave de memoria para verificar recall"
    try:
        recalled = recall_fn(stored_key)
    except Exception as e:  # fail-closed
        return False, f"BLOQUEADO: recall lanzó excepción ({e}); aprendizaje no verificado"
    if recalled:
        return True, "recall verificado: la memoria se recupera tras guardarse"
    return False, "BLOQUEADO: la memoria se guardó pero NO se recupera (recall vacío)"


def benchmark_gate(improvement: dict) -> tuple[bool, str]:
    """Invariante #9: ninguna mejora cognitiva se declara sin DELTA de benchmark.
    improvement: {metric, suite, before, after, note?}. 'after' debe mejorar respecto a 'before'
    (o ser igual SOLO con nota explícita). Sin métrica → no es mejora, es sensación.
    """
    metric = improvement.get("metric")
    suite = improvement.get("suite")
    before = improvement.get("before")
    after = improvement.get("after")
    if not metric or not suite:
        return False, "BLOQUEADO: mejora sin metric+suite → no medible (sensación, no evidencia)"
    if before is None or after is None:
        return False, f"BLOQUEADO: mejora en '{metric}' sin before/after → no hay delta"
    try:
        if float(after) > float(before):
            return True, f"mejora medida: {metric} {before}→{after} en {suite}"
        if float(after) == float(before):
            if improvement.get("note"):
                return True, f"sin delta pero justificado: {improvement['note']}"
            return False, f"BLOQUEADO: {metric} sin cambio ({before}={after}) y sin nota que lo explique"
        return False, f"BLOQUEADO: REGRESIÓN en {metric} ({before}→{after}) — no es mejora"
    except (TypeError, ValueError):
        return False, f"BLOQUEADO: before/after de '{metric}' no comparables numéricamente"


# Gate compuesto: ¿se puede declarar una tarea/objetivo 'cerrado'?
def closure_gate(item: dict) -> tuple[bool, str]:
    """Invariante de cierre: CERRAR una tarea/objetivo = afirmar 'hecho' = SIEMPRE requiere
    evidencia del EFECTO (no importa si el texto 'afirma victoria' o no — el acto de cerrar YA
    es la afirmación). Y si además afirma mejora, exige benchmark (#9). Este es el '¿está
    REALMENTE listo?' — una tarea sin prueba NO es cerrable, aunque no diga 'listo'.
    """
    if not _has_real_evidence(item.get("evidence")):
        return False, (
            "BLOQUEADO: cerrar requiere evidencia del EFECTO (command+output / url+status / "
            "test+result / verified_at+verifier). No se cierra sin prueba — falta verificar."
        )
    improvement = item.get("improvement")
    if improvement:
        ok_b, reason_b = benchmark_gate(improvement)
        if not ok_b:
            return False, reason_b
    return True, "cierre permitido: evidencia del efecto (+ benchmark si afirma mejora) OK"


__all__ = ["evidence_gate", "recall_gate", "benchmark_gate", "closure_gate",
           "EVIDENCE_REQUIRED_TYPES", "VICTORY_CLAIMS"]
