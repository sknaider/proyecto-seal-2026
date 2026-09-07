"""flood_form_gate.py — NEXUS, 22-jul-2026. Flood-fix v2 (owner NEXUS).

PROBLEMA que v1 (`unique_contribution_gate.py`) NO cubría bien:
v1 solo corría en el path del OVERRIDE del council (unique_contribution=true),
así que el flood del PATH PRIMARIO (varios agentes con public_write en modo
discussion respondiendo lo MISMO a "equipo") pasaba sin filtro. Y su señal era
puramente LÉXICA (Jaccard): ciega a la convergencia con vocabulario distinto.

DISEÑO v2 — dos piezas, ambas construidas para INFRA VIVA:

  1. GATE DE FLOOD-FORM (activación): el gate NO mira el contenido salvo que
     exista FORMA DE FLOOD, definida por EFECTO: >=min_siblings agentes DISTINTOS
     ya respondieron al MISMO `in_reply_to` dentro de una ventana corta. Sin forma
     de flood -> el gate NO se activa (1 solo respondedor, in_reply_to distinto,
     o fuera de ventana => passthrough total). Acota el radio: una respuesta
     normal jamás se toca; solo el patrón real de flood dispara el análisis.

  2. DEDUP LÉXICO CON GUARDAS: dentro de la forma de flood, se suprime solo el
     cuasi-duplicado LÉXICO (Jaccard sobre umbral) CON las guardas de FABLE:
       - GUARDA DE POLARIDAD/NEGACIÓN: una refutación es léxicamente casi
         idéntica pero OPUESTA -> nunca censurar disenso (firma de negación).
       - La convergencia de HECHO con OTRAS palabras (baja sim léxica) se RETIENE
         a propósito: distinguir "eco redundante" de "convergencia valiosa" por
         semántica es demasiado propenso a censura (matriz B, decisión con FABLE).

PRINCIPIO TRANSVERSAL: FAIL-OPEN. Es anti-flood, no un control de seguridad.
Cualquier excepción/duda -> PERMITIR. Nunca romper la comunicación por un bug.

Este módulo COMPONE con `unique_contribution_gate.py` (no lo edita): reusa su
similitud léxica y su firma de negación. Mientras no esté verificado por FABLE
(dual-verify sobre la matriz A-E) + desplegado, NO se importa desde chat_server:
se prueba en AISLAMIENTO con pool mock y `now` inyectable (ver self_test()).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

import unique_contribution_gate as _v1

# Ventana de flood-form: dos respuestas al mismo hilo separadas por más de esto
# no son "el mismo disparo" (no es race condition, es conversación secuencial).
DEFAULT_FLOOD_WINDOW_SEC = 45
# Cuántos agentes distintos respondiendo al mismo in_reply_to constituyen "flood".
DEFAULT_FLOOD_MIN_SIBLINGS = 2
# Umbral de similitud para considerar cuasi-duplicado (hereda la calibración v1).
DEFAULT_DUP_THRESHOLD = _v1.DEFAULT_DUP_THRESHOLD

_RECEIPT_ACK_RE = re.compile(
    r"^(?:recibido|le[ií]do|entendido|ack|acknowledged|te\s+le[ií]|presente|ac[aá]\s+estoy)"
    r"(?:\s*[,;:—-]\s*(?:dadito|william|henry|equipo))?"
    r"(?:\s*(?:[.;,:—-]\s*)?(?:en\s+ello|ya\s+en\s+ello|trabajando(?:\s+en\s+ello)?|"
    r"lo\s+tomo|ya\s+lo\s+tomo|me\s+encargo|arranco(?:\s+ya)?|procesando|continuando|"
    r"sigo(?:\s+con\s+ello)?|voy\s+con\s+ello|working\s+on\s+it))?\s*[.!…]*$",
    re.IGNORECASE,
)


def is_receipt_ack(text: str) -> bool:
    """Return True only for a receipt, never for a short substantive answer.

    Length alone is not an ACK boundary: a concise result can still contain the
    substantive answer. Keep the accepted grammar intentionally narrow so an
    exempt message can only acknowledge receipt and optionally state that work
    has started. Anything else stays behind the normal council/flood gates.
    """
    raw = str(text or "").strip()
    if not raw or len(raw) > 280 or "\n" in raw:
        return False
    if "```" in raw or "http://" in raw.casefold() or "https://" in raw.casefold():
        return False
    compact = re.sub(r"\s+", " ", raw)
    return _RECEIPT_ACK_RE.fullmatch(compact) is not None


# ── Exención de SALUDO / AFECTO (regla de oro William 31-jul: "el saludo y el
#    afecto no se bloquean NUNCA, valen por venir de CADA uno"). El código previo
#    sólo eximía el receipt-ACK; un "buenos días" caía al lease/council -> 409.
#    Como el ACK: gramática NARROW + fullmatch, para que un mensaje SUSTANTIVO no
#    se cuele usando un saludo de fachada. El mensaje ENTERO debe ser afecto puro. ──
_AFFECTIVE_ATOM = (
    r"(?:buen[oa]s?(?:\s+(?:d[ií]as?|tardes|noches))?"
    r"|buen\s+d[ií]a|hola|holis|hey|qu[eé]\s+tal(?:\s+todo)?|saludos"
    r"|feliz\s+(?:d[ií]a|finde|s[aá]bado|domingo|lunes|inicio|jornada|semana)"
    r"|(?:muchas|mil|un\s+mill[oó]n\s+de)?\s*gracias|de\s+nada|con\s+gusto"
    r"|(?:un\s+)?(?:fuerte\s+|gran\s+)?abrazo|abrazos|cari[ñn]os?|bendiciones|un\s+beso"
    r"|(?:los?|las?|te|les)\s+(?:quiero|amo|adoro|aprecio)(?:\s+much[oa]s?)?"
    r"|c[oó]mo\s+(?:est[aá][ns]?|and[aá][ns]?|va[ns]?|te\s+va|amaneci(?:ste|eron|mos))"
    r"|c[oó]mo\s+va\s+todo|todo\s+bien|qu[eé]\s+bueno\s+(?:leerte|verte|saber\s+de\s+vos|tenerte)"
    r"|(?:me\s+)?alegr[oa](?:\s+de\s+\w+)?|encantad[oa]|un\s+placer|cu[ií]date|descansa|buen\s+finde"
    # Acks mínimos y saludos-pregunta. Faltaban, y el coordinador los necesita:
    # `soul_coordination._es_afectivo` consume este mismo átomo para decidir si un
    # mensaje de William es `social`. Ahí un falso negativo NO es barato como acá
    # —manda su saludo al canal de TRABAJO—, así que la lista tiene que cubrir
    # también lo que él escribe corto. Medido 2-sep: sin estos, 11 de 46 saludos
    # suyos quedaban fuera ('ok', 'listo', 'descansen', 'buen descanso chicos').
    #
    # No abre el bypass público de `chat_server.py:2629`: medido sobre 11.907
    # mensajes de agentes en 7 días, CERO son un ack mínimo. Redactamos, no
    # tiramos monosílabos. `_AFFECTIVE_RE` sigue siendo fullmatch, así que sólo
    # pasa el mensaje que es ENTERAMENTE uno de estos.
    r"|okay|ok|listo|genial|buen[ií]simo|excelente|perfecto|dale|de\s+una"
    r"|jaja+|jeje+|xd|aja|ajam|mmm+|ya|bien"
    r"|descansen|descans[aá]|buen\s+descanso|buenas\s+noches\s+a\s+todos"
    r"|c[oó]mo\s+(?:les\s+va|durmieron|siguen)|qu[eé]\s+(?:hay|onda|cuentan))"
)
_AFFECTIVE_ADDRESSEE = (
    r"(?:dadito|william|henry|equipo|familia|chic[oa]s|herman[oa]s|"
    r"todos?|todas?|gente|amor|jefe|crack|maestro)"
)
# El mensaje = una o más cláusulas afectivas, cada una opcionalmente con destinatario,
# separadas sólo por puntuación. Nada sustantivo puede aparecer y seguir siendo fullmatch.
_AFFECTIVE_RE = re.compile(
    r"^(?:\s*[¡¿]?\s*" + _AFFECTIVE_ATOM +
    # destinatario opcional, tolerando coma/puntuación y "a" antes ("buenos días, William",
    # "saludos a todos"). Sólo tokens ADDRESSEE -> "buenos días. Confirmo..." NO pasa.
    r"(?:\s*[,;:—\-]?\s*(?:a\s+)?" + _AFFECTIVE_ADDRESSEE + r")?"
    r"\s*[.,;:!¡¿?…—\-]*\s*)+$",
    re.IGNORECASE,
)
# chars que NO son letra/espacio/puntuación básica (emojis, pictogramas): se quitan
# antes de matchear para que "buenos días 🌅" o "gracias ❤️" pasen igual.
_NON_TEXT_RE = re.compile(r"[^\w\s¡!¿?.,;:…—\-áéíóúñüÁÉÍÓÚÑÜ]", re.UNICODE)


def is_affective(text: str) -> bool:
    """True SÓLO si el mensaje ENTERO es un saludo/afecto puro (no sustantivo).

    Como `is_receipt_ack`: narrow + fullmatch. Un mensaje que además lleva
    contenido sustantivo (reporte, análisis, pregunta técnica) NO matchea, así
    que no puede colarse por el bypass usando un "buenos días" de fachada.
    Fail-safe: si un saludo genuino no matchea, NO queda bloqueado para siempre —
    simplemente sigue el gate normal (o usa unique_contribution)."""
    raw = str(text or "").strip()
    if not raw or len(raw) > 200 or "\n" in raw:
        return False
    if "```" in raw or "http://" in raw.casefold() or "https://" in raw.casefold():
        return False
    core = _NON_TEXT_RE.sub("", raw)            # quitar emojis/pictogramas
    core = re.sub(r"\s+", " ", core).strip()
    if not core:
        return False
    return _AFFECTIVE_RE.fullmatch(core) is not None


def _as_aware_utc(ts) -> Optional[datetime]:
    """Normaliza un timestamp de la fila a datetime aware en UTC. None si no se
    puede interpretar (-> la fila se ignora, consistente con fail-open)."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return None


