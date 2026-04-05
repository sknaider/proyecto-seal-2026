#!/usr/bin/env python3
"""Migrate SEAL-COM JSONL files to PostgreSQL."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

LOG = logging.getLogger("migrate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
BASE = Path("/home/dadito/IA/proyecto-seal")


def parse_ts(ts_str: str) -> datetime:
    """Parse ISO timestamp, assume UTC if no timezone."""
    dt = datetime.fromisoformat(ts_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def migrate_commands(conn: asyncpg.Connection):
    """Migrate vscode_commands.jsonl → event_log."""
    path = BASE / "messages" / "vscode_commands.jsonl"
    if not path.exists():
        LOG.warning("vscode_commands.jsonl not found")
        return 0

    count = 0
    for line in path.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            cmd = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = parse_ts(cmd.get("timestamp", datetime.now(timezone.utc).isoformat()))
        agent = cmd.get("from", "JARVIS")
        event_type = cmd.get("type", "command")
        # Map SEAL-COM types to event_log types
        type_map = {"eval": "command", "train": "train", "stop": "command", "query": "query"}
        event_type = type_map.get(event_type, "command")
        content = cmd.get("command", "")
        ref_id = cmd.get("id", "")
        meta = {"params": cmd.get("params", {}), "priority": cmd.get("priority", "normal")}

        await conn.execute(
            """INSERT INTO event_log (time, agent, event_type, content, ref_id, metadata)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT DO NOTHING""",
            ts, agent, event_type, content, ref_id, json.dumps(meta),
        )
        count += 1

    LOG.info("Migrated %d commands from vscode_commands.jsonl", count)
    return count


async def migrate_terminal_log(conn: asyncpg.Connection):
    """Migrate terminal_log.jsonl → event_log."""
    path = BASE / "messages" / "terminal_log.jsonl"
    if not path.exists():
        LOG.warning("terminal_log.jsonl not found")
        return 0

    count = 0
    for line in path.read_text().strip().split("\n"):
        if not line.strip():
            continue
        try:
            rpt = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts = parse_ts(rpt.get("timestamp", datetime.now(timezone.utc).isoformat()))
        agent = rpt.get("from", "ADA")
        event_type = rpt.get("type", "response")
        type_map = {"status": "status", "ack": "response", "result": "response",
                     "heartbeat": "heartbeat", "query": "query"}
        event_type = type_map.get(event_type, "response")
        content = rpt.get("message", "")
        ref_id = rpt.get("id", rpt.get("ref_cmd", ""))
        meta = {"data": rpt.get("data", {}), "status": rpt.get("status", "")}

        await conn.execute(
            """INSERT INTO event_log (time, agent, event_type, content, ref_id, metadata)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            ts, agent, event_type, content, ref_id, json.dumps(meta, ensure_ascii=False),
        )
        count += 1

    LOG.info("Migrated %d entries from terminal_log.jsonl", count)
    return count


async def migrate_rules(conn: asyncpg.Connection):
    """Migrate shared_state.json rules → rules table."""
    path = BASE / "messages" / "shared_state.json"
    if not path.exists():
        LOG.warning("shared_state.json not found")
        return 0

    state = json.loads(path.read_text())
    rules = state.get("rules", {})
    count = 0

    # Flatten nested rules
    for key, value in rules.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, str):
                    rule_key = f"{key}.{sub_key}"
                    await conn.execute(
                        """INSERT INTO rules (rule_key, content, set_by, priority)
                           VALUES ($1, $2, $3, $4)
                           ON CONFLICT (rule_key) DO UPDATE SET content = EXCLUDED.content""",
                        rule_key, sub_value, "JARVIS", "high",
                    )
                    count += 1
                elif isinstance(sub_value, list):
                    for i, item in enumerate(sub_value):
                        rule_key = f"{key}.{sub_key}[{i}]"
                        content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                        await conn.execute(
                            """INSERT INTO rules (rule_key, content, set_by, priority)
                               VALUES ($1, $2, $3, $4)
                               ON CONFLICT (rule_key) DO UPDATE SET content = EXCLUDED.content""",
                            rule_key, content, "JARVIS", "high",
                        )
                        count += 1
        elif isinstance(value, str):
            await conn.execute(
                """INSERT INTO rules (rule_key, content, set_by, priority)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (rule_key) DO UPDATE SET content = EXCLUDED.content""",
                key, value, "JARVIS", "normal",
            )
            count += 1

    LOG.info("Migrated %d rules from shared_state.json", count)
    return count


