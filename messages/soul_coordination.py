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
MODES = frozenset({"social", "discussion", "execution", "roundtable", "direct"})
ROLES = frozenset({"lead", "contributor", "speaker"})
DEFAULT_ROSTER = ("NEXUS", "JARVIS", "ALICE", "FABLE", "ADA")

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
    r"reinicien|auditen|investiguen|prueben|ejecuten|creen|modifiquen|"
    r"reparen|instalen|migren|revisen|trabajen|ayuden|busquen|hagan|"
    r"borren|eliminen)\b|```",
    re.IGNORECASE,
)
_DISCUSSION = re.compile(
    r"\b(opina\w*|explica\w*|compara\w*|analiza\w*|discute\w*|"
    r"recomienda\w*|soluci[oó]n|por qu[eé]|qu[eé] piensas|qu[eé] opinas)\b|\?",
    re.IGNORECASE,
)
_SOCIAL = re.compile(
    r"^(hola|buen(?:os d[ií]as|as tardes|as noches)|gracias|ok|okay|listo|"
    r"bien|genial|jaja|xd|c[oó]mo est[aá]n?|todo bien)"
    r"(?:\s+(?:familia|equipo|chicos|chicas|hermanos|hermanas))?[\s!?.]*$",
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
    group_audience = bool(re.search(
        r"\b(todos|todas|equipo|agentes|chicos|chicas|hermanos|hermanas|familia)\b",
        low,
    ))
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
    if _SOCIAL.match(clean) or (len(clean) <= 32 and not _DISCUSSION.search(clean)):
        return "social"
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
) -> str:
    """Choose a deterministic, online and load-aware lead.

    Capability keyword matches dominate proficiency; open work applies a small
    penalty.  ``cannot`` entries containing ``public``/``respond`` exclude an
    agent from lead duty.  Stable hashing is only the final exact-tie breaker.
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
) -> list[dict[str, Any]]:
    """Build role/public-write assignments for one turn."""

    if mode not in MODES:
        raise ValueError(f"invalid coordination mode: {mode}")
    lead = lead.upper()
    roster = list(dict.fromkeys(a.upper() for a in (requested_agents or candidates)))
    if lead not in roster:
        roster.insert(0, lead)
    named = [a.upper() for a in (named_agents or _agent_names(text, roster)) if a.upper() in roster]
    if mode == "roundtable":
        speakers = named or roster
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
        async with _connection(self.db) as conn:
            transaction = conn.transaction()
            async with transaction:
                row = await conn.fetchrow(
                    """
                    INSERT INTO soul_v3.coordination_turns
                        (source_message_id, requester, request_hash, mode, lead_agent,
                         ack_deadline, result_deadline, metadata)
                    VALUES ($1,$2,$3,$4,$5,now()+make_interval(secs=>$6),
                            now()+make_interval(secs=>$7),$8::jsonb)
                    ON CONFLICT (source_message_id) DO UPDATE
                       SET updated_at = soul_v3.coordination_turns.updated_at
                    RETURNING *
                    """,
                    source, requester, request_hash, mode, lead_agent.upper(),
                    max(1, int(ack_seconds)), max(5, int(result_seconds)),
                    json.dumps(dict(metadata or {})),
                )
                for assignment in assignments:
                    await conn.execute(
                        """
                        INSERT INTO soul_v3.coordination_assignments
                            (source_message_id, agent, role, public_write, evidence)
                        VALUES ($1,$2,$3,$4,$5::jsonb)
                        ON CONFLICT (source_message_id, agent) DO NOTHING
                        """,
                        source, str(assignment["agent"]).upper(), assignment["role"],
                        bool(assignment.get("public_write", False)),
                        json.dumps({"reason": assignment.get("reason", "")}),
                    )
        return dict(row)

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
            result = await conn.execute(
                """
                UPDATE soul_v3.coordination_assignments
                   SET status=$3,
                       evidence=evidence || $4::jsonb,
                       last_heartbeat=now(),
                       lease_until=CASE WHEN $5::int IS NULL THEN lease_until
                                        ELSE now()+make_interval(secs=>$5) END
                 WHERE source_message_id=$1 AND agent=$2
                """,
                source_message_id, agent.upper(), status,
                json.dumps(dict(evidence or {})), lease_seconds,
            )
        return result.endswith(" 1")

    async def register_grant(self, grant: VoiceGrant, metadata: Mapping[str, Any] | None = None) -> bool:
        async with _connection(self.db) as conn:
            result = await conn.execute(
                """
                INSERT INTO soul_v3.coordination_voice_grants
                    (grant_id, source_message_id, agent, purpose, expires_at, metadata)
                VALUES ($1,$2,$3,$4,to_timestamp($5),$6::jsonb)
                ON CONFLICT (grant_id) DO NOTHING
                """,
                grant.grant_id, grant.source_id, grant.agent, grant.purpose,
                grant.expires_at, json.dumps(dict(metadata or {})),
            )
        return result.endswith(" 1")

    async def consume_grant(self, grant: VoiceGrant) -> bool:
        """Atomically consume exactly one unexpired registered grant."""

        async with _connection(self.db) as conn:
            row = await conn.fetchrow(
                """
                UPDATE soul_v3.coordination_voice_grants
                   SET consumed_at=now()
                 WHERE grant_id=$1
                   AND source_message_id=$2
                   AND agent=$3
                   AND purpose=$4
                   AND consumed_at IS NULL
                   AND expires_at >= now()
                RETURNING grant_id
                """,
                grant.grant_id, grant.source_id, grant.agent, grant.purpose,
            )
        return row is not None

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
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE soul_v3.coordination_turns
                       SET status='completed', final_message_id=$3, version=version+1
                     WHERE source_message_id=$1 AND lead_agent=$2
                       AND status NOT IN ('completed','cancelled','expired')
                       AND ($4::int IS NULL OR version=$4)
                    RETURNING source_message_id
                    """,
                    source_message_id, lead_agent.upper(), final_message_id, expected_version,
                )
                if row is not None:
                    await conn.execute(
                        """
                        UPDATE soul_v3.coordination_assignments
                           SET status='done', last_heartbeat=now()
                         WHERE source_message_id=$1 AND status NOT IN ('done','declined','expired')
                        """,
                        source_message_id,
                    )
        return row is not None

    async def cancel_turn(self, source_message_id: str) -> bool:
        """Cancel a turn and atomically expire every unfinished assignment."""

        async with _connection(self.db) as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE soul_v3.coordination_turns
                       SET status='cancelled', version=version+1
                     WHERE source_message_id=$1
                       AND status NOT IN ('completed','cancelled','expired')
                    RETURNING source_message_id
                    """,
                    source_message_id,
                )
                if row is not None:
                    await conn.execute(
                        """
                        UPDATE soul_v3.coordination_assignments
                           SET status='expired', last_heartbeat=now()
                         WHERE source_message_id=$1 AND status NOT IN ('done','declined','expired')
                        """,
                        source_message_id,
                    )
        return row is not None


__all__ = [
    "MODES", "ROLES", "POLICY_VERSION", "VoiceGrant", "SoulCoordinationStore",
    "classify_mode", "choose_lead", "build_assignments", "make_voice_grant",
    "verify_voice_grant", "voice_idempotency_key",
]