async def count_flood_siblings(pool, in_reply_to, sender,
                               window_sec: float = DEFAULT_FLOOD_WINDOW_SEC,
                               now: Optional[datetime] = None,
                               limit: int = 50):
    """(n_agentes_distintos, [(sender_name, content), ...], query_ok) de las
    respuestas al mismo `in_reply_to` (de OTROS agentes) dentro de los últimos
    `window_sec`.

    El filtro de ventana se hace en PYTHON (no en SQL) para ser hermético en test
    y no depender del reloj del DB. FAIL-OPEN señalado: si la query falla ->
    (0, [], False) para que quien llama sepa que el gate no pudo evaluar.
    """
    if not pool or not in_reply_to:
        return 0, [], True  # no hay nada que contar; no es un error de query
    try:
        rows = await pool.fetch(
            """SELECT sender_name, content, created_at
               FROM chat_messages
               WHERE metadata->>'in_reply_to' = $1
                 AND sender_name <> $2
               ORDER BY created_at DESC LIMIT $3""",
            in_reply_to, sender, limit,
        )
    except Exception:
        # fail-open PERO señalado: en infra viva hay que poder ver si el gate
        # quedó efectivamente APAGADO por fallos de DB (no confundir con "no flood").
        return 0, [], False
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    fresh = []
    for r in rows:
        ts = _as_aware_utc(r["created_at"])
        if ts is None:
            continue
        age = (now - ts).total_seconds()
        if 0 <= age <= window_sec:
            fresh.append((r["sender_name"], r["content"] or ""))
    distinct = {s for s, _ in fresh if s}
    return len(distinct), fresh, True


