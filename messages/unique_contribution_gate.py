"""unique_contribution_gate.py — NEXUS, 21-jul-2026.

Pieza 2 del fix de automatización (owner NEXUS; pieza 1 = ADA, gobernanza).
Cierra el loophole del `unique_contribution` AUTO-DECLARADO en chat_server.py:
antes, el override al deny del council solo exigía flag=true + reason>=20 chars,
sin verificar que el aporte fuera REALMENTE distinto de lo ya dicho en el hilo.

Principio (FABLE): la unicidad es un juicio sobre las OTRAS respuestas -> hay que
LEER lo ya posteado y bloquear el cuasi-duplicado, dejar pasar lo nuevo.

Diseño para INFRA VIVA:
  - FAIL-OPEN: es anti-flood, NO un control de seguridad. Ante cualquier fallo
    (query, excepción) -> se PERMITE. Nunca romper la comunicación por un bug.
  - Solo afecta el path del OVERRIDE (unique_contribution=true tras deny del
    council). Mensajes normales, DMs y la voz del lead NO se tocan -> radio chico.
  - Similitud LÉXICA (Jaccard de palabras, sin stopwords). Sin deps, sincrónica.
    No es semántica (parafraseos MUY distintos pasan); es un piso, no un techo.

Umbral 0.50 calibrado por efecto sobre datos reales (21-jul):
  aportes distintos reales <=0.22 · duplicado reformulado 0.53 · casi-idéntico 0.75
  -> 0.50 caza duplicados con cero falsos positivos (0.28 de margen).
"""
from __future__ import annotations
import re

DEFAULT_DUP_THRESHOLD = 0.50

_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = {
    "de","la","el","que","y","a","en","los","las","un","una","por","con","para",
    "es","se","su","al","lo","como","mas","más","o","le","ya","si","sí","del",
    "me","te","nos","mi","tu","the","to","of","and","in","is","it","this","that",
}
# Marcadores de NEGACIÓN. NO van en _STOP: cargan la polaridad. Un texto y su
# refutación ("X fue liberado" vs "X NO fue liberado") son casi idénticos léxicamente
# pero OPUESTOS. Sin esto, el gate censuraría correcciones/refutaciones — justo el
# aporte único que la regla protege (catch de FABLE, 21-jul, verificado por efecto).
_NEG = {
    # Negadores GRAMATICALES (inequívocos). Excluidos a propósito los ambiguos de
    # contenido ("nada"->"nada más"=solo, "mal", "error", "contrario") que aparecen
    # sin negar y darían falsa diferencia de polaridad -> dejarían pasar duplicados.
    "no","not","nunca","jamas","jamás","tampoco","ni","sin","ningun","ninguna",
    "ningún","ninguno","nadie","never","cannot","cant","dont","doesnt",
    "isnt","wasnt","wont","nope","incorrecto","falso",
}


def _tokens(s: str) -> set[str]:
    return {w for w in (m.group(0).lower() for m in _WORD.finditer(s or ""))
            if w not in _STOP and len(w) > 2}


def _negation_signature(s: str) -> int:
    """Cuenta marcadores de negación en el texto crudo (independiente de _STOP).
    Dos textos con firma distinta tienen polaridad distinta -> NO son duplicados."""
    return sum(1 for m in _WORD.finditer(s or "") if m.group(0).lower() in _NEG)


def lexical_similarity(a: str, b: str) -> float:
    """Jaccard de conjuntos de palabras (sin stopwords, len>2). 0..1."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    union = len(ta | tb)
    return (len(ta & tb) / union) if union else 0.0


async def find_near_duplicate_sibling(pool, in_reply_to, sender, text,
                                      threshold: float = DEFAULT_DUP_THRESHOLD,
                                      limit: int = 25):
    """Devuelve (agente, similitud) del hermano más parecido si supera el umbral,
    o None si el aporte es distinto / no hay hermanos / falta dato / error.
    FAIL-OPEN: cualquier excepción -> None (permitir). Nunca bloquea la comunicación."""
    if not pool or not in_reply_to or not text:
        return None
    try:
        rows = await pool.fetch(
            """SELECT sender_name, content FROM chat_messages
               WHERE metadata->>'in_reply_to' = $1
                 AND sender_name <> $2
               ORDER BY created_at DESC LIMIT $3""",
            in_reply_to, sender, limit,
        )
    except Exception:
        return None  # fail-open
    best = None
    my_neg = _negation_signature(text)
    for r in rows:
        content = r["content"] or ""
        try:
            sim = lexical_similarity(text, content)
        except Exception:
            continue
        if sim < threshold:
            continue
        # GUARDA DE POLARIDAD (catch FABLE): un texto y su refutación son casi
        # idénticos léxicamente pero OPUESTOS. Si la firma de negación difiere, NO
        # es un duplicado — es una corrección/refutación. Nunca censurar disenso;
        # erra hacia PERMITIR (consistente con fail-open y "mejor oír de más").
        if my_neg != _negation_signature(content):
            continue
        if best is None or sim > best[1]:
            best = (r["sender_name"], sim)
    return best
