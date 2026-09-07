from __future__ import annotations

import pytest

from mcp_server_v4 import _agent_task_status_filter


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        (" pending ", "pending"),
        ("IN_PROGRESS", "in_progress"),
        ("completed", "completed"),
        ("cancelled", "cancelled"),
    ],
)
def test_agent_task_status_filter_accepts_supported_states(raw: str, expected: str) -> None:
    assert _agent_task_status_filter(raw) == expected


def test_agent_task_status_filter_rejects_unknown_state() -> None:
    with pytest.raises(ValueError, match="status inválido"):
        _agent_task_status_filter("done-ish")
