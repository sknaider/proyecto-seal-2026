from datetime import UTC, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from soul_cognitive_graph_viewer import (
    build_cognitive_graph_payload,
    cytoscape_elements,
    render_viewer_html,
    write_markdown_subset,
)
from soul_map_exporter import MemoryRow


def make_row(mid: int, content: str, *, category: str = "operational_anchor", layer: str = "operational") -> MemoryRow:
    return MemoryRow(
        id=mid,
        agent="ADA",
        layer=layer,
        category=category,
        memory_type="semantic",
        importance=10,
        created_at=datetime(2026, 6, 3, tzinfo=UTC),
        source="test",
        metadata={"layer": layer},
        content=content,
    )


def test_build_cognitive_graph_payload_creates_memory_and_facet_nodes():
    rows = [
        make_row(248478, "William wants Codex App Windows on dadito-laptop."),
        make_row(248403, "ADA rule: dm:ada:william and web_chat/general silence rule."),
    ]

    payload = build_cognitive_graph_payload(rows, generated_at=datetime(2026, 6, 3, tzinfo=UTC))

    assert payload["memory_count"] == 2
    assert payload["node_count"] > 2
    assert payload["edge_count"] > 2
    node_ids = {node["id"] for node in payload["nodes"]}
    assert "memory:248478" in node_ids
    assert any(node["facet_kind"] == "surface" and node["label"] == "codex_app_windows" for node in payload["nodes"])
    assert any(edge["relation"] == "facet" for edge in payload["edges"])


def test_cytoscape_elements_include_nodes_and_edges():
    payload = build_cognitive_graph_payload([make_row(1, "William and Codex App Windows.")])

    elements = cytoscape_elements(payload)

    assert any(item["group"] == "nodes" for item in elements)
    assert any(item["group"] == "edges" for item in elements)


def test_render_viewer_html_embeds_graph_and_controls():
    payload = build_cognitive_graph_payload([make_row(1, "William and Codex App Windows.")])

    html = render_viewer_html(payload)

    assert "SOUL Cognitive Graph" in html
    assert "cytoscape" in html
    assert "memory:1" in html
    assert "typeFilter" in html


def test_write_markdown_subset_writes_obsidian_files(tmp_path):
    rows = [make_row(248478, "William wants Codex App Windows on dadito-laptop.")]
    payload = build_cognitive_graph_payload(rows, generated_at=datetime(2026, 6, 3, tzinfo=UTC))

    write_markdown_subset(tmp_path, rows, payload, allow_overwrite=False)

    assert (tmp_path / "README.md").exists()
    assert (tmp_path / "SOUL Memory Map.md").exists()
    assert (tmp_path / "Memories" / "Memory-248478.md").exists()
    assert list((tmp_path / "Facets").glob("*.md"))