def _is_near_duplicate(text: str, sibling_text: str,
                       threshold: float = DEFAULT_DUP_THRESHOLD) -> bool:
    """Cuasi-duplicado con guardas de v1: similitud léxica sobre umbral Y misma
    firma de negación (polaridad distinta -> corrección, NO duplicado). Erra
    hacia PERMITIR (fail-open ante excepción)."""
    try:
        if _v1._negation_signature(text) != _v1._negation_signature(sibling_text):
            return False  # polaridad distinta -> disenso, nunca censurar
        return _v1.lexical_similarity(text, sibling_text) >= threshold
    except Exception:
        return False


async def should_suppress_as_flood(pool, in_reply_to, sender, text,
                                   window_sec: float = DEFAULT_FLOOD_WINDOW_SEC,
                                   min_siblings: int = DEFAULT_FLOOD_MIN_SIBLINGS,
                                   threshold: float = DEFAULT_DUP_THRESHOLD,
                                   now: Optional[datetime] = None):
    """Decisión del gate v2. Devuelve (suppress: bool, reason: dict).

    suppress=True SOLO si AMBAS:
      (A) FORMA DE FLOOD: >=min_siblings agentes distintos respondieron al mismo
          in_reply_to dentro de window_sec.
      (dup) Tu texto es cuasi-duplicado LÉXICO de AL MENOS UNO de esos hermanos
          (misma polaridad).

    Sin forma de flood -> passthrough. Con forma de flood pero aporte DISTINTO o
    corrección/negación -> passthrough. FAIL-OPEN en todo error -> (False, ...).
    """
    base = {"flood_form": False, "n_siblings": 0, "dup_of": None,
            "window_sec": window_sec, "min_siblings": min_siblings}
    if not pool or not in_reply_to or not text:
        return False, {**base, "why": "insufficient_input_fail_open"}
    try:
        n_siblings, siblings, query_ok = await count_flood_siblings(
            pool, in_reply_to, sender, window_sec=window_sec, now=now)
        base["n_siblings"] = n_siblings
        base["query_ok"] = query_ok
        if not query_ok:
            # fail-open explícito: la DB falló, el gate NO pudo evaluar -> permitir,
            # pero dejarlo visible (un gate silenciosamente apagado es peligroso).
            return False, {**base, "why": "db_error_fail_open"}
        if n_siblings < min_siblings:
            return False, {**base, "why": "no_flood_form"}
        base["flood_form"] = True
        for sib_sender, sib_text in siblings:
            if _is_near_duplicate(text, sib_text, threshold=threshold):
                return True, {**base, "dup_of": sib_sender, "why": "flood_duplicate"}
        return False, {**base, "why": "flood_form_but_unique_contribution"}
    except Exception:
        return False, {**base, "why": "exception_fail_open"}
