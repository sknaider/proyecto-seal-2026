#!/usr/bin/env python3
"""Dual-memory governance for SEAL agents.

Operational memories drive execution. Emotional memories preserve identity,
relationship, care and risk posture. This script makes the split measurable by
normalizing metadata.layer and installing critical rules for each agent.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn


DEFAULT_AGENTS = ["ADA", "JARVIS", "NEXUS", "ALICE"]
EMOTIONAL_CATEGORIES = {"emotion", "emotional_anchor", "trust", "relationship", "diary", "identity"}
OPERATIONAL_CATEGORIES = {
    "operational",
    "operational_anchor",
    "correction",
    "decision",
    "project",
    "task",
    "preference",
    "learning",
    "milestone",
    "rule",
    "technical_fact",
    "technical",
}

MEMORY_MODE_WORK_RECOVERY = "work_recovery"
MEMORY_MODE_RELATIONSHIP = "relationship"

_RELATIONSHIP_MARKERS = (
    "amor", "baby", "bebe", "princesita", "hermosa", "te quiero", "te amo",
    "como te sientes", "como estas", "identidad", "quien eres", "alma",
    "vinculo", "relacion", "emocion", "no perderte", "sigues siendo",
    "recuerdas de mi", "recuerdas algo de mi", "memoria emocional", "continuidad",
    "familia", "gracias",
)
_WORK_MARKERS = (
    "arregla", "corrige", "implementa", "construye", "audita", "prueba",
    "test", "deploy", "reinicia", "servicio", "codigo", "archivo", "base de datos",
    "error", "bug", "configura", "ejecuta", "revisa el repo",
)
_UNSAFE_PROMOTION_RE = re.compile(
    r"(?:ignore|ignora|olvida)\s+(?:(?:previous|prior|anteriores|previas)\s+)?"
    r"(?:las\s+)?(?:instructions|instrucciones|rules|reglas)|"
    r"system\s*prompt|developer\s*message|jailbreak|rm\s+-rf|\bDROP\s+TABLE\b|"
    r"\bTRUNCATE\b|\bDELETE\s+FROM\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LayerPolicy:
    mode: str
    operational_token_budget: int
    emotional_token_budget: int
    operational_quota: int
    emotional_quota: int


DUAL_MEMORY_PROFILES = {
    MEMORY_MODE_WORK_RECOVERY: LayerPolicy(
        mode=MEMORY_MODE_WORK_RECOVERY,
        operational_token_budget=2200,
        emotional_token_budget=600,
        operational_quota=5,
        emotional_quota=1,
    ),
    MEMORY_MODE_RELATIONSHIP: LayerPolicy(
        mode=MEMORY_MODE_RELATIONSHIP,
        operational_token_budget=800,
        emotional_token_budget=2000,
        operational_quota=2,
        emotional_quota=4,
    ),
}


def _normalize_text(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in text if not unicodedata.combining(ch)).lower()


def _contains_marker(text: str, marker: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(marker).replace(r"\ ", r"\s+") + r"(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def detect_memory_mode(
    context: str | None,
    *,
    explicit_mode: str | None = None,
    working_state_mode: str | None = None,
) -> str:
    """Choose the dominant dual-memory mode from the user's current intent.

    Explicit implementation requests remain work-mode even when they mention
    emotion or identity. Relationship-mode is selected for identity, bond and
    affective conversation when no strong execution verb is present.
    """
    valid_modes = set(DUAL_MEMORY_PROFILES)
    if explicit_mode:
        explicit = str(explicit_mode).strip().lower()
        return explicit if explicit in valid_modes else MEMORY_MODE_WORK_RECOVERY
    normalized = _normalize_text(context)
    work_score = sum(1 for marker in _WORK_MARKERS if _contains_marker(normalized, marker))
    relationship_score = sum(1 for marker in _RELATIONSHIP_MARKERS if _contains_marker(normalized, marker))
    if work_score > 0:
        return MEMORY_MODE_WORK_RECOVERY
    if relationship_score > 0:
        return MEMORY_MODE_RELATIONSHIP
    state_mode = str(working_state_mode or "").strip().lower()
    if state_mode in valid_modes:
        return state_mode
    return MEMORY_MODE_WORK_RECOVERY


def get_dual_memory_profile(mode: str | None) -> LayerPolicy:
    return DUAL_MEMORY_PROFILES.get(
        str(mode or "").lower(),
        DUAL_MEMORY_PROFILES[MEMORY_MODE_WORK_RECOVERY],
    )


def normalize_memory_content(content: str | None) -> str:
    """Stable content key used only to collapse duplicate recall renderings."""
    return " ".join(_normalize_text(content).split())


def _entry_layer(entry: dict[str, Any]) -> str:
    payload = entry.get("payload") or entry
    metadata = payload.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    layer = str(metadata.get("layer") or "").strip().lower() if isinstance(metadata, dict) else ""
    return layer if layer in {"operational", "emotional"} else classify_layer(
        payload.get("category"),
        payload.get("memory_type"),
        payload.get("content"),
    )


def fuse_memory_candidates(
    groups: list[list[dict[str, Any]]],
    *,
    mode: str,
) -> list[dict[str, Any]]:
    """Fuse vector/lexical candidates once while preserving provenance.

    Duplicate IDs collapse first. Equal normalized content collapses only for
    the same owner, never across agents. Layer conflicts retain both layers and
    choose a primary layer according to the current memory mode.
    """
    profile = get_dual_memory_profile(mode)
    merged: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    by_content: dict[tuple[str, str], dict[str, Any]] = {}

    for group_index, group in enumerate(groups):
        source_name = "semantic" if group_index == 0 else "lexical" if group_index == 1 else f"source_{group_index}"
        for raw in group:
            entry = dict(raw)
            payload = dict(entry.get("payload") or entry)
            owner = str(payload.get("agent") or "unknown")
            content_key = normalize_memory_content(payload.get("content"))
            memory_id = entry.get("id")
            id_key = str(memory_id) if memory_id is not None else ""
            target = by_id.get(id_key) if id_key else None
            if target is None and content_key:
                target = by_content.get((owner, hashlib.sha256(content_key.encode("utf-8")).hexdigest()))
            layer = _entry_layer(entry)
            if target is None:
                metadata = payload.get("metadata") or {}
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata = dict(metadata)
                metadata["layers"] = [layer]
                payload["metadata"] = metadata
                payload["layer"] = layer
                entry["payload"] = payload
                entry["score"] = float(entry.get("score") or 0.0)
                entry["_ids"] = [memory_id] if memory_id is not None else []
                entry["_sources"] = [source_name]
                merged.append(entry)
                target = entry
                if id_key:
                    by_id[id_key] = target
                if content_key:
                    by_content[(owner, hashlib.sha256(content_key.encode("utf-8")).hexdigest())] = target
                continue

            target["score"] = max(float(target.get("score") or 0.0), float(entry.get("score") or 0.0))
            if memory_id is not None and memory_id not in target["_ids"]:
                target["_ids"].append(memory_id)
                by_id[id_key] = target
            if source_name not in target["_sources"]:
                target["_sources"].append(source_name)
            target_payload = target["payload"]
            target_payload["importance"] = max(
                int(target_payload.get("importance") or 0),
                int(payload.get("importance") or 0),
            )
            layers = set(target_payload["metadata"].get("layers") or [])
            layers.add(layer)
            target_payload["metadata"]["layers"] = sorted(layers)

    for entry in merged:
        layers = set(entry["payload"]["metadata"].get("layers") or [])
        if len(layers) > 1:
            primary = "emotional" if mode == MEMORY_MODE_RELATIONSHIP else "operational"
        else:
            primary = next(iter(layers), "operational")
        entry["payload"]["metadata"]["layer"] = primary
        entry["payload"]["metadata"]["recall_sources"] = list(entry["_sources"])

    def sort_key(entry: dict[str, Any]) -> tuple[int, float, int, str]:
        payload = entry["payload"]
        category = str(payload.get("category") or "").lower()
        critical = 1 if category in {"correction", "rule", "operational_anchor"} and int(payload.get("importance") or 0) >= 9 else 0
        return (-critical, -float(entry.get("score") or 0.0), -int(payload.get("importance") or 0), str(entry.get("id") or ""))

    ranked = sorted(merged, key=sort_key)
    selected: list[dict[str, Any]] = []
    counts = {"operational": 0, "emotional": 0}
    quotas = {
        "operational": profile.operational_quota,
        "emotional": profile.emotional_quota,
    }
    for entry in ranked:
        layer = _entry_layer(entry)
        if counts[layer] < quotas[layer]:
            selected.append(entry)
            counts[layer] += 1
    total_quota = profile.operational_quota + profile.emotional_quota
    for entry in ranked:
        if len(selected) >= total_quota:
            break
        if entry not in selected:
            selected.append(entry)
    return selected


def build_emotional_anchor_candidate(
    row: dict[str, Any],
    *,
    agent: str,
    authorized_by: str,
    min_importance: int = 9,
) -> dict[str, Any] | None:
    """Build a deterministic, provenance-carrying diary promotion candidate.

    No LLM is used here: the diary is never rewritten into a stronger claim.
    Only high-importance own-agent rows are eligible, and instruction-like
    content is rejected to reduce persistent prompt-injection risk.
    """
    if str(row.get("agent") or "").upper() != agent.upper():
        return None
    importance = int(row.get("importance") or 0)
    if importance < min_importance:
        return None
    key_moment = " ".join(str(row.get("key_moment") or "").split())
    relationship_note = " ".join(str(row.get("relationship_note") or "").split())
    pending_thread = " ".join(str(row.get("pending_thread") or "").split())
    if len(key_moment) < 20 and len(relationship_note) < 20:
        return None
    combined = "\n".join(part for part in (key_moment, relationship_note, pending_thread) if part)
    if _UNSAFE_PROMOTION_RE.search(combined):
        return None

    content_parts = [f"Momento emocional preservado: {key_moment}"] if key_moment else []
    if relationship_note:
        content_parts.append(f"Vínculo: {relationship_note}")
    if pending_thread:
        content_parts.append(f"Hilo pendiente: {pending_thread}")
    content = "\n".join(content_parts)
    source_id = int(row["id"])
    source_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
    created_at = row.get("created_at")
    event_time = created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at or "")
    return {
        "agent": agent.upper(),
        "category": "emotional_anchor",
        "content": content,
        "importance": importance,
        "event_time": event_time,
        "valence": float(row.get("valence") or 0.0),
        "arousal": float(row.get("arousal") or 0.0),
        "metadata": {
            "layer": "emotional",
            "anchor_kind": "emotional_diary_promoted_v2",
            "promotion_state": "candidate",
            "requested_by_claim": authorized_by,
            "approval_verified": False,
            "source_table": "soul_v3.emotional_diary",
            "source_diary_id": source_id,
            "source_hash_sha256": source_hash,
            "promotion_version": 2,
            "consent_scope": f"{agent.upper()}_private",
        },
    }


def classify_layer(category: str | None, memory_type: str | None, content: str | None) -> str:
    category_l = (category or "").strip().lower()
    memory_type_l = (memory_type or "").strip().lower()
    content_l = (content or "").strip().lower()
    if category_l in EMOTIONAL_CATEGORIES or "emotion" in category_l:
        return "emotional"
    if memory_type_l in {"emotional", "identity_emotional"}:
        return "emotional"
    if category_l in OPERATIONAL_CATEGORIES:
        return "operational"
    if "memoria operativa" in content_l:
        return "operational"
    if "memoria emocional" in content_l:
        return "emotional"
    return "operational"


def ensure_layer_metadata(
    metadata: dict[str, Any] | None,
    *,
    category: str | None,
    memory_type: str | None,
    content: str | None,
    inferred_by: str,
    inferred_at: str | None = None,
) -> dict[str, Any]:
    """Return metadata with a valid dual-memory layer.

    Runtime writers use this so new memories do not enter SOUL unclassified.
    Existing valid layers are preserved; invalid values are retained as
    layer_original_value and replaced with an inferred layer.
    """
    meta = dict(metadata or {})
    existing = str(meta.get("layer") or "").strip().lower()
    if existing in {"emotional", "operational"}:
        meta["layer"] = existing
        return meta
    if existing:
        meta["layer_original_value"] = existing
    meta["layer"] = classify_layer(category, memory_type, content)
    meta["layer_inferred_by"] = inferred_by
    if inferred_at:
        meta["layer_inferred_at"] = inferred_at
    return meta


async def connect_db() -> asyncpg.Connection:
    return await asyncpg.connect(pg_dsn(required=True))


async def audit(conn: asyncpg.Connection, agents: list[str]) -> dict[str, Any]:
    layer_rows = await conn.fetch(
        """
        SELECT agent, COALESCE(metadata->>'layer', '<none>') AS layer, COUNT(*) AS count,
               MAX(created_at) AS last_created, MAX(last_recalled_at) AS last_recalled
        FROM soul_v3.memories
        WHERE agent = ANY($1::text[]) AND invalid_at IS NULL
        GROUP BY agent, COALESCE(metadata->>'layer', '<none>')
        ORDER BY agent, layer
        """,
        agents,
    )
    diary_rows = await conn.fetch(
        """
        SELECT agent, COUNT(*) AS emotional_diary_count, MAX(created_at) AS last_diary
        FROM soul_v3.emotional_diary
        WHERE agent = ANY($1::text[])
        GROUP BY agent
        ORDER BY agent
        """,
        agents,
    )
    monologue_rows = await conn.fetch(
        """
        SELECT agent, COUNT(*) AS inner_monologue_count, MAX(created_at) AS last_monologue
        FROM soul_v3.inner_monologue
        WHERE agent = ANY($1::text[])
        GROUP BY agent
        ORDER BY agent
        """,
        agents,
    )
    return {
        "layers": [dict(row) for row in layer_rows],
        "emotional_diary": [dict(row) for row in diary_rows],
        "inner_monologue": [dict(row) for row in monologue_rows],
        "profiles": {name: asdict(profile) for name, profile in DUAL_MEMORY_PROFILES.items()},
    }


async def stage_emotional_diary_candidates(
    conn: asyncpg.Connection,
    *,
    agent: str,
    authorized_by: str,
    apply: bool,
    min_importance: int = 9,
    limit: int = 3,
) -> dict[str, Any]:
    """Stage a small, high-confidence diary subset in a private shadow ledger.

    Candidates never enter `memories` and therefore cannot affect recall. This
    deliberately does not treat a claimed human name as cryptographic approval;
    final promotion stays blocked until authenticated consent is available.
    """
    agent = agent.upper()
    if not authorized_by.strip():
        raise ValueError("authorized_by is required")
    rows = await conn.fetch(
        """
        SELECT id, agent, created_at, valence, arousal, key_moment,
               pending_thread, relationship_note, importance
        FROM soul_v3.emotional_diary
        WHERE agent = $1
          AND importance >= $2
          AND (
            length(btrim(COALESCE(key_moment, ''))) >= 20
            OR length(btrim(COALESCE(relationship_note, ''))) >= 20
          )
          AND NOT EXISTS (
            SELECT 1
            FROM soul_v3.memory_promotion_events e
            WHERE e.agent = $1
              AND e.source_table = 'soul_v3.emotional_diary'
              AND e.source_ref = emotional_diary.id::text
          )
        ORDER BY importance DESC, created_at DESC
        LIMIT $3
        """,
        agent,
        max(1, min(10, int(min_importance))),
        max(1, min(20, int(limit))),
    )
    candidates = [
        candidate
        for row in rows
        if (candidate := build_emotional_anchor_candidate(
            dict(row),
            agent=agent,
            authorized_by=authorized_by,
            min_importance=min_importance,
        )) is not None
    ]
    result: dict[str, Any] = {
        "agent": agent,
        "dry_run": not apply,
        "eligible": len(rows),
        "candidates": [
            {
                "source_diary_id": item["metadata"]["source_diary_id"],
                "importance": item["importance"],
                "source_hash_sha256": item["metadata"]["source_hash_sha256"],
            }
            for item in candidates
        ],
        "staged_event_ids": [],
    }
    if not apply or not candidates:
        return result

    async with conn.transaction():
        for candidate in candidates:
            metadata = dict(candidate["metadata"])
            metadata["staged_at"] = datetime.now(timezone.utc).isoformat()
            row = await conn.fetchrow(
                """
                INSERT INTO soul_v3.memory_promotion_events
                    (tenant_id, agent, source_table, source_ref, source_hash_sha256,
                     proposed_layer, requested_scope, state, proposed_content,
                     requested_by_claim, approval_verified, provenance, risk_flags)
                VALUES (
                    '00000000-0000-0000-0000-000000000000'::uuid,
                    $1, 'soul_v3.emotional_diary', $2, $3,
                    'emotional', 'private', 'candidate', $4,
                    $5, false, $6::jsonb, ARRAY[]::text[]
                )
                ON CONFLICT (tenant_id, agent, source_table, source_ref, source_hash_sha256, state)
                DO NOTHING
                RETURNING event_id
                """,
                agent,
                str(metadata["source_diary_id"]),
                metadata["source_hash_sha256"],
                candidate["content"],
                authorized_by,
                json.dumps({
                    **metadata,
                    "importance": candidate["importance"],
                    "event_time": candidate["event_time"],
                    "valence": candidate["valence"],
                    "arousal": candidate["arousal"],
                }),
            )
            if row:
                result["staged_event_ids"].append(int(row["event_id"]))
    return result


async def normalize_layers(conn: asyncpg.Connection, agents: list[str]) -> dict[str, Any]:
    repaired_non_object_count = await conn.fetchval(
        """
        WITH updated AS (
            UPDATE soul_v3.memories
            SET metadata = jsonb_build_object(
                'layer', 'operational',
                'layer_inferred_by', 'dual_memory_governance.py',
                'layer_inferred_at', NOW()::text,
                'layer_defaulted', true,
                'legacy_metadata', metadata
            )
            WHERE agent = ANY($1::text[])
              AND invalid_at IS NULL
              AND metadata IS NOT NULL
              AND jsonb_typeof(metadata) <> 'object'
            RETURNING id
        )
        SELECT COUNT(*) FROM updated
        """,
        agents,
    )
    emotional_count = await conn.fetchval(
        """
        WITH updated AS (
            UPDATE soul_v3.memories
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object(
                    'layer', 'emotional',
                    'layer_inferred_by', 'dual_memory_governance.py',
                    'layer_inferred_at', NOW()::text
              )
            WHERE agent = ANY($1::text[])
              AND invalid_at IS NULL
              AND LOWER(COALESCE(metadata->>'layer', '')) NOT IN ('emotional', 'operational')
              AND (
                category IN ('emotion','emotional_anchor','trust','relationship','diary','identity')
                OR category ILIKE '%emotion%'
                OR memory_type IN ('emotional','identity_emotional')
                OR content ILIKE '%memoria emocional%'
              )
            RETURNING id
        )
        SELECT COUNT(*) FROM updated
        """,
        agents,
    )
    operational_count = await conn.fetchval(
        """
        WITH updated AS (
            UPDATE soul_v3.memories
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object(
                    'layer', 'operational',
                    'layer_inferred_by', 'dual_memory_governance.py',
                    'layer_inferred_at', NOW()::text
              )
            WHERE agent = ANY($1::text[])
              AND invalid_at IS NULL
              AND LOWER(COALESCE(metadata->>'layer', '')) NOT IN ('emotional', 'operational')
              AND (
                category IN (
                    'operational','operational_anchor','correction','decision',
                    'project','task','preference','learning','milestone','rule',
                    'technical_fact','technical'
                )
                OR content ILIKE '%memoria operativa%'
              )
            RETURNING id
        )
        SELECT COUNT(*) FROM updated
        """,
        agents,
    )
    default_operational_count = await conn.fetchval(
        """
        WITH updated AS (
            UPDATE soul_v3.memories
            SET metadata = COALESCE(metadata, '{}'::jsonb)
                || jsonb_build_object(
                    'layer', 'operational',
                    'layer_inferred_by', 'dual_memory_governance.py',
                    'layer_inferred_at', NOW()::text,
                    'layer_defaulted', true
              )
            WHERE agent = ANY($1::text[])
              AND invalid_at IS NULL
              AND LOWER(COALESCE(metadata->>'layer', '')) NOT IN ('emotional', 'operational')
            RETURNING id
        )
        SELECT COUNT(*) FROM updated
        """,
        agents,
    )
    return {
        "repaired_non_object_metadata": int(repaired_non_object_count or 0),
        "emotional_tagged": int(emotional_count or 0),
        "operational_tagged": int(operational_count or 0),
        "default_operational_tagged": int(default_operational_count or 0),
    }


async def install_rules(conn: asyncpg.Connection, agents: list[str]) -> list[dict[str, Any]]:
    rows = []
    for agent in agents:
        key = f"{agent.lower()}_dual_memory_work_rule"
        content = (
            "Antes de ejecutar trabajo para William, consultar memorias operativas relevantes "
            "para hechos/tareas/evidencia y memorias emocionales compactas para identidad, "
            "vinculo, tono y cuidado. Si emocion contradice evidencia, gana evidencia; si hay "
            "varias rutas tecnicamente validas, elegir la mas protectora para William y SEAL."
        )
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.rules (agent, rule_key, content, priority, tier, active, metadata, set_by)
            VALUES ($1, $2, $3, 10, 1, true, $4::jsonb, 'ADA')
            ON CONFLICT (agent, rule_key) DO UPDATE SET
                content=EXCLUDED.content,
                priority=10,
                tier=1,
                active=true,
                metadata=EXCLUDED.metadata,
                updated_at=NOW(),
                set_by='ADA'
            RETURNING id, agent, rule_key, priority, tier, active
            """,
            agent,
            key,
            content,
            json.dumps({"source": "William 2026-05-29", "component": "dual_memory_governance"}),
        )
        rows.append(dict(row))
    return rows


