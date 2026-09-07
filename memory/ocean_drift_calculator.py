#!/usr/bin/env python3
"""OCEAN Drift Calculator — snapshot diario y cadena OCEAN por agente.

Compara ``identity.ocean_scores`` con el último snapshot enlazado. Los deltas
diarios deben permanecer dentro de ±0.02; un exceso falla cerrado sin avanzar
el snapshot. ``drift_score`` es la media absoluta del movimiento acumulado,
idéntica a la columna generated de PostgreSQL.

Niveles canónicos: stable (<0.005) | warning (<0.02) | critical (>=0.02).

Por ALICE 2026-05-20 (P3#7) — la tabla existía pero estaba vacía, nadie
medía drift histórico. Este script ejecuta vía systemd timer diario.

Uso: ocean_drift_calculator.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sys
import uuid
from datetime import datetime, timezone

import asyncpg

from seal_secrets import pg_dsn

PG_DSN = pg_dsn(required=True)
TRAITS = ("O", "C", "E", "A", "N")
SNAPSHOT_SCHEMA = "ocean_snapshot_v3"
CALCULATOR_VERSION = "3.0.0"
SNAPSHOT_TIMEZONE = "America/Lima"
CHAIN_FIELDS = (
    "agent",
    "event_id",
    "snapshot_day",
    "ocean",
    "deltas",
    "cumulative",
    "profile_sha256",
    "previous_chain_sha256",
    "legacy_anchor_sha256",
    "schema_version",
    "source_semantics",
    "writer_version",
)


class DriftGuardError(RuntimeError):
    """Fail-closed guard: no snapshot may advance after this exception."""


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)


def _parse(val) -> dict[str, float]:
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            return {}
    if not isinstance(val, dict):
        return {}
    parsed: dict[str, float] = {}
    for trait in TRAITS:
        if trait not in val:
            return {}
        try:
            score = float(val[trait])
        except (TypeError, ValueError):
            return {}
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            return {}
        parsed[trait] = score
    return parsed


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _profile_sha256(ocean: dict[str, float]) -> str:
    return hashlib.sha256(_canonical_json(ocean).encode("utf-8")).hexdigest()


def _chain_sha256(previous: str, payload: dict) -> str:
    material = f"{previous}\n{_canonical_json(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _event_payload_from_marker(marker: dict) -> dict:
    return {field: marker.get(field) for field in CHAIN_FIELDS}


def _verified_chain_hash(marker: dict) -> str:
    """Return a v3 marker head only when its bytes recompute exactly."""
    if marker.get("schema_version") != SNAPSHOT_SCHEMA:
        raise DriftGuardError("latest linked snapshot has an unexpected schema")
    previous = str(marker.get("previous_chain_sha256") or "")
    claimed = str(marker.get("chain_sha256") or "")
    for name, value in (("previous_chain_sha256", previous), ("chain_sha256", claimed)):
        if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
            raise DriftGuardError(f"invalid {name} in latest linked snapshot")
    expected = _chain_sha256(previous, _event_payload_from_marker(marker))
    if claimed != expected:
        raise DriftGuardError("latest linked snapshot chain hash mismatch")
    return claimed


def _cumulative_score(cumulative: dict[str, float]) -> float:
    """Canonical DB-compatible metric: mean absolute cumulative movement."""
    return round(sum(abs(cumulative[trait]) for trait in TRAITS) / len(TRAITS), 4)


def _canonical_drift_level(score: float) -> str:
    if score < 0.005:
        return "stable"
    if score < 0.02:
        return "warning"
    return "critical"


def _legacy_anchor_sha256(rows: list[dict]) -> str:
    """Anchor the immutable pre-v3 sequence without pretending it was chained."""
    normalized = []
    for row in rows:
        normalized.append(
            {
                key: (str(value) if value is not None else None)
                for key, value in sorted(dict(row).items())
            }
        )
    return _sha256_json({"schema": "ocean_legacy_anchor_v1", "rows": normalized})


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


async def discover_agents(conn: asyncpg.Connection) -> list[str]:
    """Enumerate every live identity that has an OCEAN projection.

    The old fixed list silently omitted four of nine identities.  Discovery is
    intentionally data-driven so additions cannot disappear from monitoring.
    """
    rows = await conn.fetch(
        "SELECT agent FROM soul_v3.identity "
        "WHERE ocean_scores IS NOT NULL ORDER BY agent"
    )
    return [str(row["agent"]) for row in rows]


def _metadata_object(raw: object) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise DriftGuardError("snapshot metadata is invalid JSON") from exc
    if not isinstance(raw, dict):
        raise DriftGuardError("snapshot metadata is not an object")
    return raw


async def verify_agent_chain(conn: asyncpg.Connection, agent: str) -> dict:
    """Recompute the complete v3 chain and its ordered legacy anchor."""
    legacy_rows = await conn.fetch(
        "SELECT id, agent, delta_o, delta_c, delta_e, delta_a, delta_n, "
        "       cumulative_o, cumulative_c, cumulative_e, cumulative_a, cumulative_n, "
        "       triggered_by, created_at, drift_score, drift_level "
        "FROM soul_v3.ocean_drift_log "
        "WHERE agent=$1 AND event_id IS NULL ORDER BY created_at, id",
        agent,
    )
    legacy_anchor = _legacy_anchor_sha256(list(legacy_rows))
    rows = await conn.fetch(
        "SELECT d.id, d.agent, d.event_id::text AS event_id, d.snapshot_day, "
        "       d.profile_sha256, d.previous_chain_sha256, d.chain_sha256, "
        "       d.snapshot_memory_id, d.drift_score, d.drift_level, "
        "       m.agent AS memory_agent, m.category, m.source, m.metadata "
        "FROM soul_v3.ocean_drift_log d "
        "JOIN soul_v3.memories m ON m.id=d.snapshot_memory_id "
        "WHERE d.agent=$1 AND d.event_id IS NOT NULL "
        "ORDER BY d.created_at, d.id",
        agent,
    )
    expected_previous = legacy_anchor
    for index, row in enumerate(rows):
        marker = _metadata_object(row["metadata"])
        if row["memory_agent"] != agent or row["category"] != "snapshot" or row["source"] != "ocean_drift_calculator":
            raise DriftGuardError(f"[{agent}] event {row['event_id']} links to an invalid snapshot row")
        pairs = {
            "agent": str(row["agent"]),
            "event_id": str(row["event_id"]),
            "snapshot_day": str(row["snapshot_day"]),
            "profile_sha256": str(row["profile_sha256"]),
            "previous_chain_sha256": str(row["previous_chain_sha256"]),
            "chain_sha256": str(row["chain_sha256"]),
            "drift_score": str(row["drift_score"]),
            "drift_level": str(row["drift_level"]),
        }
        for field, row_value in pairs.items():
            marker_value = marker.get(field)
            if field == "drift_score":
                if float(marker_value) != float(row_value):
                    raise DriftGuardError(f"[{agent}] event {row['event_id']} {field} mismatch")
            elif str(marker_value) != row_value:
                raise DriftGuardError(f"[{agent}] event {row['event_id']} {field} mismatch")
        if str(row["previous_chain_sha256"]) != expected_previous:
            raise DriftGuardError(f"[{agent}] chain gap/fork before event {row['event_id']}")
        if index == 0:
            if marker.get("legacy_anchor_sha256") != legacy_anchor:
                raise DriftGuardError(f"[{agent}] first v3 event does not anchor legacy history")
        elif marker.get("legacy_anchor_sha256") is not None:
            raise DriftGuardError(f"[{agent}] non-initial v3 event repeats a legacy anchor")
        expected_previous = _verified_chain_hash(marker)
    return {
        "agent": agent,
        "legacy_rows": len(legacy_rows),
        "v3_events": len(rows),
        "legacy_anchor_sha256": legacy_anchor,
        "chain_head_sha256": expected_previous if rows else None,
        "verified": True,
    }


def _clamp(v, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return 0.0


async def _calculate_locked(
    conn: asyncpg.Connection,
    agent: str,
    *,
    dry_run: bool,
) -> dict | None:
    """Calculate/write one event while the caller holds the per-agent lock."""
    row = await conn.fetchrow(
        "SELECT ocean_scores FROM soul_v3.identity WHERE agent=$1",
        agent,
    )
    if not row:
        log(f"[{agent}] no identity row, skip")
        return None

    current = _parse(row["ocean_scores"])
    if not current:
        log(f"[{agent}] ocean_scores empty, skip")
        return None

    snapshot_date = await conn.fetchval(
        "SELECT (NOW() AT TIME ZONE $1)::date",
        SNAPSHOT_TIMEZONE,
    )
    snapshot_day = snapshot_date.isoformat()
    if not dry_run:
        existing = await conn.fetchval(
            "SELECT event_id::text FROM soul_v3.ocean_drift_log "
            "WHERE agent=$1 AND snapshot_day=$2 AND event_id IS NOT NULL",
            agent,
            snapshot_date,
        )
        if existing:
            log(f"[{agent}] snapshot {snapshot_day} already recorded as {existing}")
            return {"agent": agent, "status": "already_recorded", "event_id": existing}

    # Latest row drives cumulative movement. Ties are deterministic by id.
    last = await conn.fetchrow(
        "SELECT id, delta_o, delta_c, delta_e, delta_a, delta_n, "
        "       cumulative_o, cumulative_c, cumulative_e, cumulative_a, cumulative_n "
        "FROM soul_v3.ocean_drift_log WHERE agent=$1 "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        agent,
    )

    # Read the latest OCEAN projection, regardless of legacy/v3 epoch.
    prev_marker = await conn.fetchrow(
        "SELECT id, metadata FROM soul_v3.memories "
        "WHERE agent=$1 AND category='snapshot' AND source='ocean_drift_calculator' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        agent,
    )
    prev_ocean: dict[str, float] = {}
    if prev_marker and prev_marker["metadata"]:
        marker = prev_marker["metadata"]
        if isinstance(marker, str):
            try:
                marker = json.loads(marker)
            except (TypeError, ValueError):
                marker = {}
        if isinstance(marker, dict):
            prev_ocean = _parse(marker.get("ocean"))

    # The v3 chain head is structurally linked from drift_log to its snapshot.
    linked = await conn.fetchrow(
        "SELECT d.id, d.event_id::text AS event_id, d.snapshot_day, d.profile_sha256, "
        "       d.previous_chain_sha256, d.chain_sha256, d.snapshot_memory_id, m.metadata "
        "FROM soul_v3.ocean_drift_log d "
        "JOIN soul_v3.memories m ON m.id=d.snapshot_memory_id "
        "WHERE d.agent=$1 AND d.event_id IS NOT NULL "
        "ORDER BY d.created_at DESC, d.id DESC LIMIT 1",
        agent,
    )
    legacy_anchor = None
    if linked:
        linked_marker = linked["metadata"]
        if isinstance(linked_marker, str):
            try:
                linked_marker = json.loads(linked_marker)
            except (TypeError, ValueError) as exc:
                raise DriftGuardError("latest linked snapshot metadata is invalid JSON") from exc
        if not isinstance(linked_marker, dict):
            raise DriftGuardError("latest linked snapshot metadata is not an object")
        if str(linked_marker.get("event_id")) != str(linked["event_id"]):
            raise DriftGuardError("drift row and snapshot event_id mismatch")
        previous_chain_hash = _verified_chain_hash(linked_marker)
        marker_id = prev_marker.get("id") if prev_marker else None
        if marker_id is not None and int(marker_id) != int(linked["snapshot_memory_id"]):
            raise DriftGuardError("an unlinked snapshot exists after the latest v3 event")
        if last and last.get("id") is not None and int(last["id"]) != int(linked["id"]):
            raise DriftGuardError("an unlinked drift row exists after the latest v3 event")
        linked_pairs = {
            "snapshot_day": str(linked["snapshot_day"]),
            "profile_sha256": str(linked["profile_sha256"]),
            "previous_chain_sha256": str(linked["previous_chain_sha256"]),
            "chain_sha256": str(linked["chain_sha256"]),
        }
        for field, row_value in linked_pairs.items():
            if str(linked_marker.get(field)) != row_value:
                raise DriftGuardError(f"drift row and snapshot {field} mismatch")
        prev_ocean = _parse(linked_marker.get("ocean"))
        if not prev_ocean:
            raise DriftGuardError("latest linked snapshot has no valid OCEAN projection")
        if previous_chain_hash != str(linked["chain_sha256"]):
            raise DriftGuardError("drift row and snapshot chain hash mismatch")
    else:
        legacy_rows = await conn.fetch(
            "SELECT id, agent, delta_o, delta_c, delta_e, delta_a, delta_n, "
            "       cumulative_o, cumulative_c, cumulative_e, cumulative_a, cumulative_n, "
            "       triggered_by, created_at, drift_score, drift_level "
            "FROM soul_v3.ocean_drift_log WHERE agent=$1 ORDER BY created_at, id",
            agent,
        )
        legacy_anchor = _legacy_anchor_sha256(list(legacy_rows))
        previous_chain_hash = legacy_anchor

    if not prev_ocean:
        # First projection ever: establish a baseline without inventing movement.
        deltas = {t: 0.0 for t in TRAITS}
        log(f"[{agent}] primer snapshot — registro baseline")
    else:
        deltas = {t: round(float(current.get(t, 0)) - float(prev_ocean.get(t, 0)), 4) for t in TRAITS}

    # Never advance the snapshot after clipping: that would erase residual
    # movement forever. The operator must review/rebaseline explicitly.
    saturated_traits = {trait: value for trait, value in deltas.items() if abs(value) > 0.02}
    if saturated_traits:
        raise DriftGuardError(
            f"[{agent}] raw drift exceeds daily guardrail; snapshot not advanced: "
            f"{saturated_traits}"
        )
    deltas_capped = dict(deltas)

    # Cumulative = previo + |delta_capped|, capeado a 0.30
    if last:
        cum = {
            "O": _clamp(float(last["cumulative_o"] or 0) + abs(deltas_capped["O"]), -0.30, 0.30),
            "C": _clamp(float(last["cumulative_c"] or 0) + abs(deltas_capped["C"]), -0.30, 0.30),
            "E": _clamp(float(last["cumulative_e"] or 0) + abs(deltas_capped["E"]), -0.30, 0.30),
            "A": _clamp(float(last["cumulative_a"] or 0) + abs(deltas_capped["A"]), -0.30, 0.30),
            "N": _clamp(float(last["cumulative_n"] or 0) + abs(deltas_capped["N"]), -0.30, 0.30),
        }
    else:
        cum = {t: abs(deltas_capped[t]) for t in TRAITS}

    incremental_rms = round(math.sqrt(sum(d ** 2 for d in deltas_capped.values()) / len(TRAITS)), 4)
    drift_score = _cumulative_score(cum)
    level = _canonical_drift_level(drift_score)

    log(
        f"[{agent}] delta={deltas_capped} cum={cum} "
        f"incremental_rms={incremental_rms} drift_score={drift_score} ({level})"
    )

    event_id = str(uuid.uuid4())
    event_payload = {
        "agent": agent,
        "event_id": event_id,
        "snapshot_day": snapshot_day,
        "ocean": current,
        "deltas": deltas_capped,
        "cumulative": cum,
        "profile_sha256": _profile_sha256(current),
        "previous_chain_sha256": previous_chain_hash,
        "legacy_anchor_sha256": legacy_anchor,
        "schema_version": SNAPSHOT_SCHEMA,
        "source_semantics": "soul_v3.identity.ocean_scores",
        "writer_version": CALCULATOR_VERSION,
    }
    event_payload["chain_sha256"] = _chain_sha256(previous_chain_hash, event_payload)
    metadata = {
        **event_payload,
        "incremental_rms": incremental_rms,
        "drift_score": drift_score,
        "drift_level": level,
        "saturated": False,
    }

    if not dry_run:
        snapshot_memory_id = await conn.fetchval(
            "INSERT INTO soul_v3.memories "
            "(agent, category, content, importance, source, metadata, created_at) "
            "VALUES ($1, 'snapshot', $2, 3, 'ocean_drift_calculator', $3::jsonb, NOW()) "
            "RETURNING id",
            agent,
            f"OCEAN snapshot for drift tracking: O={current.get('O')} C={current.get('C')} E={current.get('E')} A={current.get('A')} N={current.get('N')}",
            _canonical_json(metadata),
        )
        await conn.execute(
            "INSERT INTO soul_v3.ocean_drift_log "
            "(agent, delta_o, delta_c, delta_e, delta_a, delta_n, "
            " cumulative_o, cumulative_c, cumulative_e, cumulative_a, cumulative_n, "
            " triggered_by, event_id, snapshot_day, profile_sha256, "
            " previous_chain_sha256, chain_sha256, snapshot_memory_id, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, "
            "        $12, $13::uuid, $14::date, $15, $16, $17, $18, NOW())",
            agent,
            deltas_capped["O"], deltas_capped["C"], deltas_capped["E"], deltas_capped["A"], deltas_capped["N"],
            cum["O"], cum["C"], cum["E"], cum["A"], cum["N"],
            f"scheduled_calculator:{CALCULATOR_VERSION}",
            event_id,
            snapshot_date,
            event_payload["profile_sha256"],
            previous_chain_hash,
            event_payload["chain_sha256"],
            snapshot_memory_id,
        )
        log(f"[{agent}] INSERT OK (drift_log + snapshot memory)")

    return {
        "agent": agent,
        "status": "dry_run" if dry_run else "recorded",
        "event_id": event_id,
        "deltas": deltas_capped,
        "cumulative": cum,
        "incremental_rms": incremental_rms,
        "drift_score": drift_score,
        "drift_level": level,
        "saturated": False,
        "profile_sha256": event_payload["profile_sha256"],
        "chain_sha256": event_payload["chain_sha256"],
    }


async def calculate_for_agent(
    conn: asyncpg.Connection,
    agent: str,
    dry_run: bool = False,
) -> dict | None:
    """Measure one agent; writes are serialized and fully transactional."""
    if dry_run:
        return await _calculate_locked(conn, agent, dry_run=True)
    async with conn.transaction():
        await conn.fetchval(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
            f"ocean-drift:{agent}",
        )
        return await _calculate_locked(conn, agent, dry_run=False)


async def main() -> int:
    dry_run = "--dry-run" in sys.argv
    verify_only = "--verify-only" in sys.argv
    log(
        f"=== ocean_drift_calculator start "
        f"(dry_run={dry_run}, verify_only={verify_only}) ==="
    )
    try:
        conn = await asyncpg.connect(PG_DSN)
    except Exception as e:
        log(f"DB connect failed: {e}")
        return 2

    try:
        agents = await discover_agents(conn)
        if not agents:
            log("Fatal: no OCEAN identities visible")
            return 4
        log(f"discovered={len(agents)} identities: {','.join(agents)}")
        if verify_only:
            verified = [await verify_agent_chain(conn, agent) for agent in agents]
            log(
                f"=== chain verification OK: {len(verified)} agents, "
                f"{sum(item['v3_events'] for item in verified)} v3 events ==="
            )
            return 0
        results = []
        rejected: list[tuple[str, str]] = []
        for agent in agents:
            try:
                r = await calculate_for_agent(conn, agent, dry_run=dry_run)
            except DriftGuardError as exc:
                rejected.append((agent, str(exc)))
                log(f"GUARD REJECTED {exc}")
                continue
            if r:
                results.append(r)
        # Summary
        elevated = [
            r for r in results
            if r.get("drift_level") in ("warning", "critical")
        ]
        log(
            f"=== done. {len(results)} agents processed, {len(elevated)} elevated, "
            f"{len(rejected)} rejected ==="
        )
        if elevated:
            for r in elevated:
                log(
                    f"  ELEVATED [{r['agent']}] {r['drift_level']} "
                    f"score={r['drift_score']}"
                )
        if rejected:
            return 5
        return 0
    except Exception as e:
        log(f"Fatal: {e}")
        return 3
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
