from decimal import Decimal

import asyncio

from tools.soul_metrics_exporter import _label, _systemd_snapshot, render_metrics


def test_label_escaping() -> None:
    assert _label('a"b\\c\nd') == 'a\\"b\\\\c\\nd'


def test_render_contains_only_aggregate_metrics() -> None:
    rendered = render_metrics(
        {
            "work": [{"agent": "ADA", "status": "in_progress", "item_count": 2}],
            "latency": [
                {
                    "agent": "ADA",
                    "tool_name": "active_recall",
                    "n": 10,
                    "median_ms": 12.0,
                    "p95_ms": 20.0,
                    "avg_ms": Decimal("14.5"),
                }
            ],
            "tools": [{"status": "up", "category": "core", "owner_agent": "ADA", "tools": 3}],
            "units": [
                {
                    "unit": "seal-chat.service",
                    "type": "service",
                    "active": "active",
                    "sub": "running",
                }
            ],
        }
    )
    assert 'soul_agent_work_items{agent="ADA",status="in_progress"} 2' in rendered
    assert 'statistic="p95"} 20.0' in rendered
    assert 'soul_registered_tools{status="up",category="core",owner="ADA"} 3' in rendered
    assert 'unit="seal-chat.service",type="service",active="active",sub="running"' in rendered
    assert 'soul_user_units{type="service",active="active",sub="running"} 1' in rendered
    assert "memory" not in rendered.lower()
    assert "chat_messages" not in rendered.lower()


def test_systemd_snapshot_contains_soul_units_only() -> None:
    units = asyncio.run(_systemd_snapshot())
    assert any(row["unit"] == "seal-chat.service" for row in units)
    assert all(
        row["unit"].startswith(
            (
                "seal-",
                "ada-codex-",
                "orion-exam.",
                "dum-",
                "claude-proxy.",
                "fable-spectre-",
                "gtl-editor.",
                "spectre-",
            )
        )
        for row in units
    )
