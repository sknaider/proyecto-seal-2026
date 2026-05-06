#!/usr/bin/env python3
"""Migrate SEAL skill content to SEAL native skills directory.

Copies SKILL.md files from ~/.soul/skills/ to tools/skills/,
then updates the soul_v3.skills table to point to the new SEAL paths.

This converts skill entries from EN PROGRESO (content in SEAL)
to DEMOSTRABLE (content owned natively by SEAL).

Run:
    python3 tools/skills/migrate_soul_skills.py [--dry-run]
"""

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "memory"))

HERMES_SKILLS = Path.home() / ".soul" / "skills"
SEAL_SKILLS = Path(__file__).resolve().parent


async def migrate(dry_run: bool = False) -> int:
    from mcp_server_v3 import get_pool

    pool = await get_pool()
    copied = 0
    updated = 0
    skipped = 0
    errors: list[str] = []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, skill_path FROM skills WHERE skill_path LIKE $1",
            str(HERMES_SKILLS) + "%",
        )
        print(f"Found {len(rows)} skills pointing to SEAL")

        for row in rows:
            soul_path = Path(row["skill_path"])
            if not soul_path.exists():
                skipped += 1
                print(f"  [SKIP] {row['name']} — {soul_path} not found")
                continue

            try:
                rel = soul_path.relative_to(HERMES_SKILLS)
            except ValueError:
                skipped += 1
                print(f"  [SKIP] {row['name']} — path outside HERMES_SKILLS")
                continue

            seal_path = SEAL_SKILLS / rel
            seal_path.parent.mkdir(parents=True, exist_ok=True)

            if not dry_run:
                shutil.copy2(soul_path, seal_path)
                copied += 1

                await conn.execute(
                    "UPDATE skills SET skill_path = $1 WHERE id = $2",
                    str(seal_path),
                    row["id"],
                )
                updated += 1
                print(f"  [OK] {row['name']} → tools/skills/{rel}")
            else:
                print(f"  [DRY] {row['name']}: {soul_path} → tools/skills/{rel}")
                copied += 1

    await pool.close()

    print(f"\n{'DRY RUN — ' if dry_run else ''}Done: {copied} copied, {updated} DB rows updated, {skipped} skipped, {len(errors)} errors")
    return 0 if not errors else 1


def main():
    parser = argparse.ArgumentParser(description="Migrate SEAL skills to SEAL native")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without making changes")
    args = parser.parse_args()
    return asyncio.run(migrate(dry_run=args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
