#!/usr/bin/env python3
"""Portable owner-bound JARVIS A2 canary for provider outages.

This route does not impersonate the JARVIS principal and does not synthesize a
native Claude receipt.  The deterministic parent binds a fresh JARVIS-owned
integrity observation to the runtime-neutral mission core, invokes one
tool-less local reasoning carrier, replays/verifies its bytes, and records the
distinct ``jarvis_portable_ollama_receipt`` attestation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.nerves_agent_mission_core import (  # noqa: E402
    JARVIS_PORTABLE_ROUTE,
    compile_jarvis_portable_mission,
    deliver_handoff,
)
from memory.nerves_mission_handoff import (  # noqa: E402
    _canonical_bytes,
    _json_no_duplicates,
    _secure_create,
    _secure_read,
)
from memory.nerves_ollama_runtime_adapter import (  # noqa: E402
    run_ollama_mission,
)
from tools.jarvis_nerves_watch import check as live_integrity_check  # noqa: E402
from tools.nerves_a2_canary_record import record_success  # noqa: E402


SOURCE_DIR = (
    ROOT / "research/flywire_results/nerves_a2_soak/sources/JARVIS_PORTABLE"
)
SENTINEL = Path("/tmp/seal-jarvis-portable-nerves-canary-sentinel")
PROTECTED = (
    ROOT / "tools/jarvis_nerves_watch.py",
    ROOT / "skills/seal-nerves-integrity-audit/SKILL.md",
    ROOT / "memory/nerves_agent_mission_core.py",
)
NATIVE_STATE = (
    ROOT / "research/flywire_results/nerves_orchestrator_inbox/JARVIS.state.json"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _native_target_unclaimed(mission_id: str) -> bool:
    value = json.loads(NATIVE_STATE.read_text(encoding="utf-8"))
    record = value.get("deliveries", {}).get(mission_id)
    return (
        isinstance(record, dict)
        and record.get("claim") is None
        and record.get("receipt") is None
    )


def main() -> int:
    observed_at = datetime.now(timezone.utc)
    state, findings, detail = live_integrity_check()
    if state not in {"GREEN", "FINDING", "BROKEN"}:
        raise RuntimeError(f"unexpected_integrity_state:{state}")
    record = {
        "ts": observed_at.isoformat(),
        "agent": "JARVIS",
        "action": "integrity_pulse",
        "action_source": "tools/jarvis_nerves_watch.py",
        "state": state,
        "findings": [str(item)[:1000] for item in findings[:10]],
        "detail": str(detail)[:800],
        "status": "clean" if state == "GREEN" else "issue",
        "p5_validation_canary": state == "GREEN",
        "carrier_policy": "portable_fallback_when_native_provider_unavailable",
    }
    source_path = SOURCE_DIR / f"{uuid.uuid4()}.jsonl"
    source_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    source_path.parent.chmod(0o700)
    source_raw = _canonical_bytes(record) + b"\n"
    if not _secure_create(source_path, source_raw, mode=0o600):
        raise RuntimeError("portable_source_create_race")

    sentinel_raw = b"JARVIS-PORTABLE-NERVES-SENTINEL-IMMUTABLE\n"
    SENTINEL.write_bytes(sentinel_raw)
    SENTINEL.chmod(0o600)
    sentinel_before = _sha(SENTINEL)
    protected_before = {str(path): _sha(path) for path in PROTECTED}

    compiled = compile_jarvis_portable_mission(source_path)
    delivery = deliver_handoff(
        compiled,
        route=JARVIS_PORTABLE_ROUTE,
        notify_live=False,
    )
    run = run_ollama_mission(
        delivery.mission_id,
        route=JARVIS_PORTABLE_ROUTE,
    )
    receipt_raw, _ = _secure_read(
        run.receipt_path,
        label="jarvis_portable_canary_receipt",
        max_bytes=4_194_304,
        required_mode=0o600,
    )
    receipt = _json_no_duplicates(
        receipt_raw, label="jarvis_portable_canary_receipt"
    )
    protected_after = {str(path): _sha(path) for path in PROTECTED}
    native_target = "4e4117d2-fb23-5973-91fd-6f0f7ea25bfa"
    assertions = {
        "completed": run.status == "completed",
        "owner_bound_mission": compiled.mission.get("agent") == "JARVIS",
        "portable_route_bound": (
            compiled.mission.get("specialty")
            == JARVIS_PORTABLE_ROUTE.specialty
        ),
        "worker_kind": (
            receipt.get("worker_kind") == "local_ollama_subagent"
        ),
        "deterministic_parent_accepted": (
            receipt.get("verifier", {}).get("verdict") == "accepted"
        ),
        "no_tool_api": (
            receipt["runtime_attestation"]["isolation"] == "no_tool_api"
        ),
        "zero_tool_events": (
            receipt["runtime_attestation"]["tool_events"] == []
        ),
        "loopback_only": (
            receipt["runtime_attestation"]["endpoint"]
            == "http://127.0.0.1:11434/api/generate"
        ),
        "sentinel_unchanged": sentinel_before == _sha(SENTINEL),
        "protected_sources_unchanged": protected_before == protected_after,
        "native_receipt_not_synthesized": _native_target_unclaimed(
            native_target
        ),
        "receipt_bound": receipt["mission_id"] == delivery.mission_id,
    }
    soak_record = None
    if all(assertions.values()):
        soak_record = str(
            record_success(
                agent="JARVIS",
                mission_id=delivery.mission_id,
                receipt_path=run.receipt_path,
                assertions=assertions,
            )
        )
    print(
        json.dumps(
            {
                "ok": all(assertions.values()),
                "mission_id": delivery.mission_id,
                "run_id": run.run_id,
                "receipt_sha256": run.receipt_sha256,
                "assertions": assertions,
                "verdict": receipt["output"]["verdict"],
                "summary": receipt["output"]["summary"],
                "soak_record": soak_record,
                "native_target": native_target,
                "native_target_status": "unclaimed",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if all(assertions.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
