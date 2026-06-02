#!/usr/bin/env python3
"""Dual-memory governance for SEAL agents.

Operational memories drive execution. Emotional memories preserve identity,
relationship, care and risk posture. This script makes the split measurable by
normalizing metadata.layer and installing critical rules for each agent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
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
    }


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


async def run(agents: list[str], *, apply: bool) -> dict[str, Any]:
    conn = await connect_db()
    try:
        before = await audit(conn, agents)
        result: dict[str, Any] = {"before": before}
        if apply:
            result["normalized"] = await normalize_layers(conn, agents)
            result["rules"] = await install_rules(conn, agents)
            result["after"] = await audit(conn, agents)
        return result
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit and enforce dual-memory usage")
    parser.add_argument("--agent", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    agents = [a.upper() for a in args.agent] if args.agent else DEFAULT_AGENTS
    result = asyncio.run(run(agents, apply=args.apply))
    print(json.dumps(result, indent=2 if args.json else None, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
