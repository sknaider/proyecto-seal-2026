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
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

import asyncpg

try:
    from .seal_secrets import pg_dsn
except ImportError:  # CLI execution: python memory/soul_map_exporter.py
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from seal_secrets import pg_dsn


LINK_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Machines/dadito-laptop", ("dadito-laptop", "dadito laptop")),
    ("Machines/daditogamer", ("daditogamer", "dadito gamer")),
    ("Machines/DGX Spark", ("dgx spark", "spark")),
    ("Systems/Codex App Windows", ("codex app", "codex windows app", "codex app windows")),
    ("Systems/Codex", ("codex",)),
    ("Systems/SOUL MCP", ("soul mcp", "mcp")),
    ("Systems/ADA Codex Bridge", ("ada codex bridge", "bridge headless", "remote bridge")),
    ("Systems/WebChat", ("webchat", "web_chat", "web chat")),
    ("People/William", ("william", "dadito")),
    ("People/Henry", ("henry", "kinger")),
    ("Agents/JARVIS", ("jarvis",)),
    ("Agents/ALICE", ("alice",)),
    ("Agents/NEXUS", ("nexus",)),
    ("Agents/DUM", ("dum",)),
    ("Agents/ADA", ("ada",)),
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


@dataclass(frozen=True)
class MapEdge:
    source: str
    target: str
    relation: str
    soul_id: int | None = None
    agent: str | None = None
    layer: str | None = None


@dataclass(frozen=True)
class SkillRow:
    name: str
    description: str
    path: str
    agent: str
    source: str
    metric_score: float | None = None
    boot_load: bool | None = None


@dataclass(frozen=True)
class ToolRow:
    key: str
    display_name: str
    category: str
    purpose: str
    endpoint: str
    owner_agent: str
    status: str
    source: str


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
    lower = content.lower().replace("_", " ")
    seen: set[str] = set()
    links: list[str] = []
    for target, aliases in LINK_RULES:
        for alias in aliases:
            pattern = r"(?<![a-z0-9])" + re.escape(alias.lower()).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
            if re.search(pattern, lower):
                if target not in seen:
                    seen.add(target)
                    links.append(target)
                break
    if "Systems/Codex App Windows" in seen and "Systems/Codex" in seen:
        seen.remove("Systems/Codex")
        links = [link for link in links if link != "Systems/Codex"]
    return links


def read_skill_description(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines()]
    for line in lines:
        if line and not line.startswith("#"):
            return line[:240]
    for line in lines:
        if line.startswith("#"):
            return line.lstrip("#").strip()[:240]
    return ""


def discover_local_skills(root: Path, *, limit: int) -> list[SkillRow]:
    skill_paths: list[Path] = []
    for base in (root / ".agents" / "skills", root / "skills", root / "tools" / "skills"):
        if base.exists():
            skill_paths.extend(sorted(base.rglob("SKILL.md")))
    rows: list[SkillRow] = []
    for path in skill_paths[:limit]:
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        rows.append(
            SkillRow(
                name=path.parent.name,
                description=read_skill_description(path),
                path=rel.as_posix(),
                agent="TEAM",
                source="filesystem",
            )
        )
    return rows


def merge_skills(db_skills: list[SkillRow], local_skills: list[SkillRow], *, limit: int) -> list[SkillRow]:
    merged: dict[str, SkillRow] = {}
    for skill in local_skills + db_skills:
        key = f"{skill.agent.upper()}:{skill.name.lower()}"
        if key not in merged or skill.source == "soul_v3.skills":
            merged[key] = skill
    return sorted(merged.values(), key=lambda skill: (skill.agent, skill.name.lower()))[:limit]


def skill_note_ref(skill: SkillRow) -> str:
    return f"Skills/{slugify(skill.agent)}-{slugify(skill.name)}"


def skill_note_path(root: Path, skill: SkillRow) -> Path:
    return root / f"{skill_note_ref(skill)}.md"


def tool_note_ref(tool: ToolRow) -> str:
    return f"Tools/{slugify(tool.key or tool.display_name)}"


def tool_note_path(root: Path, tool: ToolRow) -> Path:
    return root / f"{tool_note_ref(tool)}.md"


def render_skill_note(skill: SkillRow) -> str:
    return "\n".join(
        [
            frontmatter(
                {
                    "name": skill.name,
                    "agent": skill.agent,
                    "source": skill.source,
                    "skill_path": skill.path,
                    "metric_score": skill.metric_score if skill.metric_score is not None else "",
                    "boot_load": skill.boot_load if skill.boot_load is not None else False,
                    "canonical": skill.source == "soul_v3.skills",
                }
            ),
            f"# Skill {skill.name}",
            "",
            skill.description or "No description available.",
            "",
            f"Path: `{skill.path}`",
            "",
        ]
    )


