import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_server_v4 import _parse_metadata_arg, format_dual_memory_entries


def test_parse_metadata_arg_accepts_structured_dict():
    meta = _parse_metadata_arg({"layer": "operational", "anchor_kind": "canonical"})

    assert meta == {"layer": "operational", "anchor_kind": "canonical"}


def test_parse_metadata_arg_accepts_json_string():
    meta = _parse_metadata_arg('{"layer":"emotional","anchor_kind":"canonical"}')

    assert meta == {"layer": "emotional", "anchor_kind": "canonical"}


def test_parse_metadata_arg_preserves_non_json_string_as_raw_metadata():
    meta = _parse_metadata_arg("manual note")

    assert meta == {"raw_metadata": "manual note"}


def test_format_dual_memory_entries_splits_operational_and_emotional():
    text, ids = format_dual_memory_entries(
        [
            {
                "id": 248035,
                "score": 0.99,
                "payload": {
                    "agent": "ADA",
                    "scope": "team",
                    "category": "decision",
                    "importance": 10,
                    "content": "MEMORIA OPERATIVA ADA v2",
                    "metadata": {"layer": "operational"},
                },
            },
            {
                "id": 242369,
                "score": 0.98,
                "payload": {
                    "agent": "ADA",
                    "scope": "team",
                    "category": "emotion",
                    "importance": 10,
                    "content": "MEMORIA EMOCIONAL ADA v1",
                    "metadata": {"layer": "emotional"},
                },
            },
        ],
        "ADA",
    )

    assert "CAPA OPERATIVA" in text
    assert "CAPA EMOCIONAL COMPACTA" in text
    assert "MEMORIA OPERATIVA ADA v2" in text
    assert "MEMORIA EMOCIONAL ADA v1" in text
    assert ids == [248035, 242369]