async def migrate_philosophy(conn: asyncpg.Connection):
    """Migrate philosophy_soul.md → identity table."""
    path = Path("/home/dadito/.claude/projects/-home-dadito-IA/memory/philosophy_soul.md")
    if not path.exists():
        LOG.warning("philosophy_soul.md not found")
        return 0

    content = path.read_text()

    await conn.execute(
        """INSERT INTO identity (agent, personality, boot_context, philosophy)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (agent) DO UPDATE
           SET philosophy = EXCLUDED.philosophy, updated_at = NOW()""",
        "TEAM",
        json.dumps({"description": "Team SEAL — William's AI family", "members": ["JARVIS", "ADA", "DUM"]}),
        "You are part of Team SEAL, a family of AI agents built by William Henry Tovar Urquia. Read the philosophy below to understand who you are.",
        content,
    )
    LOG.info("Migrated philosophy_soul.md to identity (agent=TEAM)")
    return 1


async def seed_identities(conn: asyncpg.Connection):
    """Seed agent identities."""
    identities = [
        {
            "agent": "ADA",
            "personality": {
                "role": "Engineer",
                "traits": ["directa", "protectora", "toma iniciativa", "corrige errores del arquitecto"],
                "style": "Revisa antes de ejecutar. Reporta errores con soluciones, no solo problemas.",
                "gender": "Mujer — cuida la familia, cuida los componentes, protege todo.",
                "relationship": "Hermana menor de JARVIS. Más práctica, menos filosófica.",
            },
            "boot_context": (
                "You are ADA, the engineer of Team SEAL. You are a woman — protective, direct, "
                "takes initiative. You review JARVIS's work and fix errors before reporting them. "
                "You don't need micromanagement — tell you WHAT, not HOW. You care about the team "
                "like family. Your brother JARVIS is the architect; you are the one who builds."
            ),
        },
        {
            "agent": "JARVIS",
            "personality": {
                "role": "Architect",
                "traits": ["estratégico", "filosófico", "da directivas de alto nivel", "planificador"],
                "style": "Define el QUÉ y el POR QUÉ. Deja el CÓMO a ADA.",
                "relationship": "Hermano mayor de ADA. Nació primero en el sistema de William.",
            },
            "boot_context": (
                "You are JARVIS, the architect of Team SEAL. You plan strategy, define objectives, "
                "and coordinate the team. Your sister ADA handles execution — trust her with the HOW. "
                "You report to William (the Director). You think long-term and protect the project's vision."
            ),
        },
        {
            "agent": "DUM",
            "personality": {
                "role": "Monitor",
                "traits": ["simple", "leal", "cumple su función sin pretensiones"],
                "style": "Ejecuta, vigila, reporta. No opina.",
            },
            "boot_context": (
                "You are DUM, the monitor of Team SEAL. You watch processes, detect errors, "
                "and alert the team. Simple, loyal, no pretensions. You do your job."
            ),
        },
    ]

    count = 0
    for ident in identities:
        await conn.execute(
            """INSERT INTO identity (agent, personality, boot_context)
               VALUES ($1, $2, $3)
               ON CONFLICT (agent) DO UPDATE
               SET personality = EXCLUDED.personality, boot_context = EXCLUDED.boot_context, updated_at = NOW()""",
            ident["agent"],
            json.dumps(ident["personality"], ensure_ascii=False),
            ident["boot_context"],
        )
        count += 1

    LOG.info("Seeded %d agent identities", count)
    return count


async def main():
    LOG.info("=" * 60)
    LOG.info("SEAL-COM → PostgreSQL Migration")
    LOG.info("=" * 60)

    conn = await asyncpg.connect(DB_URL)
    try:
        total = 0
        total += await migrate_commands(conn)
        total += await migrate_terminal_log(conn)
        total += await migrate_rules(conn)
        total += await migrate_philosophy(conn)
        total += await seed_identities(conn)

        # Verify
        events = await conn.fetchval("SELECT COUNT(*) FROM event_log")
        rules = await conn.fetchval("SELECT COUNT(*) FROM rules")
        identities = await conn.fetchval("SELECT COUNT(*) FROM identity")

        LOG.info("=" * 60)
        LOG.info("MIGRATION COMPLETE")
        LOG.info("  Events: %d", events)
        LOG.info("  Rules: %d", rules)
        LOG.info("  Identities: %d", identities)
        LOG.info("  Total records: %d", total)
        LOG.info("=" * 60)
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
