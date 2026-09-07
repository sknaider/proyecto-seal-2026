#!/usr/bin/env python3
"""Reconcile filesystem skills into the SOUL registry as review-pending rows.

The filesystem remains the execution source.  This reconciler only creates the
minimum catalog row required for telemetry; it never enables boot loading or
marks a skill reviewed.  Repeated runs are idempotent by active name/path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))

from seal_secrets import pg_dsn


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    values: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if ":" not in line or line[:1].isspace():
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def describe_skill(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    meta = _frontmatter(text)
    name = meta.get("name") or path.parent.name
    description = meta.get("description")
    if not description:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith(("#", "---", ">")):
                description = stripped
                break
    return {
        "name": name,
        "description": description or f"Filesystem skill {name}",
        "skill_path": str(path.resolve()),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def discover_skill_files(roots: Iterable[Path]) -> list[Path]:
    return sorted(
        {
            path.resolve()
            for root in roots
            if root.exists()
            for path in root.rglob("SKILL.md")
            if path.is_file()
        }
    )


async def reconcile(
    conn: asyncpg.Connection,
    roots: Iterable[Path],
    *,
    apply: bool,
) -> dict[str, Any]:
    await conn.execute("SELECT pg_advisory_xact_lock(hashtext('seal_skill_registry_sync_v1'))")
    rows = await conn.fetch(
        "SELECT name, skill_path FROM soul_v3.skills WHERE invalid_at IS NULL"
    )
    names = {str(row["name"]) for row in rows}
    paths = {
        str(Path(row["skill_path"]).expanduser().resolve())
        for row in rows
        if row["skill_path"]
    }
    candidates = [
        item
        for item in (describe_skill(path) for path in discover_skill_files(roots))
        if item["name"] not in names and item["skill_path"] not in paths
    ]
    inserted: list[dict[str, Any]] = []
    if apply:
        for item in candidates:
            row = await conn.fetchrow(
                """
                INSERT INTO soul_v3.skills
                    (agent,name,description,skill_path,type,pending_review,
                     execution_environment,boot_load,metadata)
                SELECT 'TEAM',$1::varchar,$2::text,$3::text,'guiding',true,'restricted',false,$4::jsonb
                WHERE NOT EXISTS (
                    SELECT 1 FROM soul_v3.skills
                    WHERE invalid_at IS NULL AND (name=$1::varchar OR skill_path=$3::text)
                )
                RETURNING id,name,skill_path,pending_review,boot_load
                """,
                item["name"],
                item["description"],
                item["skill_path"],
                json.dumps(
                    {
                        "owner": "SEAL",
                        "registry_source": "filesystem_reconcile_v1",
                        "activation_policy": "manual_or_retrieval_only",
                        "content_sha256": item["content_sha256"],
                    },
                    ensure_ascii=False,
                ),
            )
            if row is not None:
                inserted.append(dict(row))
    return {
        "ok": True,
        "mode": "apply" if apply else "preview",
        "filesystem_count": len(discover_skill_files(roots)),
        "active_registry_before": len(rows),
        "candidate_count": len(candidates),
        "inserted_count": len(inserted),
        "candidates": [item["name"] for item in candidates],
        "inserted": inserted,
        "review_state": "pending_review",
        "boot_load": False,
    }


async def main_async(apply: bool) -> int:
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        async with conn.transaction():
            result = await reconcile(
                conn,
                (ROOT / "skills", ROOT / ".agents/skills"),
                apply=apply,
            )
    finally:
        await conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
