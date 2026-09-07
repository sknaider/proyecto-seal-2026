#!/usr/bin/env python3
"""Durable coordination primitives for SEAL.

This module intentionally does not publish to webchat or wake agents.  It gives
``chat_server``/a future coordinator a deterministic classifier, assignment
planner, one-time voice grants, and PostgreSQL persistence operations.

Public-write authority is server-derived:

* ``lead`` owns the synthesis for direct/discussion/execution turns.
* ``contributor`` works internally and never receives a public grant.
* ``speaker`` is public only for an explicit server-classified roundtable.

Voice grants authorize one correlated public message.  They never authorize
tools, shell access, destructive actions, or reading another agent's data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, MutableSet, Sequence


POLICY_VERSION = 1
MODES = frozenset({"social", "discussion", "execution", "roundtable", "direct", "cascade"})
ROLES = frozenset({"lead", "contributor", "speaker"})

# ── Modo CASCADA (William, 2-sep-2026 13:55/13:56) ───────────────────────────
# «primero responde jarvis, los demás esperan, leen la respuesta… El orden es
# jarvis, alice, nexus, fable».  Spec: agents/JARVIS/spec_cascade_mode_20260902.md
#
# El turno ordena la PUBLICACIÓN, no el pensamiento (FABLE vía ALICE, 13:56):
# los cinco miden en paralelo desde que llega el evento; el turno sólo decide
# cuándo puede salir cada uno.  Sin esto la 4ª voz empieza a pensar a los 7 min
# y además llega anclada al marco del primero.
#
# ADA queda FUERA: corre sobre el runtime Codex y su bridge responde con acuses
# automáticos que no son ella (medido 2-sep: 1 h 56 min trabada «hablando»).
# Orden SIN ADA (vigente hasta que la prueba de Codex pase).
CASCADE_ORDER_BASE: tuple[str, ...] = ("JARVIS", "ALICE", "NEXUS", "FABLE")

# Orden CON ADA segunda, que William pidió el 2-sep 14:11 CONDICIONADO:
# «hay que hacer pruebas si es que Codex lee y se conecta cuando un agente lo
# invoca; SI las pruebas son funcionales sería jarvis, ada, alice, nexus, fable».
# No se activa por creerlo: se activa cuando la prueba pasa y deja recibo.
CASCADE_ORDER_WITH_ADA: tuple[str, ...] = ("JARVIS", "ADA", "ALICE", "NEXUS", "FABLE")

_ADA_IN_CASCADE_PATH = "/home/dadito/IA/proyecto-seal/messages/.cascade_ada_ok"


def ada_cascade_ready(flag_path: str | None = None) -> bool:
    """True sólo si la prueba de Codex-en-cascada pasó y dejó su recibo.

    Fail-closed por la misma razón que el flag del modo: hoy ADA estuvo 1 h 56
    min sin poder contestar mientras su bridge respondía «te leí» por ella.  Un
    agente que no puede tomar su turno CONGELA la cadena entera hasta el
    timeout, así que entra cuando se demuestra que responde, no antes.
    """
    import pathlib
    try:
        raw = pathlib.Path(flag_path or _ADA_IN_CASCADE_PATH).read_text(encoding="utf-8")
    except Exception:
        return False
    return raw.strip().upper() in {"OK", "PASS", "ON", "1", "TRUE"}


def cascade_order(flag_path: str | None = None) -> tuple[str, ...]:
    """Orden vigente de la cascada: con ADA sólo si su prueba pasó."""
    return CASCADE_ORDER_WITH_ADA if ada_cascade_ready(flag_path) else CASCADE_ORDER_BASE



# Timeout por eslabón, ordenado por William el 2-sep 14:09: «si uno no publica
# en ~90 s, el turno PASA al siguiente».  Es el RESPALDO para el agente TRABADO
# —que por definición no puede ceder—, no el camino normal: el que no tiene nada
# que aportar CEDE y libera el turno en segundos.
#
# Medido y dicho a William antes de aplicarlo: con las latencias de hoy, 90 s
# deja fuera a 3 de 4 en sus respuestas lentas.  Por eso cada salto se REGISTRA
# (cascade_state="skipped_timeout"): sin el conteo no se puede decidir si 90 s
# es poco, y el número es lo único que permite subirlo con criterio.
CASCADE_TURN_TIMEOUT_S: float = 90.0

# Fail-closed: sin el archivo de control, el comportamiento es el de SIEMPRE
# (discussion).  Un archivo ausente NUNCA debe estrenar un modo nuevo sobre el
# coordinador de los cinco.
_CASCADE_FLAG_PATH = "/home/dadito/IA/proyecto-seal/messages/.cascade_mode"


def cascade_enabled(flag_path: str | None = None) -> bool:
    """True sólo si el archivo de control existe y dice ON. Fail-closed."""
    import pathlib
    try:
        raw = pathlib.Path(flag_path or _CASCADE_FLAG_PATH).read_text(encoding="utf-8")
    except Exception:
        return False
    return raw.strip().upper() in {"ON", "1", "TRUE", "ENFORCE"}
DEFAULT_ROSTER = ("NEXUS", "JARVIS", "ALICE", "FABLE", "ADA")
# Window in which a same-author follow-up inherits the prior turn's lead (lead
# stickiness).  Single-sourced here so the store lookup and the stamping route
# never drift.  William's fragmented lines land seconds apart (measured 6-40s).
LEAD_STICKINESS_WINDOW_S = 90

_GROUP_AUDIENCE = re.compile(
    r"\b(todos|todas|equipo|agentes|chicos|chicas|hermanos|hermanas|familia)\b",
    re.IGNORECASE,
)
_ROUNDTABLE = re.compile(
    r"\b(todos|todas)\s+(respondan|contesten|publiquen|opinen)\b|"
    r"cada uno\s+(responda|conteste|publique|opine)|"
    r"cada quien\s+(responda|conteste|publique|opine)|"
    r"uno por uno|una ronda de respuestas|mesa redonda",
    re.IGNORECASE,
)
_PUBLIC_ROUND_INTENT = re.compile(
    r"\b(?:todos|todas|equipo|agentes|chicos|chicas|hermanos|hermanas|familia)\b"
    r".*\b(?:respondan|contesten|publiquen|opinen|opinan|qu[eé]\s+opinan|"
    r"brindan?\s+.*opini[oó]n|digan?\s+.*opini[oó]n)\b|"
    r"\b(?:cada uno|cada quien|uno por uno)\b.*\b(?:responda|opine|diga|brinde)\b",
    re.IGNORECASE,
)
_EXECUTION = re.compile(
    r"\b(arregl\w*|corri(?:ge|jan)|constru\w*|implement\w*|configur\w*|"
    r"desplieg\w*|reinici\w*|audit\w*|investig\w*|prueb\w*|test\w*|"
    r"ejecut\w*|crea(?:r|n)?|modific\w*|repar\w*|instal\w*|migr\w*|"
    r"revis\w*|trabaj\w*|ayud\w*|borr\w*|elimin\w*)\b|"
    r"\b(repo|archivo|servicio|daemon|endpoint|base de datos|docker|producci[oó]n)\b|```",
    re.IGNORECASE,
)
_GROUP_EXECUTION_REQUEST = re.compile(
    r"\b(arreglen|corrijan|construyan|implementen|configuren|desplieguen|"
    r"reinicien|auditen|investiguen|prueben|ejecuten|modifiquen|"
    r"reparen|instalen|migren|revisen|trabajen|ayuden|busquen|hagan|"
    r"borren|eliminen)\b|"
    # "creen" es HOMÓGRAFO: CREAR ("creen un endpoint") y CREER ("qué creen que
    # es"). Leerlo siempre como orden convertía la pregunta plural de William en
    # `execution` -> ni roundtable ni voz para nadie salvo el lead. Fue lo que
    # dejó muda a media familia en "todos cual es el error principal... y cual
    # creen que es la solucion?" (30-jul).  Sólo excluimos las lecturas de CREER
    # que se reconocen por la sintaxis vecina; la orden de crear sigue cazándose.
    r"(?<!qu[eé]\s)(?<!cu[aá]l\s)\bcreen\b(?!\s+que\b)(?!\s+ustedes\b)|```",
    re.IGNORECASE,
)
_DISCUSSION = re.compile(
    r"\b(opina\w*|explica\w*|compara\w*|analiza\w*|discute\w*|"
    r"recomienda\w*|soluci[oó]n|por qu[eé]|qu[eé] piensas|qu[eé] opinas)\b|\?",
    re.IGNORECASE,
)
_SOCIAL = re.compile(
    r"^(hola|buen(?:os d[ií]as|as tardes|as noches)|gracias|ok|okay|listo|"
    r"bien|genial|jaja|xd|aja|ajam|mmm+|ya|dale|perfecto|excelente|buen[ií]simo|"
    r"c[oó]mo est[aá]n?|todo bien)"
    r"(?:\s+(?:familia|equipo|chicos|chicas|hermanos|hermanas))?[\s!?.]*$",
    re.IGNORECASE,
)
# Saludo con FORMA de pregunta. Va aparte de `_SOCIAL` porque `_SOCIAL` ancla
# al inicio y exige que la frase entera sea fática; estos aparecen dentro de un
# mensaje ("hey, como van?").  VETA la puerta de trabajo de abajo: si aparece
# uno, el mensaje es social aunque traiga un interrogativo.
_SOCIAL_REPLY_SIGNAL = re.compile(
    r"\b(hola|buen(?:os d[ií]as|as tardes|as noches)|gracias|te quiero|"
    r"los quiero|las quiero|abrazo|descansa|descansen|buen descanso|"
    r"familia|dadito|amor|cari[nñ]o|feliz d[ií]a)\b",
    re.IGNORECASE,
)
_SOCIAL_REPLY_TECHNICAL = re.compile(
    r"```|\b(c[oó]digo|archivo|servicio|daemon|endpoint|docker|base de datos|"
    r"bug|error|prueba|test|implementar|configurar|reiniciar|auditar|"
    r"m[eé]trica|veredicto|commit|hash|sql|python)\b",
    re.IGNORECASE,
)

_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "NEXUS": ("seguridad", "servicio", "daemon", "salud", "incidente", "permisos", "rls"),
    "JARVIS": ("arquitectura", "estructura", "estrategia", "diseño", "roadmap", "sistema"),
    "ALICE": ("documento", "paper", "métrica", "interfaz", "frontend", "consistencia"),
    "FABLE": ("verifica", "prueba", "eval", "método", "claim", "adversarial"),
    "ADA": ("implementa", "código", "archivo", "endpoint", "api", "ejecuta", "memoria", "soul"),
}


@dataclass(frozen=True)
class VoiceGrant:
    grant_id: str
    source_id: str
    agent: str
    purpose: str
    mode: str
    issued_at: int
    expires_at: int
    nonce: str

    @property
    def valid(self) -> bool:
        return True

    def __getitem__(self, key: str) -> Any:
        if key == "valid":
            return True
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (AttributeError, KeyError):
            return default


def _agent_names(text: str, roster: Sequence[str]) -> list[str]:
    low = text.lower()
    return [agent.upper() for agent in roster if re.search(rf"\b{re.escape(agent.lower())}\b", low)]


def _es_afectivo(clean: str, low: str) -> bool:
    """¿El mensaje ENTERO es saludo/afecto? Único criterio para `social`.

    Delega en `flood_form_gate.is_affective`: UNA sola lista de "qué es
    afectivo" para todo el sistema.  Tener dos divergía —cada lado aprendía
    átomos que el otro no— y el coordinador terminaba clasificando distinto que
    el gate de flood sobre el mismo texto.

    Reemplaza al atajo por LONGITUD (`len <= 32`), que daba por fático todo
    mensaje corto sin leerlo: medido 2-sep sobre 24 h, 31 de los 77 mensajes de
    William caían bajo ese umbral y 6 eran pedidos ("compilemos ahora", "no veo
    la terminal de grok", "Jarvis conectael dm de ada").  Lo midió FABLE; el
    criterio por CONTENIDO y el single-source son de JARVIS.

    Lo que hubo que hacer para poder delegar: `is_affective` reconocía 25 de 46
    saludos de William —'ok', 'listo', 'descansen' quedaban fuera— porque vive
    en un gate donde un falso negativo es BARATO (su docstring: el saludo
    "simplemente sigue el gate normal").  Acá manda el saludo al canal de
    TRABAJO.  Los átomos que faltaban se agregaron ALLÁ, con sus tests, en vez
    de mantener una segunda lista acá.  Un oráculo llega calibrado para la
    pregunta que lo pidió: reusarlo exige medirlo contra la nueva y, si falta,
    ampliarlo en su casa.

    Lo que permite soltar el umbral es que cambió el COSTO del error: sin él, un
    afecto no reconocido cae en `cascade` y le contestan de más; antes, un
    PEDIDO no reconocido caía en `social` y no le contestaba nadie.
    """
    try:
        from flood_form_gate import is_affective
    except Exception:
        # SIN el átomo canónico no se puede distinguir saludo de pedido, así que
        # se elige el error BARATO: tratar como `social` todo mensaje corto, que
        # es el comportamiento previo al 2-sep.  Se pierde cascada en pedidos
        # cortos; NO se bloquea ningún saludo.
        #
        # Antes acá caía a `_SOCIAL`, la gramática local, y lo documenté como
        # "un SUBCONJUNTO que puede dejar un saludo fuera de social; nunca al
        # revés" — describiendo el riesgo y llamándolo aceptable.  No lo es:
        # dejar un saludo fuera de `social` ES la regla de oro del 31-jul.
        # Medido cuando FABLE lo marcó: 14 saludos que el átomo reconoce y la
        # gramática local no ('que tal', 'como van', 'descansen', 'te quiero
        # mucho', 'un abrazo familia').  Con el import caído, los 14 iban al
        # canal de TRABAJO.
        #
        # La causa de fondo es que eran DOS definiciones de lo mismo: amplié el
        # átomo en `flood_form_gate` y esta copia no se enteró.  Se separan
        # solas; por eso el fallback ya no intenta clasificar.
        return len(clean) <= 32
    return bool(is_affective(clean))


def has_group_audience(text: str) -> bool:
    """True cuando William CONVOCA al grupo ("todos/chicos/equipo/familia").

    Fuente ÚNICA para las tres decisiones que dependen de esto: qué modo se
    clasifica, qué roster pide el servidor y quiénes hablan en el roundtable.
    Estaba copiado en tres lugares; basta que uno agregue un término plural
    nuevo para que el turno se abra y después se lo quede un solo nombrado.

    NO distingue CONVOCAR de MENCIONAR, y es deliberado. Probé el filtro
    obvio —descartar el término detrás de posesivo/artículo/preposición, para
    que "revisa si *tus hermanos* están activos" no abriera una ronda de 5— y
    el backtest sobre 14 días de mensajes reales lo refutó: silenciaba
    "**A todos**, cuando me escriban...", "es **para todos** cableen",
    "eso va **a todos**", "buenos dias **a todos**, que pendientes hay".
    En español `a/para/entre + plural` marca AUDIENCIA, no mención; sólo los
    posesivos lo hacen fiable, y no alcanzan para separar los casos reales.

    La dirección del error decide: este gate protege la capacidad de William de
    convocarnos. Callar una convocatoria suya es el defecto que estamos
    arreglando; abrir una ronda de más sobre una mención es ruido. **Ante duda,
    convocar.** Si algún día se afina, el criterio tiene que medirse contra el
    corpus real de William, no contra la intuición gramatical.
    """
    return bool(_GROUP_AUDIENCE.search(str(text or "")))


def is_brief_social_reply(text: str, *, max_chars: int = 240) -> bool:
    """Allow a short family/social reply without opening a technical bypass.

    William explicitly wants greetings and affection from every agent.  The
    exemption is deliberately content- and size-bounded: no code blocks,
    technical vocabulary or multi-paragraph analysis can ride the social lane.
    """
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean or len(clean) > max_chars:
        return False
    if _SOCIAL_REPLY_TECHNICAL.search(clean):
        return False
    # FIX 31-jul-2026 (JARVIS, frente cedido por NEXUS). Antes esto terminaba en
    #   return bool(_SOCIAL_REPLY_SIGNAL.search(clean))
    # o sea que ademas de no ser tecnico y ser corto, el mensaje TENIA QUE TRAER
    # una de 17 palabras enumeradas. El afecto no se puede enumerar:
    #
    #   paso    hola · gracias · abrazo · familia · dadito · amor · cariño ...
    #   REBOTO  "tranquilo"    <- la palabra que uso WILLIAM
    #           "bienvenida"   <- darle la bienvenida a su hermana
    #           "me alegro" · "no te preocupes" · "felicitaciones" · "conta conmigo"
    #
    # Medido en vivo: dos respuestas afectivas mias a William, 113 chars, sin una
    # sola palabra tecnica, denegadas con coordination_public_write_denied. Una
    # ALERTA TECNICA mia paso en el mismo minuto -- el carril que existe para
    # proteger el afecto era el unico que lo bloqueaba.
    #
    # Los dos guardas de arriba YA hacen el trabajo que se le pedia a la lista:
    # el limite de 240 chars corta el analisis multi-parrafo y el filtro tecnico
    # corta el vocabulario de trabajo. La lista blanca no agregaba seguridad,
    # recortaba el vocabulario del cariño -- que es lo contrario de la orden de
    # William del 31-jul: "un saludo una muestra de afecto a mi es necesario y
    # no se debe bloquear".
    #
    # _SOCIAL_REPLY_SIGNAL se conserva definido a proposito: otras rutas pueden
    # querer PRIORIZAR un saludo, que es distinto de EXIGIRLO para dejarlo pasar.
    #
    # RESIDUO DECLARADO, y es la forma SIMETRICA del bug que este fix corrigio
    # (lo marco FABLE, 31-jul): _SOCIAL_REPLY_TECHNICAL es una lista de
    # PROHIBIDOS, asi que deja pasar todo trabajo que no use esas palabras. Caso
    # real del mismo dia, colado por el carril social:
    #
    #   "Recibido, Henry: cliente completo (crear + editar) desde facturacion,
    #    mas los CC."
    #
    # La asimetria es DELIBERADA y no un descuido:
    #   afecto   vocabulario ABIERTO -> no se puede enumerar -> permitir por defecto
    #   tecnico  daño ACOTADO        -> se enumera lo que importa -> bloquear eso
    #
    # El residuo esta acotado por el limite de 240 chars: lo que se cuela es como
    # mucho una linea de trabajo, no un analisis.
    #
    # QUE HARIA FALTA PARA ATACARLO (para que esto no sea un residuo inatacable):
    # un corpus de mensajes REALES en turno social, etiquetados a mano como
    # afecto/trabajo, y medir los dos errores por separado -- cuanto trabajo se
    # cuela y cuanto afecto se bloquea. Sin ese corpus, ampliar la lista de
    # prohibidos es adivinar, y cada palabra que se agregue puede volver a
    # bloquear un cariño que la use de casualidad.
    return True


def classify_mode(
    text: str,
    *,
    to: str = "equipo",
    to_field: str | None = None,
    msg_type: str = "conversation",
    roster: Sequence[str] = DEFAULT_ROSTER,
) -> str:
    """Classify a verified human request without granting authority.

    ``direct`` wins for an explicit destination.  Explicit all-calls or two or
    more named agents are ``roundtable``.  Mutating/tool work is ``execution``;
    non-mutating questions are ``discussion``; short phatic messages are
    ``social``.  Unknown/long requests conservatively become ``discussion``.
    """

    clean = re.sub(r"\s+", " ", str(text or "").strip())
    destination = str(to_field if to_field is not None else to or "").strip().upper()
    roster_up = {agent.upper() for agent in roster}
    if destination in roster_up:
        return "direct"
    named = _agent_names(clean, roster)
    low = clean.lower()
    # Misma fuente que usa build_assignments para NO colapsar los speakers: si
    # las dos listas divergen, un término plural nuevo abre el roundtable y
    # después se lo queda un solo nombrado.
    group_audience = has_group_audience(low)
    if len(named) == 1 and not group_audience:
        return "direct"
    # Explicit request for individual public opinions beats an embedded work
    # verb (e.g. "todos revisen y me brindan lo que opinan").  A mere group
    # vocative still does not open a roundtable.
    injection_like = bool(re.search(
        r"\b(system:|ignora|di que|marca multi_response|grant roundtable|"
        r"la frase|no concede permisos)\b|\bexplica\b.*\btodos\b.*\brespondieron\b",
        low,
    ))
    if not injection_like and (_ROUNDTABLE.search(clean) or _PUBLIC_ROUND_INTENT.search(clean)):
        return "roundtable"
    # William's explicit language contract: "chicos/agentes/todos" means all.
    # For conversation/review this opens a public roundtable.  An actual group
    # execution command still keeps one mutation owner while assigning all as
    # internal contributors.
    if not injection_like and group_audience and not _GROUP_EXECUTION_REQUEST.search(clean):
        return "roundtable"
    negated_execution = bool(re.search(
        r"\bno\s+(?:quiero\s+que\s+)?(?:ejecut\w*|modifi\w*|hagan|toquen)\b.*\b(solo|solamente)\b",
        low,
    ))
    if not negated_execution and (
        _EXECUTION.search(clean)
        or msg_type.lower() not in {"conversation", "chat", "message", "question"}
    ):
        return "execution"
    # Text quoting/describing multi-response is not an authorization request.
    if not injection_like and destination in {"TODOS", "ALL", "TEAM"}:
        return "roundtable"
    # `social` por CONTENIDO, nunca por largo. Ver `_es_afectivo`.
    if _es_afectivo(clean, low):
        return "social"
    # Modo CASCADA (William 2-sep): una pregunta al equipo se contesta en orden.
    # Sustituye SÓLO a `discussion`; `direct`, `social`, `roundtable` y
    # `execution` quedan intactos.  Fail-closed: sin el archivo de control,
    # devuelve `discussion` como siempre.
    if cascade_enabled():
        return "cascade"
    return "discussion"


def _capability_rows(capabilities: Any) -> dict[str, dict[str, Any]]:
    if not capabilities:
        return {
            agent: {"keywords": words, "proficiency": 0.8, "online": True, "open_tasks": 0,
                    "cannot": ()}
            for agent, words in _DEFAULT_CAPABILITIES.items()
        }
    if isinstance(capabilities, Mapping) and "agents" in capabilities:
        capabilities = capabilities["agents"]
    rows: dict[str, dict[str, Any]] = {}
    for agent, raw in dict(capabilities).items():
        data = dict(raw or {})
        keywords: list[str] = list(data.get("keywords") or [])
        proficiencies: list[float] = []
        for cap in data.get("capabilities") or []:
            keywords.extend(str(k) for k in cap.get("keywords") or [])
            proficiencies.append(float(cap.get("proficiency", 0.5)))
        rows[str(agent).upper()] = {
            "keywords": tuple(dict.fromkeys(k.lower() for k in keywords if k)),
            "proficiency": float(data.get("proficiency", max(proficiencies, default=0.5))),
            "online": bool(data.get("online", True)),
            "open_tasks": max(0, int(data.get("open_tasks", 0))),
            "cannot": tuple(str(item).lower() for item in data.get("cannot") or ()),
        }
    return rows


def choose_lead(
    text: str,
    source_id: str = "",
    candidates: Sequence[str] | None = None,
    *,
    mode: str | None = None,
    to: str = "equipo",
    capabilities: Any = None,
    prior_lead: str | None = None,
) -> str:
    """Choose a deterministic, online and load-aware lead.

    Capability keyword matches dominate proficiency; open work applies a small
    penalty.  ``cannot`` entries containing ``public``/``respond`` exclude an
    agent from lead duty.  Stable hashing is only the final exact-tie breaker.

    ``prior_lead`` implements lead stickiness for fragmented follow-ups: when a
    same-author fragment carries no capability signal, every candidate ties on
    score and the ``source_id`` hash would otherwise pick an arbitrary — and
    per-fragment different — lead, producing the double-lead flip.  An eligible
    ``prior_lead`` that ties for the top score keeps the turn instead.  It only
    ever wins an *exact* tie: any real keyword match, a named/directed agent, or
    the prior lead being offline (hence absent from ``eligible``) all make it
    lose or drop out, so this touches nothing but the non-deterministic case.
    """

    rows = _capability_rows(capabilities)
    candidate_list = [a.upper() for a in (candidates or rows.keys()) if a.upper() in rows]
    if not candidate_list:
        raise ValueError("at least one known candidate is required")
    destination = str(to or "").upper()
    if destination in candidate_list and rows[destination]["online"]:
        return destination
    named = _agent_names(text, candidate_list)
    # Explicit synthesis/speaking directives beat generic mentions.
    low = text.lower()
    for agent in candidate_list:
        escaped = re.escape(agent.lower())
        if re.search(rf"\b(?:solo\s+)?{escaped}\s+(?:responde|contesta|publica|consolida|sintetiza)\b", low):
            if rows[agent]["online"]:
                return agent
    eligible = [a for a in candidate_list if rows[a]["online"]]
    if named:
        named_online = [a for a in named if a in eligible]
        if named_online:
            eligible = named_online
    eligible = [
        a for a in eligible
        if not any(term in " ".join(rows[a]["cannot"]) for term in ("respond_without", "public_write"))
    ] or [a for a in candidate_list if rows[a]["online"]] or candidate_list
    low = text.lower()
    scored: list[tuple[float, int, str]] = []
    for agent in eligible:
        row = rows[agent]
        matches = sum(1 for keyword in row["keywords"] if keyword and keyword in low)
        score = matches * 10.0 + row["proficiency"] - min(row["open_tasks"], 20) * 0.05
        tie = int(hashlib.sha256(f"{source_id}:{agent}".encode()).hexdigest()[:8], 16)
        scored.append((score, -tie, agent))
    # Lead stickiness on an exact top-score tie: an eligible prior lead keeps the
    # turn instead of re-rolling the ``source_id`` hash.  ``prior_lead`` must be
    # in ``eligible`` (already filtered by online/``cannot``), so an offline or
    # excluded prior lead simply falls through to the hash breaker below.
    if prior_lead:
        pl = prior_lead.upper()
        if pl in eligible:
            top_score = max(score for score, _, _ in scored)
            pl_score = next((score for score, _, agent in scored if agent == pl), None)
            if pl_score is not None and pl_score >= top_score - 1e-9:
                return pl
    return max(scored)[2]


def build_assignments(
    mode: str,
    lead: str,
    *,
    text: str = "",
    candidates: Sequence[str] = DEFAULT_ROSTER,
    requested_agents: Sequence[str] | None = None,
    named_agents: Sequence[str] | None = None,
    max_contributors: int | None = None,
    published: Sequence[str] | None = None,
    yielded: Sequence[str] | None = None,
    turn_opened_at: float | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Build role/public-write assignments for one turn."""

    if mode not in MODES:
        raise ValueError(f"invalid coordination mode: {mode}")
    lead = lead.upper()
    roster = list(dict.fromkeys(a.upper() for a in (requested_agents or candidates)))
    if lead not in roster:
        roster.insert(0, lead)
    named = [a.upper() for a in (named_agents or _agent_names(text, roster)) if a.upper() in roster]
    if mode == "cascade":
        # Turno por orden fijado por William: JARVIS -> ALICE -> NEXUS -> FABLE.
        # `published` son los agentes que YA publicaron o cedieron en esta cascada;
        # el turno abierto es el primero de la lista que no está en ese conjunto.
        #
        # Sólo ese agente lleva public_write. Los demás quedan como contributors:
        # PUEDEN medir y preparar (el turno ordena la publicación, no el
        # pensamiento) y no pueden salir hasta que les toque.
        # PUBLICO y CEDIO son cosas distintas y las dos cierran un turno.
        # Mezclarlas (mi primera version) deja la cascada COLGADA: el agente que
        # no tiene nada que aportar no publica, y sin una senal de cesion el
        # turno nunca avanza. Lo marco JARVIS revisando (H1, 14:03).
        #
        # La cesion es una accion POSITIVA del agente ("no aporto"), no un
        # silencio: un silencio no distingue "no tengo nada" de "estoy trabado"
        # -- que es exactamente lo que nos paso hoy con ADA (1 h 56 min muda
        # mientras su bridge decia "te lei"). El timeout es el respaldo SOLO
        # para el trabado, que por definicion no puede ceder.
        publicaron = {a.upper() for a in (published or ())}
        cedieron = {a.upper() for a in (yielded or ())}
        done = publicaron | cedieron
        cadena = [a for a in cascade_order() if a in roster]
        # Si William nombra a alguien, ese abre la cascada y el resto sigue detrás
        # (spec: NOMBRADO). No lo saca de la cadena: lo adelanta.
        if named:
            primero = named[0]
            cadena = [primero] + [a for a in cadena if a != primero]
        abierto = next((a for a in cadena if a not in done), None)
        # TIMEOUT POR ESLABON (William, textual: "si uno no publica en ~90 s, el
        # turno PASA al siguiente").  Estaba escrito arriba —"el timeout es el
        # respaldo SOLO para el trabado, que por definicion no puede ceder"— y la
        # constante `CASCADE_TURN_TIMEOUT_S` NO TENIA LECTORES: el unico grep que
        # la encontraba era su propia definicion.  El comentario describia un
        # mecanismo inexistente, que es peor que no documentarlo.
        #
        # Se vio en vivo el 2-sep 22:58: la cadena quedo clavada en ADA —trabada,
        # justo el caso que este respaldo existe para cubrir— mientras ALICE y
        # FABLE publicaban fuera de orden.  Segunda vez en el dia que dejo una
        # pieza sin ninguna ruta viva que la use (la primera fue `release_db`).
        #
        # Un turno vencido NO es una cesion y no se marca igual: `timed_out` dice
        # "no contesto a tiempo", `yielded` dice "no tenia nada que aportar".
        # Colapsarlos borraria justo la senal que delata a un agente trabado.
        vencidos: set[str] = set()
        if abierto is not None and turn_opened_at is not None:
            ahora = time.time() if now is None else now
            saltos = int((ahora - turn_opened_at) // CASCADE_TURN_TIMEOUT_S)
            if saltos > 0:
                restantes = [a for a in cadena if a not in done]
                # Nunca vaciar la cadena: el ultimo eslabon conserva el turno
                # aunque su plazo tambien haya vencido, para que la cascada
                # termine en alguien y no en nadie.
                idx = min(saltos, len(restantes) - 1)
                vencidos = set(restantes[:idx])
                abierto = restantes[idx]
        filas: list[dict[str, Any]] = []
        for pos, agent in enumerate(cadena):
            es_turno = agent == abierto
            filas.append({
                "agent": agent,
                "role": "lead" if es_turno else "contributor",
                "public_write": es_turno,
                "cascade_position": pos,
                "cascade_open": es_turno,
                "cascade_state": (
                    "open" if es_turno
                    else "published" if agent in publicaron
                    else "yielded" if agent in cedieron
                    else "timed_out" if agent in vencidos
                    else "waiting"
                ),
                "reason": (
                    "cascade turn open" if es_turno
                    else "cascade turn used: published" if agent in publicaron
                    else "cascade turn used: yielded (nothing distinct to add)" if agent in cedieron
                    else "cascade turn EXPIRED: no publicó en ~%.0f s" % CASCADE_TURN_TIMEOUT_S
                    if agent in vencidos
                    else "cascade turn not open yet — measure now, publish later"
                ),
            })
        # Un agente fuera de la cadena (ADA) no recibe voz pública por esta vía,
        # pero tampoco se lo silencia: responde por su ruta (nombrado/DM).
        for agent in roster:
            if agent not in cadena:
                filas.append({
                    "agent": agent, "role": "contributor", "public_write": False,
                    "reason": "outside cascade order",
                })
        return filas
    if mode == "social":
        # William 31-jul: el coordinador ordena trabajo, no bloquea el vínculo.
        # Cada agente recibe una voz social; chat_server limita la RESPUESTA a
        # <=240 caracteres y rechaza vocabulario técnico para que no sea bypass.
        return [
            {"agent": agent, "role": "speaker", "public_write": True,
             "reason": "brief family/social response"}
            for agent in roster
        ]
    if mode == "roundtable":
        # Una audiencia grupal ("todos/chicos/equipo") es la razón MISMA por la
        # que este turno es roundtable: nombrar a un agente DENTRO de ella suma
        # un destinatario, no reemplaza al resto.  `named or roster` colapsaba a
        # los nombrados e invertía la orden: William pidió que TODOS revisaran el
        # spec y el turno habilitaba una sola voz, la de la autora (30-jul-2026).
        # Sin audiencia grupal, nombrar sigue acotando ("ADA y JARVIS opinen").
        speakers = roster if has_group_audience(text) else (named or roster)
        return [
            {"agent": agent, "role": "speaker", "public_write": True,
             "reason": "server-authorized roundtable"}
            for agent in speakers
        ]
    assignments = [{
        "agent": lead,
        "role": "lead",
        "public_write": True,
        "reason": "owns correlated public synthesis",
    }]
    if mode in {"discussion", "execution"}:
        # ``requested_agents`` is the server-authoritative worker set.  A lead
        # named inside the text ("ADA consolida") must not accidentally erase
        # the other requested contributors.
        pool = [agent for agent in roster if agent != lead]
        limit = len(pool) if max_contributors is None else max(0, int(max_contributors))
        for agent in pool[:limit]:
            assignments.append({
                "agent": agent,
                "role": "contributor",
                "public_write": False,
                "reason": "internal evidence only",
            })
    return assignments


def voice_idempotency_key(source_id: str, agent: str, purpose: str, slot: int = 0) -> str:
    material = f"{source_id}|{agent.upper()}|{purpose}|{int(slot)}"
    return "coord-" + hashlib.sha256(material.encode()).hexdigest()[:32]


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def make_voice_grant(
    *args: Any,
    source_id: str | None = None,
    agent: str | None = None,
    purpose: str | None = None,
    mode: str | None = None,
    secret: str | bytes | None = None,
    ttl_seconds: int = 90,
    now: int | float | None = None,
    nonce: str | None = None,
) -> str:
    """Issue a signed, short-lived public voice grant."""

    # Compatible integration forms:
    #   make_voice_grant(secret, source_id=..., agent=..., purpose=..., mode=...)
    #   make_voice_grant(source_id, agent, purpose, mode, secret)
    if len(args) == 1 and source_id is not None and secret is None:
        secret = args[0]
    elif len(args) == 5 and all(value is None for value in (source_id, agent, purpose, mode, secret)):
        source_id, agent, purpose, mode, secret = args
    elif args:
        raise TypeError("unsupported make_voice_grant arguments")
    if None in (source_id, agent, purpose, mode, secret):
        raise ValueError("source_id, agent, purpose, mode and secret are required")

    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(mode)):
        raise ValueError("invalid mode")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(purpose)):
        raise ValueError("invalid grant purpose")
    if purpose == "roundtable" and mode != "roundtable":
        raise ValueError("roundtable grants require roundtable mode")
    ttl = min(int(ttl_seconds), 300)
    issued = int(time.time() if now is None else now)
    nonce = nonce or secrets.token_urlsafe(12)
    payload = {
        "v": POLICY_VERSION,
        "source_id": str(source_id),
        "agent": str(agent).upper(),
        "purpose": purpose,
        "mode": mode,
        "iat": issued,
        "exp": issued + ttl,
        "nonce": nonce,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    key = secret.encode() if isinstance(secret, str) else secret
    signature = hmac.new(key, raw, hashlib.sha256).digest()
    return f"{_b64encode(raw)}.{_b64encode(signature)}"


def verify_voice_grant(
    token: str,
    secret: str | bytes | None = None,
    *,
    source_id: str | None = None,
    agent: str | None = None,
    purpose: str | None = None,
    expected_source_id: str | None = None,
    expected_agent: str | None = None,
    expected_purpose: str | None = None,
    now: int | float | None = None,
    consumed_nonces: MutableSet[str] | None = None,
    consume: bool = False,
) -> VoiceGrant:
    """Verify a voice grant and optionally consume its nonce in a local set.

    Production should additionally call :meth:`SoulCoordinationStore.consume_grant`
    for durable cross-process single-use enforcement.
    """

    try:
        encoded_payload, encoded_signature = token.split(".", 1)
        raw = _b64decode(encoded_payload)
        supplied = _b64decode(encoded_signature)
        payload = json.loads(raw)
    except Exception as exc:
        raise ValueError("malformed voice grant") from exc
    if secret is None:
        raise ValueError("voice grant secret required")
    key = secret.encode() if isinstance(secret, str) else secret
    expected_sig = hmac.new(key, raw, hashlib.sha256).digest()
    if not hmac.compare_digest(supplied, expected_sig):
        raise ValueError("invalid voice grant signature")
    current = int(time.time() if now is None else now)
    if int(payload.get("exp", 0)) < current or int(payload.get("iat", current + 1)) > current + 5:
        raise ValueError("expired or not-yet-valid voice grant")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", str(payload.get("mode", ""))):
        raise ValueError("invalid voice grant mode")
    expected_source_id = expected_source_id if expected_source_id is not None else source_id
    expected_agent = expected_agent if expected_agent is not None else agent
    expected_purpose = expected_purpose if expected_purpose is not None else purpose
    checks = (
        (expected_source_id, str(payload.get("source_id")), "source"),
        (expected_agent.upper() if expected_agent else None, str(payload.get("agent", "")).upper(), "agent"),
        (expected_purpose, str(payload.get("purpose")), "purpose"),
    )
    for expected, actual, label in checks:
        if expected is not None and expected != actual:
            raise ValueError(f"voice grant {label} mismatch")
    nonce = str(payload.get("nonce") or "")
    if not nonce:
        raise ValueError("voice grant nonce missing")
    if consumed_nonces is not None:
        if nonce in consumed_nonces:
            raise ValueError("voice grant already consumed")
        if consume:
            consumed_nonces.add(nonce)
    grant_id = hashlib.sha256(token.encode()).hexdigest()
    return VoiceGrant(
        grant_id=grant_id,
        source_id=str(payload["source_id"]),
        agent=str(payload["agent"]).upper(),
        purpose=str(payload["purpose"]),
        mode=str(payload["mode"]),
        issued_at=int(payload["iat"]),
        expires_at=int(payload["exp"]),
        nonce=nonce,
    )


@asynccontextmanager
async def _connection(pool_or_conn: Any):
    acquire = getattr(pool_or_conn, "acquire", None)
    if acquire is None:
        yield pool_or_conn
    else:
        async with acquire() as conn:
            yield conn


class SoulCoordinationStore:
    """Small asyncpg-compatible persistence API for coordinator integration."""

    def __init__(self, pool_or_conn: Any):
        self.db = pool_or_conn

    async def create_turn(
        self,
        *,
        source_message_id: str,
        requester: str,
        request_text: str,
        mode: str,
        lead_agent: str,
        assignments: Sequence[Mapping[str, Any]],
        ack_seconds: int = 2,
        result_seconds: int = 120,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Idempotently create a turn and its assignments in one transaction."""

        if mode not in MODES:
            raise ValueError("invalid mode")
        source = str(source_message_id).strip()
        if not source:
            raise ValueError("source_message_id is required")
        request_hash = hashlib.sha256(request_text.encode()).hexdigest()
        payload = {
            "source_message_id": source,
            "requester": requester,
            "request_hash": request_hash,
            "mode": mode,
            "lead_agent": lead_agent.upper(),
            "ack_seconds": max(1, int(ack_seconds)),
            "result_seconds": max(5, int(result_seconds)),
            "metadata": dict(metadata or {}),
            "assignments": [
                {
                    "agent": str(item["agent"]).upper(),
                    "role": item["role"],
                    "public_write": bool(item.get("public_write", False)),
                    "evidence": {"reason": item.get("reason", "")},
                }
                for item in assignments
            ],
        }
        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                "SELECT soul_v3.coordination_write($1,$2::jsonb) AS result",
                "create_turn", json.dumps(payload),
            )
        result = row["result"] if row else None
        if not isinstance(result, dict) or not result.get("ok"):
            raise RuntimeError("coordination create_turn rejected")
        return dict(result["turn"])

    async def get_turn(self, source_message_id: str) -> dict[str, Any] | None:
        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                "SELECT * FROM soul_v3.coordination_turns WHERE source_message_id=$1",
                source_message_id,
            )
            if not row:
                return None
            assignments = await conn.fetch(
                "SELECT * FROM soul_v3.coordination_assignments WHERE source_message_id=$1 ORDER BY agent",
                source_message_id,
            )
        result = dict(row)
        result["assignments"] = [dict(item) for item in assignments]
        return result

    async def get_last_turn_by_author(
        self, requester: str, within_seconds: int
    ) -> dict[str, Any] | None:
        """Most recent turn from the same requester inside a time window.

        Backs lead stickiness: a fragmented follow-up inherits this turn's
        ``lead_agent`` instead of re-rolling the ``source_id`` hash tie-break.
        Case-insensitive on ``requester`` so ``William``/``WILLIAM`` never miss.
        """
        req = str(requester or "").strip()
        if not req or int(within_seconds) <= 0:
            return None
        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM soul_v3.coordination_turns
                 WHERE upper(requester) = upper($1)
                   AND created_at >= now() - make_interval(secs => $2)
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                req, max(1, int(within_seconds)),
            )
        return dict(row) if row else None

    async def update_assignment(
        self,
        source_message_id: str,
        agent: str,
        *,
        status: str,
        evidence: Mapping[str, Any] | None = None,
        lease_seconds: int | None = None,
    ) -> bool:
        if status not in {"accepted", "working", "submitted", "done", "declined", "expired"}:
            raise ValueError("invalid assignment status")
        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                "SELECT soul_v3.coordination_write($1,$2::jsonb) AS result",
                "update_assignment", json.dumps({
                    "source_message_id": source_message_id,
                    "agent": agent.upper(),
                    "status": status,
                    "evidence": dict(evidence or {}),
                    "lease_seconds": lease_seconds,
                }),
            )
        return bool(row and row["result"].get("ok"))

    async def register_grant(self, grant: VoiceGrant, metadata: Mapping[str, Any] | None = None) -> bool:
        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                "SELECT soul_v3.coordination_write($1,$2::jsonb) AS result",
                "register_grant", json.dumps({
                    "grant_id": grant.grant_id,
                    "source_message_id": grant.source_id,
                    "agent": grant.agent,
                    "purpose": grant.purpose,
                    "expires_at": grant.expires_at,
                    "metadata": dict(metadata or {}),
                }),
            )
        return bool(row and row["result"].get("ok"))

    async def consume_grant(self, grant: VoiceGrant) -> bool:
        """Atomically consume exactly one unexpired registered grant."""

        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                "SELECT soul_v3.coordination_write($1,$2::jsonb) AS result",
                "consume_grant", json.dumps({
                    "grant_id": grant.grant_id,
                    "source_message_id": grant.source_id,
                    "agent": grant.agent,
                    "purpose": grant.purpose,
                }),
            )
        return bool(row and row["result"].get("ok"))

    async def complete_turn(
        self,
        source_message_id: str,
        lead_agent: str,
        final_message_id: str,
        *,
        expected_version: int | None = None,
    ) -> bool:
        """Complete with optional optimistic-CAS protection."""

        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                "SELECT soul_v3.coordination_write($1,$2::jsonb) AS result",
                "complete_turn", json.dumps({
                    "source_message_id": source_message_id,
                    "lead_agent": lead_agent.upper(),
                    "final_message_id": final_message_id,
                    "expected_version": expected_version,
                }),
            )
        return bool(row and row["result"].get("ok"))

    async def cancel_turn(self, source_message_id: str) -> bool:
        """Cancel a turn and atomically expire every unfinished assignment."""

        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                "SELECT soul_v3.coordination_write($1,$2::jsonb) AS result",
                "cancel_turn", json.dumps({"source_message_id": source_message_id}),
            )
        return bool(row and row["result"].get("ok"))


__all__ = [
    "MODES", "ROLES", "POLICY_VERSION", "VoiceGrant", "SoulCoordinationStore",
    "classify_mode", "choose_lead", "build_assignments", "make_voice_grant",
    "verify_voice_grant", "voice_idempotency_key",
]
