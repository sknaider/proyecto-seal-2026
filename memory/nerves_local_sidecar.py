#!/usr/bin/env python3
"""Process at most one ADA NERVES local tool-less handoff per invocation."""

from __future__ import annotations

import json
import sys

from memory.nerves_agent_mission_core import (
    ADA_ROUTE,
    AgentMissionError,
    stale_claim_mission_ids,
)
from memory.nerves_ollama_runtime_adapter import (
    OllamaRuntimeError,
    fail_stale_ollama_claim,
    run_ollama_mission,
)
from memory.nerves_mission_handoff import _load_state


def pending_mission_ids() -> list[str]:
    state = _load_state(ADA_ROUTE.state_path)
    records = [
        record
        for record in state.get("deliveries", {}).values()
        if isinstance(record, dict)
        and record.get("status") in {"pending", "live_notified"}
    ]
    records.sort(key=lambda record: str(record.get("created_at") or ""))
    return [str(record["mission_id"]) for record in records]


def main() -> int:
    stale = stale_claim_mission_ids(route=ADA_ROUTE)
    if stale:
        result = fail_stale_ollama_claim(stale[0], route=ADA_ROUTE)
        print(
            json.dumps(
                {
                    "ok": True,
                    "mission_id": result.mission_id,
                    "status": result.status,
                    "receipt_sha256": result.receipt_sha256,
                    "reaped_stale_claim": True,
                },
                sort_keys=True,
            )
        )
        return 0
    pending = pending_mission_ids()
    if not pending:
        print(json.dumps({"ok": True, "status": "idle", "processed": 0}))
        return 0
    mission_id = pending[0]
    try:
        result = run_ollama_mission(mission_id)
    except (AgentMissionError, OllamaRuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mission_id": mission_id,
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "ok": result.status == "completed",
                "mission_id": result.mission_id,
                "status": result.status,
                "receipt_sha256": result.receipt_sha256,
                "run_id": result.run_id,
                "tool_events": [],
            },
            sort_keys=True,
        )
    )
    # A terminal failed/abstained receipt is still successfully processed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
