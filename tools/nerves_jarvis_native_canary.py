#!/usr/bin/env python3
"""Dispatch or admit one fresh native JARVIS route canary for the A2 soak.

``dispatch`` runs the real bounded JARVIS integrity probe, binds its exact
private bytes into a new A2 mission, builds deterministic evidence, and wakes
the JARVIS principal through the authenticated handoff.  It never fabricates a
finding and never spawns the worker itself.

``record`` reopens a completed native receipt and its state/transcript custody
chain before admitting it into the active soak.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from memory.nerves_integrity_evidence_bundle import (  # noqa: E402
    build_integrity_evidence_bundle,
)
from memory.nerves_mission_handoff import deliver_jarvis_handoff  # noqa: E402
from memory.nerves_mission_shadow import (  # noqa: E402
    DEFAULT_MANIFEST_DIR,
    ShadowMissionLedger,
    compile_jarvis_integrity_mission,
    write_shadow_manifest,
)
from tools.jarvis_nerves_watch import check as live_integrity_check  # noqa: E402
from tools.nerves_a2_canary_record import record_success  # noqa: E402
from tools.nerves_a2_soak_monitor import release_fingerprint  # noqa: E402


SOURCE_DIR = (
    ROOT / "research/flywire_results/nerves_a2_soak/jarvis_sources"
)
EVIDENCE_DIR = (
    ROOT / "research/flywire_results/nerves_evidence_bundles/JARVIS"
)


class JarvisCanaryError(RuntimeError):
    """The live JARVIS route cannot be admitted safely."""


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _write_private_exclusive(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        raw = _canonical(value)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise JarvisCanaryError("source_custody_invalid")


def dispatch() -> dict:
    observed_at = datetime.now(timezone.utc)
    state, findings, detail = live_integrity_check()
    if state not in {"GREEN", "FINDING", "BROKEN"}:
        raise JarvisCanaryError(f"unexpected_integrity_state:{state}")
    record = {
        "ts": observed_at.isoformat(),
        "agent": "JARVIS",
        "action": "integrity_pulse",
        "action_source": "tools/jarvis_nerves_watch.py",
        "state": state,
        "findings": [str(item) for item in findings[:10]],
        "detail": str(detail)[:800],
        "status": "clean" if state == "GREEN" else "issue",
        "p5_validation_canary": state == "GREEN",
    }
    source_path = SOURCE_DIR / f"{observed_at.strftime('%Y%m%dT%H%M%S%fZ')}.jsonl"
    _write_private_exclusive(source_path, record)
    fingerprint = release_fingerprint(ROOT)
    anchor = f"p5-validation:{fingerprint}:{observed_at.isoformat()}"
    mission = compile_jarvis_integrity_mission(
        record,
        artifact_path=source_path,
        episode_anchor=anchor,
        validation_canary=state == "GREEN",
    )
    opened = ShadowMissionLedger().open_or_join(mission)
    manifest = write_shadow_manifest(opened.mission, DEFAULT_MANIFEST_DIR)
    bundle = build_integrity_evidence_bundle(
        manifest,
        output_dir=EVIDENCE_DIR,
        workspace=ROOT,
    )
    handoff = deliver_jarvis_handoff(
        manifest,
        bundle.evidence_path,
        bundle.provenance_path,
        workspace=ROOT,
    )
    return {
        "schema": "seal.nerves.jarvis-p5-canary-dispatch.v1",
        "state": state,
        "mission_id": handoff.mission_id,
        "source_path": str(source_path),
        "manifest_path": str(manifest),
        "evidence_path": str(bundle.evidence_path),
        "handoff_path": str(handoff.inbox_path),
        "handoff_sha256": handoff.handoff_sha256,
        "handoff_status": handoff.status,
        "live_notified": handoff.live_notified,
        "worker_spawned_by_dispatcher": False,
    }


def record(mission_id: str, receipt_path: Path) -> dict:
    normalized = str(uuid.UUID(mission_id))
    path = record_success(
        agent="JARVIS",
        mission_id=normalized,
        receipt_path=receipt_path,
        assertions={
            "native_receipt_reopened": True,
            "state_and_transcripts_revalidated": True,
            "a2_boundary_exact": True,
        },
    )
    return {
        "schema": "seal.nerves.jarvis-p5-canary-record.v1",
        "mission_id": normalized,
        "canary_path": str(path),
        "recorded": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("dispatch")
    recorder = subcommands.add_parser("record")
    recorder.add_argument("--mission-id", required=True)
    recorder.add_argument("--receipt-path", required=True, type=Path)
    arguments = parser.parse_args()
    try:
        payload = (
            dispatch()
            if arguments.command == "dispatch"
            else record(arguments.mission_id, arguments.receipt_path)
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}:{exc}"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps({"ok": True, **payload}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