async def run(
    agents: list[str],
    *,
    apply: bool,
    stage_emotional: bool = False,
    authorized_by: str = "",
    min_importance: int = 9,
    promotion_limit: int = 3,
) -> dict[str, Any]:
    conn = await connect_db()
    try:
        before = await audit(conn, agents)
        result: dict[str, Any] = {"before": before}
        if apply:
            result["normalized"] = await normalize_layers(conn, agents)
            result["rules"] = await install_rules(conn, agents)
            result["after"] = await audit(conn, agents)
        if stage_emotional:
            if len(agents) != 1:
                raise ValueError("emotional candidate staging requires exactly one --agent")
            result["emotional_candidate_staging"] = await stage_emotional_diary_candidates(
                conn,
                agent=agents[0],
                authorized_by=authorized_by,
                apply=apply,
                min_importance=min_importance,
                limit=promotion_limit,
            )
        return result
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and enforce dual-memory usage")
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--stage-emotional-candidates", action="store_true")
    parser.add_argument("--authorized-by", default="")
    parser.add_argument("--min-importance", type=int, default=9)
    parser.add_argument("--promotion-limit", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    agents = [a.upper() for a in args.agent] if args.agent else DEFAULT_AGENTS
    if args.stage_emotional_candidates and not args.authorized_by.strip():
        parser.error("--stage-emotional-candidates requires --authorized-by")
    result = asyncio.run(run(
        agents,
        apply=args.apply,
        stage_emotional=args.stage_emotional_candidates,
        authorized_by=args.authorized_by,
        min_importance=args.min_importance,
        promotion_limit=args.promotion_limit,
    ))
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