def render_tool_note(tool: ToolRow) -> str:
    return "\n".join(
        [
            frontmatter(
                {
                    "tool_key": tool.key,
                    "display_name": tool.display_name,
                    "category": tool.category,
                    "owner_agent": tool.owner_agent,
                    "status": tool.status,
                    "endpoint": tool.endpoint,
                    "source": tool.source,
                    "canonical": tool.source == "soul_v3.agent_tools_registry",
                }
            ),
            f"# Tool {tool.display_name}",
            "",
            tool.purpose or "No purpose available.",
            "",
            f"Endpoint: `{tool.endpoint}`",
            "",
            f"Owner: [[Agents/{tool.owner_agent}]]",
            "",
        ]
    )


def render_skills_index(skills: list[SkillRow]) -> str:
    lines = [
        frontmatter({"generated_by": "memory/soul_map_exporter.py", "skill_count": len(skills)}),
        "# Skills",
        "",
    ]
    for skill in skills:
        lines.append(f"- [[{skill_note_ref(skill)}|{skill.name}]] `{skill.agent}` `{skill.source}`")
    lines.append("")
    return "\n".join(lines)


def render_tools_index(tools: list[ToolRow]) -> str:
    lines = [
        frontmatter({"generated_by": "memory/soul_map_exporter.py", "tool_count": len(tools)}),
        "# Tools",
        "",
    ]
    for tool in tools:
        lines.append(f"- [[{tool_note_ref(tool)}|{tool.display_name}]] `{tool.category}` `{tool.status}`")
    lines.append("")
    return "\n".join(lines)


def missing_edge_targets(root: Path, edges: list[MapEdge]) -> list[str]:
    missing: list[str] = []
    for target in sorted({edge.target for edge in edges}):
        if not (root / f"{target}.md").exists():
            missing.append(target)
    return missing


def render_placeholder_node(target: str) -> str:
    title = target.rsplit("/", 1)[-1]
    return "\n".join(
        [
            frontmatter({"generated_by": "memory/soul_map_exporter.py", "canonical": False, "placeholder": True}),
            f"# {title}",
            "",
            f"Generated placeholder for `[[{target}]]`.",
            "",
        ]
    )


def referenced_nodes_from_tools_and_skills(skills: list[SkillRow], tools: list[ToolRow]) -> list[MapEdge]:
    edges: list[MapEdge] = []
    for skill in skills:
        source = skill_note_ref(skill)
        edges.append(MapEdge(source=source, target=f"Agents/{skill.agent}", relation="skill_agent", agent=skill.agent))
    for tool in tools:
        source = tool_note_ref(tool)
        edges.append(MapEdge(source=source, target=f"Agents/{tool.owner_agent}", relation="tool_owner", agent=tool.owner_agent))
        if tool.category:
            edges.append(MapEdge(source=source, target=f"ToolCategories/{slugify(tool.category)}", relation="tool_category", agent=tool.owner_agent))
    return edges


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


def memory_note_ref(row: MemoryRow) -> str:
    return memory_note_path(Path("."), row).with_suffix("").as_posix()


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


def build_edges(rows: list[MemoryRow]) -> list[MapEdge]:
    edges: list[MapEdge] = []
    for row in rows:
        source = memory_note_ref(row)
        edges.append(
            MapEdge(
                source=source,
                target=f"Agents/{row.agent}",
                relation="agent",
                soul_id=row.id,
                agent=row.agent,
                layer=row.layer,
            )
        )
        edges.append(
            MapEdge(
                source=source,
                target=f"Layers/{row.layer}",
                relation="layer",
                soul_id=row.id,
                agent=row.agent,
                layer=row.layer,
            )
        )
        if row.category:
            edges.append(
                MapEdge(
                    source=source,
                    target=f"Categories/{slugify(row.category)}",
                    relation="category",
                    soul_id=row.id,
                    agent=row.agent,
                    layer=row.layer,
                )
            )
        for target in extract_links(row.content):
            edges.append(
                MapEdge(
                    source=source,
                    target=target,
                    relation="mentions",
                    soul_id=row.id,
                    agent=row.agent,
                    layer=row.layer,
                )
            )
    return edges


def render_edges_json(edges: list[MapEdge]) -> str:
    payload = [
        {
            "source": edge.source,
            "target": edge.target,
            "relation": edge.relation,
            "soul_id": edge.soul_id,
            "agent": edge.agent,
            "layer": edge.layer,
        }
        for edge in edges
    ]
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_edges_md(edges: list[MapEdge]) -> str:
    lines = [
        frontmatter(
            {
                "generated_by": "memory/soul_map_exporter.py",
                "edge_count": len(edges),
                "canonical": False,
            }
        ),
        "# SOUL Memory Map Edges",
        "",
        "Generated edges for Obsidian/Markdown navigation. SOUL DB remains canonical.",
        "",
        "| Source | Relation | Target | Soul ID |",
        "|---|---|---|---|",
    ]
    for edge in edges:
        source = f"[[{edge.source}]]"
        target = f"[[{edge.target}]]"
        soul_id = f"#{edge.soul_id}" if edge.soul_id is not None else ""
        lines.append(f"| {source} | `{edge.relation}` | {target} | {soul_id} |")
    lines.append("")
    return "\n".join(lines)


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


