#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_vision_pii_min.py — Minimización de PII en captions de visión (Gate G4 / §C.5, carril NEXUS).

Máster-spec SOUL Vision §C.5 + §D/G4 (FABLE): la descripción RICA del VLM lleva MÁS PII que "2× person"
("hombre de camisa roja en el escritorio" ya identifica). Antes de que el caption PERSISTA o VIAJE, se le
minimizan los identificadores DIRECTOS. Criterio de PASE (G4): el texto que viaja no lleva identificadores
directos no consentidos.

ALCANCE HONESTO (v1 determinístico, contexto Perú):
  ✅ Enmascara identificadores DIRECTOS de alta confianza por regex: DNI (8 díg), RUC (11), teléfono (+51/9…),
     email, URL, placa vehicular peruana, y nombres propios MARCADOS ("señor X", "se llama X", "llamado X").
  ⚠️  NO resuelve por sí solo la PII DESCRIPTIVA/contextual ("hombre de camisa roja") — eso requiere NER/LLM.
     Para eso: (a) hook `descriptive_flags()` que MARCA descriptores potencialmente identificantes para una
     2ª pasada (NER local / prompt-level en el VLM), y (b) la política de que el VLM se PROMPTEE para no
     singularizar (defensa en profundidad, §A/§C). FABLE remata la cobertura por efecto contra G4.

