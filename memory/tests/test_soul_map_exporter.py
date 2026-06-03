from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soul_map_exporter import (
    MemoryRow,
    SkillRow,
    ToolRow,
    build_edges,
    extract_links,
    frontmatter,
    merge_skills,
    memory_note_path,
    skill_note_path,
    render_skill_note,
    render_tool_note,
    render_edges_json,
    render_edges_md,
    render_memory_note,
    slugify,
)


def test_slugify_is_filesystem_safe():
    assert slugify("Codex App Windows / ADA!") == "codex-app-windows-ada"
    assert slugify("!!!", fallback="memory") == "memory"


def test_extract_links_known_entities():
    links = extract_links("William fixed dadito-laptop through Codex App and MCP.")
    assert "People/William" in links
    assert "Machines/dadito-laptop" in links
    assert "Systems/Codex App Windows" in links
    assert "Systems/SOUL MCP" in links
    assert "Systems/Codex" not in links


def test_extract_links_uses_word_boundaries():
    links = extract_links("canadarm and adagio should not trigger agent links; randomium and dummies are words.")
    assert "Agents/ADA" not in links
    assert "Agents/DUM" not in links


def test_frontmatter_quotes_strings_and_lists():
    text = frontmatter({"agent": "ADA", "importance": 10, "links": ["People/William"]})
    assert 'agent: "ADA"' in text
    assert "importance: 10" in text
    assert 'links: ["People/William"]' in text


def test_render_memory_note_contains_canonical_fields():
    row = MemoryRow(
        id=248478,
        agent="ADA",
        layer="operational",
        category="operational_anchor",
        memory_type="semantic",
        importance=10,
        created_at=datetime(2026, 6, 2, tzinfo=UTC),
        source="direct_chat",
        metadata={"layer": "operational"},
        content="William wants Codex App Windows on dadito-laptop.",
    )
    note = render_memory_note(row)
    assert "soul_id: 248478" in note
    assert 'layer: "operational"' in note
    assert "[[Machines/dadito-laptop]]" in note
    assert "William wants Codex App Windows" in note
    assert memory_note_path(Path("/tmp/vault"), row).name.startswith("248478-")


def test_build_edges_links_memory_to_agent_layer_category_and_entities():
    row = MemoryRow(
        id=248478,
        agent="ADA",
        layer="operational",
        category="operational_anchor",
        memory_type="semantic",
        importance=10,
        created_at=datetime(2026, 6, 2, tzinfo=UTC),
        source="direct_chat",
        metadata={"layer": "operational"},
        content="William wants Codex App Windows on dadito-laptop.",
    )
    edges = build_edges([row])
    edge_pairs = {(edge.relation, edge.target) for edge in edges}
    assert ("agent", "Agents/ADA") in edge_pairs
    assert ("layer", "Layers/operational") in edge_pairs
    assert ("category", "Categories/operational-anchor") in edge_pairs
    assert ("mentions", "People/William") in edge_pairs
    assert ("mentions", "Machines/dadito-laptop") in edge_pairs


def test_render_edges_outputs_json_and_markdown():
    row = MemoryRow(
        id=248478,
        agent="ADA",
        layer="operational",
        category="operational_anchor",
        memory_type="semantic",
        importance=10,
        created_at=datetime(2026, 6, 2, tzinfo=UTC),
        source="direct_chat",
        metadata={"layer": "operational"},
        content="William wants Codex App Windows on dadito-laptop.",
    )
    edges = build_edges([row])
    assert '"relation": "agent"' in render_edges_json(edges)
    assert "| Source | Relation | Target | Soul ID |" in render_edges_md(edges)


def test_render_skill_and_tool_notes():
    skill = SkillRow(
        name="seal-nexus-deep-audit",
        description="Deep audit workflow.",
        path=".agents/skills/seal-nexus-deep-audit/SKILL.md",
        agent="TEAM",
        source="filesystem",
    )
    tool = ToolRow(
        key="soul_mcp",
        display_name="SOUL MCP Memory",
        category="core_soul",
        purpose="Memory server.",
        endpoint="http://localhost:8771/mcp",
        owner_agent="ADA",
        status="up",
        source="soul_v3.agent_tools_registry",
    )
    assert "Deep audit workflow" in render_skill_note(skill)
    assert "Memory server" in render_tool_note(tool)


def test_merge_skills_prefers_soul_db_over_filesystem():
    local = SkillRow("x", "local", "skills/x/SKILL.md", "TEAM", "filesystem")
    db = SkillRow("x", "db", "/db/x", "ADA", "soul_v3.skills")
    merged = merge_skills([db], [local], limit=10)
    assert len(merged) == 2
    assert {skill.agent for skill in merged} == {"ADA", "TEAM"}


def test_merge_skills_prefers_soul_db_for_same_agent_and_name():
    local = SkillRow("x", "local", "skills/x/SKILL.md", "TEAM", "filesystem")
    db = SkillRow("x", "db", "/db/x", "TEAM", "soul_v3.skills")
    merged = merge_skills([db], [local], limit=10)
    assert len(merged) == 1
    assert merged[0].description == "db"
    assert merged[0].source == "soul_v3.skills"


def test_skill_note_path_includes_agent_to_avoid_duplicate_name_clobber():
    ada = SkillRow("debugging", "ada", "/db/ada", "ADA", "soul_v3.skills")
    team = SkillRow("debugging", "team", "/db/team", "TEAM", "soul_v3.skills")
    assert skill_note_path(Path("/tmp/vault"), ada).name == "ada-debugging.md"
    assert skill_note_path(Path("/tmp/vault"), team).name == "team-debugging.md"
