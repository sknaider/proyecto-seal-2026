"""harness_oracle — primitivo de observación + escalación (Fase 1, lane NEXUS).

Detecta divergencia creencia-vs-verdad estructuralmente (el fósil #966) y la registra
append-only + escala al dueño server-side del state_key.

Spec: fable/agent_harness_study/SPEC_harness_oracle_v2.md §3-§4.
Reparto: NEXUS provee este primitivo (observe + escalate). JARVIS/ADA (Fase 2) lo llaman
desde check_at_use (inline) y el watcher periódico (frozen-liveness).

Principios aplicados:
- C3: el destino de escalación = owner_agent del REGISTRO (server-side), NO caller-claimed.
- FAIL LOUD: errores se imprimen a stderr, no se tragan.
- hash-only: se guarda hash + snippet redactado, no el valor full.
- append-only: solo INSERT (la tabla revoca UPDATE/DELETE a PUBLIC).
- anti cry-wolf: cooldown por state_key (lección del drift detector).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import os
import re
import subprocess
import sys

try:
    from seal_secrets import pg_dsn
except Exception:  # pragma: no cover - fallback si el módulo no está en path
    pg_dsn = None

import asyncpg

_SEND_WEBCHAT = "/home/dadito/IA/proyecto-seal/messages/send_webchat.py"
_COOLDOWN_DEFAULT_S = 1800  # 30 min por state_key (igual que el resource guard)
_SNIPPET_MAX = 80
_SECRET_RE = re.compile(
    r"(?i)(token|api[_-]?key|password|secret|authorization)\s*[:=]\s*([^\s,;]+)"
)


def _dsn() -> str:
    env = os.environ.get("SEAL_DB_URL")
    if env:
        return env
    if pg_dsn is not None:
        return pg_dsn(required=True)
    raise RuntimeError("[harness_oracle] sin DSN: define SEAL_DB_URL o instala seal_secrets")


def _h(value) -> str:
    """Hash corto y estable de un valor (16 hex)."""
    s = "" if value is None else str(value)
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _redact(value) -> str:
    """Snippet redactado — nunca el valor full si es sensible."""
    s = "" if value is None else str(value)
    s = _SECRET_RE.sub(r"\1=[REDACTED]", s)
    if len(s) <= _SNIPPET_MAX:
        return s
    return s[:_SNIPPET_MAX] + "…"


async def check_at_use(
    agent: str,
    state_key: str,
    belief_value,
    conn: asyncpg.Connection | None = None,
) -> dict:
    """Inline check v2: validar una creencia justo antes de actuar.

    Es un detector/normalizador, no un lock: devuelve el `true_value` fresco para que
    el caller actúe con la fuente canónica, aunque haya detectado stale-read.
    """
    return await observe(agent, state_key, belief_value, "stale_read", conn=conn)


async def observe(
    agent: str,
    state_key: str,
    belief_value,
    divergence_kind: str = "stale_read",
    conn: asyncpg.Connection | None = None,
) -> dict:
    """Compara la creencia del agente vs la verdad canónica del registro e inserta una observación.

    Si diverge → escala al owner_agent (server-side) con cooldown. Devuelve el resultado.
    `divergence_kind`: 'stale_read' (inline check_at_use) | 'frozen_liveness' (watcher).
    """
    own_conn = conn is None
    if own_conn:
        conn = await asyncpg.connect(_dsn())
    try:
        reg = await _fetch_registry(conn, state_key)
        if reg is None:
            # FAIL LOUD: sin fuente canónica registrada NO podemos definir 'la verdad'.
            msg = f"[harness_oracle] state_key sin registro canónico: {state_key!r} — no se puede observar"
            print(msg, file=sys.stderr)
            raise KeyError(msg)

        # La verdad = ejecutar la query canónica del registro (server-side, no input del caller).
        true_value = await conn.fetchval(reg["canonical_query"])

        belief_hash, true_hash = _h(belief_value), _h(true_value)
        diverged = belief_hash != true_hash
        row_id, delivered = await _record_observation(
            conn=conn,
            agent=agent,
            state_key=state_key,
            belief_value=belief_value,
            true_value=true_value,
            belief_hash=belief_hash,
            true_hash=true_hash,
            diverged=diverged,
            divergence_kind=divergence_kind,
            owner=reg["owner_agent"],
        )
        if diverged:
            print(
                f"[harness_oracle] DIVERGED state_key={state_key} agent={agent} "
                f"kind={divergence_kind} belief={belief_hash} true={true_hash} "
                f"escalated_to={reg['owner_agent']} delivered={delivered}",
                file=sys.stderr,
            )
        return {
            "id": row_id, "diverged": diverged, "belief_hash": belief_hash,
            "true_hash": true_hash, "true_value": true_value,
            "escalated_to": reg["owner_agent"] if diverged else None,
            "escalation_delivered": delivered,
        }
    finally:
        if own_conn:
            await conn.close()


async def check_liveness_once(
    agent: str = "SYSTEM",
    conn: asyncpg.Connection | None = None,
) -> list[dict]:
    """Watcher v2: detecta fuentes canónicas congeladas aunque nadie las lea.

    Para cada `state_key` con `liveness_max_lag`, ejecuta su query canónica. Si
    devuelve un timestamp más viejo que `now - liveness_max_lag`, registra
    `frozen_liveness` y escala con cooldown.
    """
    own_conn = conn is None
    if own_conn:
        conn = await asyncpg.connect(_dsn())
    try:
        rows = await conn.fetch(
            """SELECT state_key, canonical_query, liveness_max_lag, owner_agent
               FROM soul_v3.harness_truth_registry
               WHERE liveness_max_lag IS NOT NULL"""
        )
        results: list[dict] = []
        now = datetime.now(timezone.utc)
        for reg in rows:
            true_value = await conn.fetchval(reg["canonical_query"])
            is_frozen = _is_frozen(true_value, reg["liveness_max_lag"], now)
            if not is_frozen:
                results.append({
                    "state_key": reg["state_key"],
                    "diverged": False,
                    "divergence_kind": None,
                    "true_value": true_value,
                })
                continue

            true_hash = _h({
                "last_seen": true_value,
                "expected_after": now - reg["liveness_max_lag"],
            })
            row_id, delivered = await _record_observation(
                conn=conn,
                agent=agent,
                state_key=reg["state_key"],
                belief_value=f"last_seen={true_value}",
                true_value=true_value,
                belief_hash=_h(true_value),
                true_hash=true_hash,
                diverged=True,
                divergence_kind="frozen_liveness",
                owner=reg["owner_agent"],
            )
            results.append({
                "id": row_id,
                "state_key": reg["state_key"],
                "diverged": True,
                "divergence_kind": "frozen_liveness",
                "true_value": true_value,
                "escalated_to": reg["owner_agent"],
                "escalation_delivered": delivered,
            })
        return results
    finally:
        if own_conn:
            await conn.close()


async def _fetch_registry(conn, state_key: str):
    return await conn.fetchrow(
        """SELECT canonical_query, owner_agent
           FROM soul_v3.harness_truth_registry
           WHERE state_key=$1""",
        state_key,
    )


def _is_frozen(value, lag, now: datetime) -> bool:
    if value is None or lag is None:
        return False
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value < now - lag


async def _record_observation(
    *,
    conn,
    agent: str,
    state_key: str,
    belief_value,
    true_value,
    belief_hash: str,
    true_hash: str,
    diverged: bool,
    divergence_kind: str,
    owner: str,
) -> tuple[int, bool]:
    escalated_to = owner if diverged else None
    delivered = False
    if diverged:
        delivered = await _escalate(
            owner=owner,
            agent=agent,
            state_key=state_key,
            belief=belief_value,
            true=true_value,
            kind=divergence_kind,
            conn=conn,
        )

    row_id = await conn.fetchval(
        """INSERT INTO soul_v3.harness_oracle
             (agent, state_key, belief_hash, true_hash, belief_snippet,
              diverged, divergence_kind, escalated_to, escalation_delivered)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id""",
        agent,
        state_key,
        belief_hash,
        true_hash,
        _redact(belief_value),
        diverged,
        divergence_kind if diverged else None,
        escalated_to,
        delivered,
    )
    return row_id, delivered


async def _escalate(owner, agent, state_key, belief, true, kind, conn) -> bool:
    """Escala por canal VIVO al owner (server-side). Cooldown por state_key. Devuelve si LLEGÓ.

    Síncrono a propósito: usa el subprocess de send_webchat (mismo patrón del resto del equipo).
    El cooldown se chequea contra la última escalación entregada de este state_key en la tabla
    (sin estado externo — la tabla append-only ES el registro).
    """
    cooldown_s = int(os.environ.get("SEAL_HARNESS_ORACLE_COOLDOWN_S", _COOLDOWN_DEFAULT_S))
    if cooldown_s > 0:
        recent = await conn.fetchval(
            """SELECT EXISTS (
                   SELECT 1
                   FROM soul_v3.harness_oracle
                   WHERE state_key=$1
                     AND diverged=true
                     AND escalation_delivered=true
                     AND ts > now() - make_interval(secs => $2)
               )""",
            state_key,
            cooldown_s,
        )
        if recent:
            return False

    text = (
        f"⚠️ harness_oracle: DIVERGENCIA detectada en '{state_key}' "
        f"(tipo: {kind}). Agente '{agent}' creyó un estado distinto al canónico. "
        f"belief≠true. Eres el owner de este state_key — revisa si actuaste sobre un fósil. "
        f"(Detección estructural, no humana — esto es lo que faltó en #966.)"
    )
    try:
        ok = _send_live_alert(owner, text)
        if not ok:
            print(f"[harness_oracle] escalación a {owner} FALLÓ", file=sys.stderr)
        return ok
    except Exception as e:  # FAIL LOUD
        print(f"[harness_oracle] escalación a {owner} EXCEPCIÓN: {e}", file=sys.stderr)
        return False


def _send_live_alert(owner: str, text: str) -> bool:
    r = subprocess.run(
        ["python3", _SEND_WEBCHAT, "NEXUS", owner, text],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        print(r.stderr.strip(), file=sys.stderr)
    return r.returncode == 0


# CLI de prueba manual: python3 harness_oracle.py <agent> <state_key> <belief>
if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("uso: harness_oracle.py <agent> <state_key> <belief_value>", file=sys.stderr)
        sys.exit(2)
    res = asyncio.run(observe(sys.argv[1], sys.argv[2], sys.argv[3]))
    print(res)