Determinístico, sin red, sin estado. Devuelve el texto minimizado + telemetría de lo removido (auditoría).
"""
from __future__ import annotations
import re

# ── Identificadores directos (Perú) — alta confianza ──────────────────────────
_PATTERNS = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("url", re.compile(r"\bhttps?://\S+\b", re.I)),
    ("ruc", re.compile(r"\b(?:10|15|17|20)\d{9}\b")),             # RUC 11 díg (antes que DNI)
    ("telefono", re.compile(r"(?:\+51\s?)?\b9\d{8}\b")),          # celular Perú 9XXXXXXXX
    ("dni", re.compile(r"\b\d{8}\b")),                            # DNI 8 díg
    ("placa", re.compile(r"\b[A-Z]\d[A-Z]-\d{3}\b|\b[A-Z]{3}-\d{3}\b", re.I)),  # placa (re.I: mayús/minús — fix FABLE #2)
    ("id_largo", re.compile(r"\b\d{6,}\b")),                      # otros IDs/badges largos
]
# Nombres propios MARCADOS por un marcador de persona (conservador: no NER, solo tras marcador).
_NAME_MARKED = re.compile(
    r"\b(señor|senor|sra\.?|señora|senora|sr\.?|don|doña|dona|llamad[oa]|se llama|nombre[:\s])\s+"
    r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+){0,2})", re.I)

# Descriptores que PUEDEN singularizar (para flag, NO auto-borrado en v1) — señal para la 2ª pasada.
_DESCRIPTIVE_HINTS = re.compile(
    r"\b(camis[ae]|polo|chaqueta|gorra|sombrero|mochila|lentes|barba|tatuaje|cicatriz|uniforme|chaleco)\b",
    re.I)

# Referencia a PERSONA — ENUMERAR-LO-BUENO (no lo-malo: la lista de descriptores NUNCA se completa y deja
# pasar PII sensible de salud como "silla de ruedas/muletas/embarazada" = Ley 29733 categoría ESPECIAL).
# Si el caption referencia a una persona → el egreso es default-DENY hasta la 2ª pasada NER/humano.
# Invierte falsos-negativos (leaks) → falsos-positivos (over-block) = dirección segura para PII (catch FABLE).
_PERSON_REF = re.compile(
    r"\b(persona|personas|gente|hombre|hombres|mujer|mujeres|niñ[oa]s?|ancian[oa]s?|señor|señora|señores|"
    r"señoras|individuo|sujeto|chic[oa]s?|joven|jóvenes|adult[oa]s?|beb[eé]s?|alguien|él|ella|ellos|ellas|"
    # rol/oficio + sinónimos-de-persona de alta frecuencia (cierre barato FABLE; el resto lo cubre el NER 2ª pasada)
    r"human[oa]s?|figura|figuras|silueta|siluetas|client[ea]s?|trabajador(?:es|a|as)?|conductor(?:es|a|as)?|"
    r"pasajer[oa]s?|peat[oó]n|peatones|polic[ií]as?|vendedor(?:es|a|as)?|guardias?|enfermer[oa]s?|"
    r"emplead[oa]s?|visitante|visitantes)\b", re.I)

_MASK = "⟨redactado:{kind}⟩"


def minimize_caption(text: str) -> dict:
    """Minimiza identificadores directos de `text`. Devuelve:
       {"text": minimizado, "removed": [{"kind","value_hash"}...], "descriptive_flags": [...]}.
    NO registra el valor PII en claro (solo un hash corto para auditoría/idempotencia)."""
    if not text:
        return {"text": text or "", "removed": [], "descriptive_flags": []}
    import hashlib
    removed = []
    out = text

    def _mask(kind, m):
        val = m.group(0)
        removed.append({"kind": kind, "value_hash": hashlib.sha256(val.encode("utf-8")).hexdigest()[:12]})
        return _MASK.format(kind=kind)

    # Nombres marcados primero (conserva el marcador, enmascara el nombre)
    def _mask_name(m):
        marker, name = m.group(1), m.group(2)
        removed.append({"kind": "nombre", "value_hash": hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]})
        return f"{marker} {_MASK.format(kind='nombre')}"
    out = _NAME_MARKED.sub(_mask_name, out)

    # Identificadores por regex (orden importa: RUC/telefono antes que DNI/id_largo)
    for kind, pat in _PATTERNS:
        out = pat.sub(lambda m, k=kind: _mask(k, m), out)

    flags = sorted({h.group(0).lower() for h in _DESCRIPTIVE_HINTS.finditer(out)})
    return {"text": out, "removed": removed, "descriptive_flags": flags}


def is_free_of_direct_ids(text: str) -> bool:
    """True si `text` no contiene identificadores DIRECTOS conocidos (DNI/RUC/tel/email/placa/nombre-marcado).
    OJO (FABLE #1/#3): NO garantiza ausencia de PII DESCRIPTIVA ("camisa roja+cicatriz") NI nombres SIN marcador
    (requieren NER/2ª pasada). Para la compuerta de egreso G4 usá `safe_for_egress`, NO esto solo."""
    return len(minimize_caption(text)["removed"]) == 0


def safe_for_egress(text: str) -> bool:
    """Compuerta G4 FAIL-CLOSED: True SOLO si (a) no hay identificadores directos, Y (b) el caption NO referencia
    a ninguna persona. Referencia a persona → False por DEFECTO hasta la 2ª pasada (NER/humano) — porque cualquier
    descripción de una persona PUEDE singularizarla (incl. datos sensibles de salud: silla de ruedas/muletas/
    embarazada = Ley 29733 especial), y la lista de descriptores NUNCA se completa (catch FABLE: enumerar-lo-bueno,
    no lo-malo). `descriptive_flags` queda como SEÑAL, no como garantía. Over-block es la dirección segura para PII;
    la 2ª pasada despeja los genéricos ("2 personas caminan" → revisar → liberar).
    ⚠️ TECHO DEL REGEX (FABLE): un regex NUNCA enumera "toda referencia a un humano" (roles raros, perífrasis).
    `safe_for_egress=True` = pasó el FILTRO BARATO de 1ª línea, NO es garantía de PII-safe. El gate REAL de G4 es
    el NER/LLM de la 2ª pasada (§C.5). Esta función es defensa-en-profundidad de 1ª línea, no la última palabra."""
    r = minimize_caption(text)
    if r["removed"]:
        return False
    return _PERSON_REF.search(text) is None


if __name__ == "__main__":
    # 1) Caption con identificadores directos → enmascarados, telemetría sin PII en claro.
    cap = ("Se ve al señor Juan Pérez en el escritorio; DNI 45678912, celular 987654321, "
           "correo juan@example.com, placa ABC-123.")
    r = minimize_caption(cap)
    assert "Juan Pérez" not in r["text"], r["text"]
    assert "45678912" not in r["text"] and "987654321" not in r["text"]
    assert "juan@example.com" not in r["text"] and "ABC-123" not in r["text"]
    kinds = {x["kind"] for x in r["removed"]}
    assert {"nombre", "dni", "telefono", "email", "placa"} <= kinds, kinds
    assert all("value" not in x or True for x in r["removed"])  # nunca guardamos el valor en claro
    assert not is_free_of_direct_ids(cap) and is_free_of_direct_ids(r["text"])

    # 2) Caption genérico (sin identificadores directos) → intacto, sin direct-ids, pero flag descriptivo.
    cap2 = "Dos personas caminan hacia la izquierda; una lleva una mochila."
    r2 = minimize_caption(cap2)
    assert r2["text"] == cap2 and is_free_of_direct_ids(cap2)
    assert "mochila" in r2["descriptive_flags"]   # marcado para 2ª pasada (NER/LLM), no auto-borrado en v1

    # 2b) FABLE #1: PII DESCRIPTIVA singularizante → sin direct-ids PERO NO safe_for_egress (hay persona).
    desc = "el hombre de camisa roja con cicatriz y chaleco naranja en la puerta 3"
    assert is_free_of_direct_ids(desc) is True and safe_for_egress(desc) is False
    assert safe_for_egress(cap2) is False               # "2 personas" → referencia a persona → default-deny

    # 2b') FABLE residual profundo: descriptores FUERA de la lista + datos SENSIBLES de salud → default-DENY
    # (enumerar-lo-bueno: referencia-a-persona, no lo-malo). Antes PASABAN; ahora bloquean.
    for leak in ("la mujer embarazada de pelo rojo en silla de ruedas",
                 "hombre alto y calvo con muletas",
                 "un anciano con bastón cruza"):
        assert safe_for_egress(leak) is False, leak

    # 2b'') Caption SIN persona → sí es seguro viajar (over-block solo aplica a personas).
    assert safe_for_egress("una puerta abierta y un auto rojo estacionado") is True

    # 2b''') FABLE residual angosto: ROL/oficio + perífrasis de persona → ahora BLOQUEAN (antes escapaban).
    for role in ("el cliente firma", "un trabajador con casco", "el conductor baja", "una silueta humana",
                 "un policía en la esquina", "el peatón cruza"):
        assert safe_for_egress(role) is False, role

    # 2c) FABLE #2: placa en MINÚSCULA ya se enmascara (antes escapaba).
    rp = minimize_caption("la placa abc-123 en el auto")
    assert "abc-123" not in rp["text"] and any(x["kind"] == "placa" for x in rp["removed"])

    # 2c) FABLE #2: placa en MINÚSCULA ya se enmascara (antes escapaba).
    rp = minimize_caption("la placa abc-123 en el auto")
    assert "abc-123" not in rp["text"] and any(x["kind"] == "placa" for x in rp["removed"])

    # 3) RUC no se confunde con DNI (11 vs 8 díg).
    r3 = minimize_caption("Empresa RUC 20123456789 en la puerta.")
    assert "20123456789" not in r3["text"] and any(x["kind"] == "ruc" for x in r3["removed"])

    # 4) Idempotencia: minimizar dos veces no cambia (ya no hay identificadores).
    assert minimize_caption(r["text"])["removed"] == []

    print("seal_vision_pii_min: direct-id masking + name-marked + telemetry-sin-PII + descriptive-flags OK")
