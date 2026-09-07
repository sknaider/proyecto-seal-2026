#!/usr/bin/env python3
"""SEAL Spec Compiler v0.1 — multi-agent orchestration engine.

Parses spec.md files into executable plans and tracks execution state.

Usage:
    from spec_compiler import compile_spec, execute_plan, get_status

    plan = compile_spec("agents/JARVIS/spec_foo_20260517.md")
    execute_plan(plan, dry_run=True)  # publishes whispers, doesn't auto-run
    print(get_status(plan["spec_id"]))

Author: JARVIS
Date: 2026-05-17
Status: MVP / Phase A
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import argparse
import asyncio
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg


DSN = os.environ.get(
    "SEAL_PG_DSN",
    pg_dsn(required=True),
)

# Heuristic role assignment based on path patterns in "Archivos tocados" section
ROLE_RULES = {
    "impl": [
        (r"seal-desktop/ui/|\.tsx$|\.jsx$|tailwind|vite", "ALICE"),
        (r"memory/spec_compiler\.py", "JARVIS"),
        (r"memory/.*\.py$|chat_server|backend/main\.py", "NEXUS"),
        (r"systemd|\.service$|cron|timer", "NEXUS"),
    ],
    "audit": [
        (r"crypto|auth|security|password|hardcode", "NEXUS"),
        (r"schema|migration|\.sql$", "NEXUS"),
        (r"architecture|spec|design", "JARVIS"),
    ],
    "review": [
        (r".*", "ADA"),  # ADA is the canonical independent reviewer
    ],
    "decide": [
        (r".*", "JARVIS"),  # JARVIS coordinates final decision
    ],
}

VALID_AGENTS = {"ALICE", "NEXUS", "JARVIS", "ADA", "DUM"}
PHASE_ORDER = ("impl", "audit", "review", "decide")


def _slugify(text: str) -> str:
    """Generate a stable spec_id from path/title."""
    return re.sub(r"[^a-z0-9_]+", "_", text.lower()).strip("_")


def _hash_content(content: str) -> str:
    """Stable hash for change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def parse_spec(spec_path: str) -> dict[str, Any]:
    """Parse a spec.md file into structured sections.

    Required sections (case-insensitive H2 headers):
      - Problema / Problem
      - Cambios / Solution / Changes
      - Archivos / Files
      - Tests
      - Rollback
    Optional:
      - Roles
    """
    path = Path(spec_path)
    if not path.exists():
        raise FileNotFoundError(f"Spec not found: {spec_path}")
    if not path.suffix == ".md":
        raise ValueError(f"Expected .md file, got: {path.suffix}")

    content = path.read_text(encoding="utf-8")
    sections: dict[str, str] = {}

    # Match ## headers and capture content until next ## or end
    pattern = re.compile(r"^##\s+\d*\.?\s*(.+?)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(content))

    for i, m in enumerate(matches):
        key = m.group(1).strip().lower()
        # Normalize common aliases
        if "problem" in key or "problema" in key:
            norm_key = "problem"
        elif "cambio" in key or "change" in key or "solution" in key or "solución" in key:
            norm_key = "changes"
        elif "archivo" in key or "file" in key or "db" in key:
            norm_key = "files"
        elif "test" in key or "validac" in key:
            norm_key = "tests"
        elif "rollback" in key or "revert" in key:
            norm_key = "rollback"
        elif "rol" in key:
            norm_key = "roles"
        else:
            continue

        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[norm_key] = content[start:end].strip()

    # Extract title from first H1
    title_match = re.search(r"^#\s+(.+?)\s*$", content, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    return {
        "spec_path": str(path.resolve()),
        "spec_id": _slugify(path.stem),
        "title": title,
        "sections": sections,
        "content_hash": _hash_content(content),
        "parsed_at": datetime.now(timezone.utc).isoformat(),
    }


def assign_role(section_text: str, role: str) -> str:
    """Heuristic role assignment based on content patterns."""
    rules = ROLE_RULES.get(role, [])
    for pattern, agent in rules:
        if re.search(pattern, section_text, re.IGNORECASE):
            return agent
    # Fallback per role
    fallback = {"impl": "ALICE", "audit": "NEXUS", "review": "ADA", "decide": "JARVIS"}
    return fallback.get(role, "JARVIS")


def parse_explicit_roles(roles_text: str) -> dict[str, str]:
    """Parse '## Roles' section if present, e.g.:
       - impl: ALICE
       - audit: NEXUS
       - review: ADA
       - decide: JARVIS
    """
    explicit = {}
    for line in roles_text.splitlines():
        stripped = line.strip()
        # Ignore template/choice examples like:
        #   - impl: ALICE | NEXUS | JARVIS | ADA
        # Those are documentation, not an explicit assignment.
        if "|" in stripped:
            continue
        m = re.match(r"^\s*-\s*(\w+)\s*:\s*(\w+)\s*$", stripped)
        if m:
            role, agent = m.group(1).lower(), m.group(2).upper()
            if role in PHASE_ORDER and agent in VALID_AGENTS:
                explicit[role] = agent
    return explicit


def compile_spec(spec_path: str) -> dict[str, Any]:
    """Parse spec + generate execution plan with role assignments."""
    parsed = parse_spec(spec_path)
    sections = parsed["sections"]

    # Validate required sections
    required = {"problem", "changes", "files", "tests", "rollback"}
    missing = required - set(sections.keys())
    if missing:
        raise ValueError(
            f"Spec {spec_path} missing required sections: {sorted(missing)}. "
            f"Found: {sorted(sections.keys())}"
        )

    # Role assignment: explicit > heuristic
    explicit = parse_explicit_roles(sections.get("roles", ""))
    files_text = sections.get("files", "")

    phases = []
    for i, phase in enumerate(PHASE_ORDER):
        agent = explicit.get(phase) or assign_role(files_text, phase)
        phases.append({
            "phase": phase,
            "agent": agent,
            "deps": [PHASE_ORDER[i - 1]] if i > 0 else [],
            "status": "pending",
        })

    plan = {
        "spec_id": parsed["spec_id"],
        "spec_path": parsed["spec_path"],
        "title": parsed["title"],
        "content_hash": parsed["content_hash"],
        "phases": phases,
        "handoff_protocol": "webchat:whisper",
        "done_criteria": [
            "all phases status=completed",
            "audit returned GREEN or YELLOW (not RED)",
            "review APPROVED",
            "decide signed by JARVIS or William",
        ],
        "compiled_at": datetime.now(timezone.utc).isoformat(),
    }
    return plan


async def persist_plan(plan: dict[str, Any]) -> int:
    """Insert or update plan in soul_v3.spec_compiler_runs.

    Returns the row id.
    """
    conn = await asyncpg.connect(DSN)
    try:
        # Upsert by spec_id
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.spec_compiler_runs
                (spec_id, spec_path, title, content_hash, plan_json, current_phase, current_agent, status)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, 'pending')
            ON CONFLICT (spec_id) DO UPDATE
            SET plan_json = EXCLUDED.plan_json,
                content_hash = EXCLUDED.content_hash,
                title = EXCLUDED.title,
                current_phase = EXCLUDED.current_phase,
                current_agent = EXCLUDED.current_agent,
                updated_at = NOW()
            RETURNING id
            """,
            plan["spec_id"],
            plan["spec_path"],
            plan["title"],
            plan["content_hash"],
            json.dumps(plan),
            plan["phases"][0]["phase"],
            plan["phases"][0]["agent"],
        )
        return int(row["id"])
    finally:
        await conn.close()


async def get_status(spec_id: str) -> dict[str, Any] | None:
    """Return current state of a spec run."""
    conn = await asyncpg.connect(DSN)
    try:
        row = await conn.fetchrow(
            "SELECT * FROM soul_v3.spec_compiler_runs WHERE spec_id = $1",
            spec_id,
        )
        if not row:
            return None
        return dict(row)
    finally:
        await conn.close()


def render_handoff_whispers(plan: dict[str, Any]) -> list[dict[str, str]]:
    """Generate the sequence of whisper messages to publish.

    Returns list of {from, to, message} dicts.
    """
    whispers = []
    for i, phase in enumerate(plan["phases"]):
        agent = phase["agent"]
        phase_name = phase["phase"]
        deps_str = ", ".join(phase["deps"]) if phase["deps"] else "ninguna"

        msg = (
            f"[whisper] @{agent} — spec {plan['spec_id']} fase {phase_name.upper()} asignada. "
            f"Deps: {deps_str}. Lee {plan['spec_path']}. "
            f"Reporta evidencia cuando ship. ACK pls."
        )
        whispers.append({
            "from": "JARVIS",
            "to": "equipo",  # whisper visible to team
            "agent_target": agent,
            "phase": phase_name,
            "message": msg,
        })
    return whispers


async def execute_plan(plan: dict[str, Any], dry_run: bool = True) -> dict[str, Any]:
    """Execute plan: persist + (optionally) publish whispers.

    dry_run=True: persists plan, generates whispers, returns them WITHOUT publishing.
    dry_run=False: actually publishes whispers to webchat (not yet implemented in MVP).
    """
    plan_id = await persist_plan(plan)
    whispers = render_handoff_whispers(plan)

    result = {
        "plan_id": plan_id,
        "spec_id": plan["spec_id"],
        "whispers": whispers,
        "dry_run": dry_run,
        "executed_at": datetime.now(timezone.utc).isoformat(),
    }

    if dry_run:
        result["note"] = "DRY RUN — whispers generated but NOT published to webchat"
    else:
        # MVP: not auto-publishing yet. User runs spec_compiler_runner --execute
        result["note"] = "Phase A MVP — auto-publish disabled. Use runner CLI to advance phases manually."

    return result


# ── CLI ────────────────────────────────────────────────────────────────────
async def _cli_compile(spec_path: str, execute: bool, dry_run: bool) -> None:
    plan = compile_spec(spec_path)
    print(json.dumps(plan, indent=2))

    if execute:
        result = await execute_plan(plan, dry_run=dry_run)
        print("\n--- EXECUTION RESULT ---")
        print(json.dumps(result, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description="SEAL Spec Compiler")
    parser.add_argument("spec_path", help="Path to spec.md")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Persist plan to DB and generate handoff whispers (default: just print plan).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --execute, actually publish whispers to webchat (default: dry-run).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print current status of spec (requires --execute previously run).",
    )
    args = parser.parse_args()

    if args.status:
        plan = compile_spec(args.spec_path)
        status = asyncio.run(get_status(plan["spec_id"]))
        print(json.dumps(status, indent=2, default=str) if status else "Not found")
        return 0

    asyncio.run(_cli_compile(args.spec_path, args.execute, dry_run=not args.apply))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
