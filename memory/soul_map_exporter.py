#!/usr/bin/env python3
"""Read-only SOUL DB to Obsidian/Markdown memory map exporter.

SOUL DB remains canonical. This script creates a human-readable vault snapshot
for navigation and review; it never writes to the database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import asyncpg

from seal_secrets import pg_dsn


KNOWN_LINKS: tuple[tuple[str, str], ...] = (
    ("dadito-laptop", "Machines/dadito-laptop"),
    ("daditogamer", "Machines/daditogamer"),
    ("dgx spark", "Machines/DGX Spark"),
    ("spark", "Machines/DGX Spark"),
    ("codex app", "Systems/Codex App Windows"),
    ("codex", "Systems/Codex"),
    ("mcp", "Systems/SOUL MCP"),
    ("bridge", "Systems/ADA Codex Bridge"),
    ("webchat", "Systems/WebChat"),
    ("william", "People/William"),
    ("henry", "People/Henry"),
    ("ada", "Agents/ADA"),
    ("jarvis", "Agents/JARVIS"),
    ("alice", "Agents/ALICE"),
    ("nexus", "Agents/NEXUS"),
    ("dum", "Agents/DUM"),
)

LAYER_ORDER = {"operational": 0, "emotional": 1, "<none>": 2}


@dataclass(frozen=True)
class MemoryRow:
    id: int
    agent: str
    layer: str
    category: str
    memory_type: str
    importance: int
    created_at: datetime | None
    source: str | None
    metadata: dict[str, Any]
    content: str


def slugify(value: str, *, fallback: str = "item", max_len: int = 72) -> str:
    """Return a filesystem-safe ASCII-ish slug."""
    text = value.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if not text:
        text = fallback
    return text[:max_len].strip("-") or fallback


def yaml_scalar(value: Any) -> str:
    if value is None:
        return '""'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int | float):
            rendered = str(value)
        elif isinstance(value, list):
            rendered = "[" + ", ".join(yaml_scalar(v) for v in value) + "]"
        else:
            rendered = yaml_scalar(value)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def extract_links(content: str) -> list[str]:
    lower = content.lower()
    seen: set[str] = set()
    links: list[str] = []
    for needle, target in KNOWN_LINKS:
        if needle in lower and target not in seen:
            seen.add(target)
            links.append(target)
    return links


def wikilinks(targets: Iterable[str]) -> str:
    links = [f"[[{target}]]" for target in targets]
    return " ".join(links)


def row_from_record(record: asyncpg.Record) -> MemoryRow:
    metadata_raw = record.get("metadata") or {}
    if isinstance(metadata_raw, str):
        try:
            metadata = json.loads(metadata_raw)
        except json.JSONDecodeError:
            metadata = {"legacy_metadata": metadata_raw}
    else:
        metadata = dict(metadata_raw)
    layer = str(metadata.get("layer") or record.get("layer") or "<none>").lower()
    if layer not in {"operational", "emotional"}:
        layer = "<none>"
    return MemoryRow(
        id=int(record["id"]),
        agent=str(record["agent"]),
        layer=layer,
        category=str(record.get("category") or ""),
        memory_type=str(record.get("memory_type") or ""),
        importance=int(record.get("importance") or 0),
        created_at=record.get("created_at"),
        source=record.get("source"),
        metadata=metadata,
        content=str(record.get("content") or ""),
    )


def memory_note_path(root: Path, row: MemoryRow) -> Path:
    prefix = f"{row.id}-{slugify(row.category or row.content[:48])}.md"
    return root / "Memories" / row.agent / row.layer / prefix


def render_memory_note(row: MemoryRow) -> str:
    created = row.created_at.isoformat() if row.created_at else ""
    links = extract_links(row.content)
    fields = {
        "soul_id": row.id,
        "agent": row.agent,
        "layer": row.layer,
        "category": row.category,
        "memory_type": row.memory_type,
        "importance": row.importance,
        "created_at": created,
        "source": row.source or "",
        "canonical": True,
        "generated_by": "memory/soul_map_exporter.py",
        "links": links,
    }
    body = [
        frontmatter(fields),
        f"# SOUL Memory {row.id}",
        "",
        f"**Agent:** [[Agents/{row.agent}]]",
        f"**Layer:** [[Layers/{row.layer}]]",
        f"**Category:** `{row.category}`",
        f"**Importance:** `{row.importance}`",
        "",
    ]
    if links:
        body.extend(["**Linked Map Nodes:**", "", wikilinks(links), ""])
    body.extend(["## Content", "", row.content.strip(), ""])
    return "\n".join(body)


def render_index(rows: list[MemoryRow], generated_at: datetime) -> str:
    by_layer: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    for row in rows:
        by_layer[row.layer] = by_layer.get(row.layer, 0) + 1
        by_agent[row.agent] = by_agent.get(row.agent, 0) + 1
    lines = [
        frontmatter(
            {
                "generated_at": generated_at.isoformat(),
                "canonical_source": "SOUL DB",
                "generated_by": "memory/soul_map_exporter.py",
                "memory_count": len(rows),
            }
        ),
        "# SOUL Memory Map",
        "",
        "SOUL DB is canonical. This vault is a generated navigation map.",
        "",
        "## Counts By Layer",
        "",
    ]
    for layer, count in sorted(by_layer.items(), key=lambda item: LAYER_ORDER.get(item[0], 99)):
        lines.append(f"- [[Layers/{layer}]]: {count}")
    lines.extend(["", "## Counts By Agent", ""])
    for agent, count in sorted(by_agent.items()):
        lines.append(f"- [[Agents/{agent}]]: {count}")
    lines.extend(["", "## Hot Memories", ""])
    for row in rows[:25]:
        path = memory_note_path(Path("."), row).with_suffix("")
        lines.append(f"- [[{path.as_posix()}|#{row.id}]] `{row.layer}` `{row.category}` imp={row.importance}")
    lines.append("")
    return "\n".join(lines)


def render_agent_note(agent: str, rows: list[MemoryRow]) -> str:
    layers = sorted({row.layer for row in rows}, key=lambda layer: LAYER_ORDER.get(layer, 99))
    lines = [
        frontmatter({"agent": agent, "generated_by": "memory/soul_map_exporter.py", "canonical": False}),
        f"# Agent {agent}",
        "",
        "## Layers",
        "",
    ]
    for layer in layers:
        count = sum(1 for row in rows if row.layer == layer)
        lines.append(f"- [[Layers/{layer}]]: {count}")
    lines.extend(["", "## Top Memories", ""])
    for row in rows[:20]:
        path = memory_note_path(Path("."), row).with_suffix("")
        lines.append(f"- [[{path.as_posix()}|#{row.id}]] `{row.layer}` `{row.category}` imp={row.importance}")
    lines.append("")
    return "\n".join(lines)


def render_layer_note(layer: str, rows: list[MemoryRow]) -> str:
    lines = [
        frontmatter({"layer": layer, "generated_by": "memory/soul_map_exporter.py", "canonical": False}),
        f"# Layer {layer}",
        "",
    ]
    if layer == "operational":
        lines.append("Execution memory: tasks, decisions, corrections, rules, evidence, services.")
    elif layer == "emotional":
        lines.append("Identity and relationship memory: trust, emotion, diary, continuity anchors.")
    else:
        lines.append("Unclassified memories that need governance review.")
    lines.extend(["", "## Top Memories", ""])
    for row in rows[:30]:
        path = memory_note_path(Path("."), row).with_suffix("")
        lines.append(f"- [[{path.as_posix()}|#{row.id}]] `{row.agent}` `{row.category}` imp={row.importance}")
    lines.append("")
    return "\n".join(lines)


def write_text(path: Path, content: str, *, allow_overwrite: bool) -> None:
    if path.exists() and not allow_overwrite:
        raise FileExistsError(f"{path} exists; pass --allow-overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


async def fetch_memories(conn: asyncpg.Connection, *, agents: list[str], limit: int, min_importance: int) -> list[MemoryRow]:
    query = """
        SELECT id, agent, category, memory_type, content, importance, source, metadata, created_at,
               COALESCE(metadata->>'layer', '<none>') AS layer
        FROM soul_v3.memories
        WHERE invalid_at IS NULL
          AND importance >= $1
          AND ($2::text[] IS NULL OR agent = ANY($2::text[]))
        ORDER BY importance DESC, created_at DESC, id DESC
        LIMIT $3
    """
    agent_filter = agents or None
    async with conn.transaction(readonly=True):
        records = await conn.fetch(query, min_importance, agent_filter, limit)
    return [row_from_record(record) for record in records]


async def export_vault(args: argparse.Namespace) -> dict[str, Any]:
    generated_at = datetime.now(UTC)
    root = args.out.resolve()
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rows = await fetch_memories(
            conn,
            agents=args.agent,
            limit=args.limit,
            min_importance=args.min_importance,
        )
    finally:
        await conn.close()

    if args.dry_run:
        return {
            "status": "dry-run",
            "out": str(root),
            "memory_count": len(rows),
            "agents": sorted({row.agent for row in rows}),
            "layers": sorted({row.layer for row in rows}, key=lambda layer: LAYER_ORDER.get(layer, 99)),
        }

    root.mkdir(parents=True, exist_ok=True)
    write_text(root / "README.md", render_index(rows, generated_at), allow_overwrite=args.allow_overwrite)
    for row in rows:
        write_text(memory_note_path(root, row), render_memory_note(row), allow_overwrite=args.allow_overwrite)

    for agent in sorted({row.agent for row in rows}):
        agent_rows = [row for row in rows if row.agent == agent]
        write_text(root / "Agents" / f"{agent}.md", render_agent_note(agent, agent_rows), allow_overwrite=args.allow_overwrite)

    for layer in sorted({row.layer for row in rows}, key=lambda item: LAYER_ORDER.get(item, 99)):
        layer_rows = [row for row in rows if row.layer == layer]
        write_text(root / "Layers" / f"{layer}.md", render_layer_note(layer, layer_rows), allow_overwrite=args.allow_overwrite)

    static_nodes = {
        "People/William.md": "# William\n\n[[Agents/ADA]] [[Layers/emotional]] [[Layers/operational]]\n",
        "People/Henry.md": "# Henry\n\nSecond in command after William.\n",
        "Machines/dadito-laptop.md": "# dadito-laptop\n\nWindows Codex App client for ADA.\n",
        "Machines/DGX Spark.md": "# DGX Spark\n\nCanonical SOUL/Spark runtime host.\n",
        "Systems/Codex App Windows.md": "# Codex App Windows\n\nPrimary ADA interface on dadito-laptop.\n",
        "Systems/SOUL MCP.md": "# SOUL MCP\n\nCanonical memory MCP service.\n",
        "Systems/ADA Codex Bridge.md": "# ADA Codex Bridge\n\nHeadless WebChat to Codex runtime bridge.\n",
        "Systems/WebChat.md": "# WebChat\n\nSEAL chat transport and channel source.\n",
    }
    for rel, content in static_nodes.items():
        write_text(root / rel, content, allow_overwrite=args.allow_overwrite)

    return {
        "status": "ok",
        "out": str(root),
        "memory_count": len(rows),
        "file_count": sum(1 for path in root.rglob("*") if path.is_file()),
        "agents": sorted({row.agent for row in rows}),
        "layers": sorted({row.layer for row in rows}, key=lambda layer: LAYER_ORDER.get(layer, 99)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export SOUL DB memories to a read-only Obsidian/Markdown map.")
    parser.add_argument("--out", type=Path, required=True, help="Output vault directory.")
    parser.add_argument("--agent", action="append", default=[], help="Agent filter; can be repeated.")
    parser.add_argument("--limit", type=int, default=250, help="Maximum memories to export.")
    parser.add_argument("--min-importance", type=int, default=8, help="Minimum memory importance.")
    parser.add_argument("--allow-overwrite", action="store_true", help="Allow overwriting generated files.")
    parser.add_argument("--dry-run", action="store_true", help="Only print summary; do not write files.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(export_vault(args))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