async def fetch_db_skills(conn: asyncpg.Connection, *, limit: int) -> list[SkillRow]:
    query = """
        SELECT agent, name, description, skill_path, metric_score, boot_load
        FROM soul_v3.skills
        WHERE invalid_at IS NULL
        ORDER BY boot_load DESC, metric_score DESC NULLS LAST, importance_count DESC, name
        LIMIT $1
    """
    async with conn.transaction(readonly=True):
        records = await conn.fetch(query, limit)
    return [
        SkillRow(
            name=str(record["name"]),
            description=str(record.get("description") or ""),
            path=str(record.get("skill_path") or ""),
            agent=str(record.get("agent") or "TEAM"),
            source="soul_v3.skills",
            metric_score=float(record["metric_score"]) if record.get("metric_score") is not None else None,
            boot_load=bool(record.get("boot_load")),
        )
        for record in records
    ]


async def fetch_tools(conn: asyncpg.Connection, *, limit: int) -> list[ToolRow]:
    query = """
        SELECT tool_key, display_name, category, purpose, endpoint, owner_agent, status, source
        FROM soul_v3.agent_tools_registry
        ORDER BY
            CASE status WHEN 'up' THEN 0 WHEN 'unknown' THEN 1 WHEN 'stale' THEN 2 ELSE 3 END,
            category,
            display_name
        LIMIT $1
    """
    async with conn.transaction(readonly=True):
        records = await conn.fetch(query, limit)
    return [
        ToolRow(
            key=str(record.get("tool_key") or ""),
            display_name=str(record.get("display_name") or record.get("tool_key") or ""),
            category=str(record.get("category") or ""),
            purpose=str(record.get("purpose") or ""),
            endpoint=str(record.get("endpoint") or ""),
            owner_agent=str(record.get("owner_agent") or "TEAM"),
            status=str(record.get("status") or "unknown"),
            source="soul_v3.agent_tools_registry",
        )
        for record in records
    ]


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
        db_skills = await fetch_db_skills(conn, limit=args.skill_limit)
        tools = await fetch_tools(conn, limit=args.tool_limit)
    finally:
        await conn.close()
    local_skills = discover_local_skills(Path.cwd(), limit=args.skill_limit)
    skills = merge_skills(db_skills, local_skills, limit=args.skill_limit)

    if args.dry_run:
        return {
            "status": "dry-run",
            "out": str(root),
            "memory_count": len(rows),
            "skill_count": len(skills),
            "tool_count": len(tools),
            "agents": sorted({row.agent for row in rows}),
            "layers": sorted({row.layer for row in rows}, key=lambda layer: LAYER_ORDER.get(layer, 99)),
        }

    root.mkdir(parents=True, exist_ok=True)
    write_text(root / "README.md", render_index(rows, generated_at), allow_overwrite=args.allow_overwrite)
    for row in rows:
        write_text(memory_note_path(root, row), render_memory_note(row), allow_overwrite=args.allow_overwrite)

    for skill in skills:
        write_text(skill_note_path(root, skill), render_skill_note(skill), allow_overwrite=args.allow_overwrite)
    write_text(root / "Skills" / "README.md", render_skills_index(skills), allow_overwrite=args.allow_overwrite)

    for tool in tools:
        write_text(tool_note_path(root, tool), render_tool_note(tool), allow_overwrite=args.allow_overwrite)
    write_text(root / "Tools" / "README.md", render_tools_index(tools), allow_overwrite=args.allow_overwrite)

    edges = build_edges(rows) + referenced_nodes_from_tools_and_skills(skills, tools)
    write_text(root / "Graph" / "edges.json", render_edges_json(edges), allow_overwrite=args.allow_overwrite)
    write_text(root / "Graph" / "edges.md", render_edges_md(edges), allow_overwrite=args.allow_overwrite)

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
        "Systems/Codex.md": "# Codex\n\nCodex CLI/App runtime family.\n",
        "Systems/SOUL MCP.md": "# SOUL MCP\n\nCanonical memory MCP service.\n",
        "Systems/ADA Codex Bridge.md": "# ADA Codex Bridge\n\nHeadless WebChat to Codex runtime bridge.\n",
        "Systems/WebChat.md": "# WebChat\n\nSEAL chat transport and channel source.\n",
        "Machines/daditogamer.md": "# daditogamer\n\nWindows machine previously used for Codex workflow validation.\n",
    }
    for rel, content in static_nodes.items():
        write_text(root / rel, content, allow_overwrite=args.allow_overwrite)

    for target in missing_edge_targets(root, edges):
        write_text(root / f"{target}.md", render_placeholder_node(target), allow_overwrite=args.allow_overwrite)

    return {
        "status": "ok",
        "out": str(root),
        "memory_count": len(rows),
        "skill_count": len(skills),
        "tool_count": len(tools),
        "edge_count": len(edges),
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
    parser.add_argument("--skill-limit", type=int, default=500, help="Maximum skills to export.")
    parser.add_argument("--tool-limit", type=int, default=80, help="Maximum tools to export.")
    parser.add_argument("--allow-overwrite", action="store_true", help="Allow overwriting generated files.")
    parser.add_argument("--dry-run", action="store_true", help="Only print summary; do not write files.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = asyncio.run(export_vault(args))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
