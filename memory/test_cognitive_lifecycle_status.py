#!/usr/bin/env python3
"""Smoke tests for cognitive lifecycle status reporter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from cognitive_lifecycle_status import write_markdown


def main() -> None:
    now = datetime.now(timezone.utc)
    status = [
        {
            "agent_name": "ADA",
            "state": "deep_work",
            "runtime": "codex",
            "budget_class": "code_heavy",
            "source_event_id": 1,
            "router_action": "delegate_codex",
            "confidence": 0.84,
            "reason": "fixture",
            "since": now,
            "expires_at": now,
            "updated_at": now,
            "feature_flag": "shadow",
        }
    ]
    events = [
        {
            "agent_name": "ADA",
            "previous_state": None,
            "new_state": "deep_work",
            "runtime": "codex",
            "budget_class": "code_heavy",
            "source_event_id": 1,
            "router_action": "delegate_codex",
            "confidence": 0.84,
            "reason": "fixture",
            "feature_flag": "shadow",
            "created_at": now,
        }
    ]
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "status.md"
        write_markdown(status, events, path)
        text = path.read_text(encoding="utf-8")
        assert "Cognitive Lifecycle Status" in text
        assert "ADA" in text
        assert "deep_work" in text
        assert "Read-only reporter" in text
    print("PASS cognitive lifecycle status fixtures")


if __name__ == "__main__":
    main()
