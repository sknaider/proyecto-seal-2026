#!/usr/bin/env python3
"""Verify that every active SOUL DB skill_path resolves on the host."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SQL = """SELECT id||E'\\t'||agent||E'\\t'||name||E'\\t'||skill_path
FROM soul_v3.skills
WHERE invalid_at IS NULL AND skill_path IS NOT NULL
ORDER BY id"""


def parse_rows(text: str) -> list[tuple[int, str, str, str]]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        skill_id, agent, name, path = line.split("\t", 3)
        rows.append((int(skill_id), agent, name, path))
    return rows


def validate() -> dict:
    proc = subprocess.run(
        ["docker", "exec", "-i", "seal-memory-db", "psql", "-U", "seal", "-d", "seal_memory", "-At", "-c", SQL],
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        return {"schema": "soul-skill-registry-v1", "passed": False, "error": (proc.stderr or proc.stdout).strip(), "counts": {"active_paths": 0, "broken": 0}, "broken": []}
    broken = []
    rows = parse_rows(proc.stdout)
    for skill_id, agent, name, raw_path in rows:
        path = Path(raw_path)
        if not path.is_absolute():
            path = REPO / path
        if not path.exists():
            broken.append({"id": skill_id, "agent": agent, "name": name, "path": str(path)})
    return {
        "schema": "soul-skill-registry-v1",
        "passed": not broken,
        "counts": {"active_paths": len(rows), "broken": len(broken)},
        "broken": broken,
    }


def main() -> int:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
