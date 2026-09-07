import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_tools_registry import AgentTool, format_boot_section, validate_tool


def test_validate_tool_rejects_invalid_status():
    try:
        validate_tool(AgentTool("x", "X", "core_soul", "demo", status="broken"))
    except ValueError as exc:
        assert "invalid status" in str(exc)
    else:
        raise AssertionError("validate_tool should reject invalid status")


def test_format_boot_section_includes_status_owner_and_endpoint():
    section = format_boot_section(
        [
            {
                "display_name": "SOUL Dashboard",
                "category": "dashboards",
                "purpose": "Dashboard de autonomia.",
                "endpoint": "http://localhost:8850",
                "protocol": "http",
                "host": "127.0.0.1",
                "port": 8850,
                "owner_agent": "JARVIS",
                "status": "up",
                "usage_hint": "Abrir Pendientes.",
            }
        ]
    )

    assert "Herramientas disponibles" in section
    assert "[UP] SOUL Dashboard" in section
    assert "owner=JARVIS" in section
    assert "http://localhost:8850" in section
    assert "Abrir Pendientes" in section
