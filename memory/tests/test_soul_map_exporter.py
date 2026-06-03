from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from soul_map_exporter import (
    MemoryRow,
    extract_links,
    frontmatter,
    memory_note_path,
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
