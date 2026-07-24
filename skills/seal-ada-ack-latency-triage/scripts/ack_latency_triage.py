#!/usr/bin/env python3
"""Validate and classify typed ADA acknowledgement-latency evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


SCHEMA = "seal.ada.ack_latency_bundle.v1"
RESULT_SCHEMA = "seal.ada.ack_latency_triage.v1"
PATTERN_ID = "ada_response_delivery_latency_recovery_v1"
ALLOWED_KINDS = {"public_web_chat"}
PUBLIC_OBSERVATION_ID = re.compile(r"^ada-public-ack-(\d+)-(\d+)$")
PUBLIC_LEGACY_ID = re.compile(r"^api_(william|ada)_[A-Za-z0-9_-]+$")
SYNTHETIC_MARKERS = ("synthetic", "fixture", "test", "fake", "mock")


class AckLatencyEvidenceError(ValueError):
    """Evidence is private, malformed, synthetic, or not discriminating."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise AckLatencyEvidenceError(f"{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AckLatencyEvidenceError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise AckLatencyEvidenceError(f"{field}_timezone_required")
    return parsed


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _legacy_id(value: Any, field: str, expected_sender: str) -> str:
    if not isinstance(value, str):
        raise AckLatencyEvidenceError(f"{field}_legacy_id_invalid")
    match = PUBLIC_LEGACY_ID.fullmatch(value)
    if match is None or match.group(1) != expected_sender.lower():
        raise AckLatencyEvidenceError(f"{field}_legacy_id_invalid")
    lowered = value.lower()
    if any(marker in lowered for marker in SYNTHETIC_MARKERS):
        raise AckLatencyEvidenceError(f"{field}_synthetic_id_forbidden")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], prefix: str) -> None:
    if set(value) != expected:
        raise AckLatencyEvidenceError(f"{prefix}_shape_invalid")


def _public_observation(
    observation: dict[str, Any],
    *,
    sla_seconds: int,
) -> dict[str, Any]:
    _exact_keys(
        observation,
        {
            "observation_id",
            "pattern_id",
            "source_kind",
            "channel",
            "request",
            "ack",
            "recovery",
        },
        "public_observation",
    )
    if observation["channel"] != "web_chat":
        raise AckLatencyEvidenceError("private_or_unknown_channel_forbidden")
    request = observation["request"]
    ack = observation["ack"]
    recovery = observation["recovery"]
    for name, row, sender in (
        ("request", request, "William"),
        ("ack", ack, "ADA"),
    ):
        if not isinstance(row, dict):
            raise AckLatencyEvidenceError(f"{name}_invalid")
        _exact_keys(row, {"id", "legacy_id", "sender", "created_at", "in_reply_to"}, name)
        if row["sender"] != sender:
            raise AckLatencyEvidenceError(f"{name}_sender_invalid")
        if not isinstance(row["id"], int) or row["id"] <= 0:
            raise AckLatencyEvidenceError(f"{name}_id_invalid")
        _legacy_id(row["legacy_id"], name, sender)
    if request["in_reply_to"] is not None:
        raise AckLatencyEvidenceError("request_reply_binding_invalid")
    if ack["in_reply_to"] != request["legacy_id"]:
        raise AckLatencyEvidenceError("ack_reply_binding_invalid")
    expected_observation_id = f"ada-public-ack-{request['id']}-{ack['id']}"
    if observation["observation_id"] != expected_observation_id:
        raise AckLatencyEvidenceError("observation_id_not_canonical")
    if recovery is not None:
        if not isinstance(recovery, dict):
            raise AckLatencyEvidenceError("recovery_invalid")
        _exact_keys(
            recovery,
            {"id", "legacy_id", "sender", "created_at", "in_reply_to"},
            "recovery",
        )
        _legacy_id(recovery["legacy_id"], "recovery", "ADA")
        if (
            recovery["sender"] != "ADA"
            or recovery["in_reply_to"] != request["legacy_id"]
            or not isinstance(recovery["id"], int)
            or recovery["id"] <= ack["id"]
        ):
            raise AckLatencyEvidenceError("recovery_binding_invalid")
    request_at = _utc(_timestamp(request["created_at"], "request_created_at"))
    ack_at = _utc(_timestamp(ack["created_at"], "ack_created_at"))
    if ack_at < request_at:
        raise AckLatencyEvidenceError("ack_precedes_request")
    latency_ms = int(round((ack_at - request_at).total_seconds() * 1000))
    if recovery is not None:
        recovery_at = _utc(_timestamp(recovery["created_at"], "recovery_created_at"))
        if recovery_at < ack_at:
            raise AckLatencyEvidenceError("recovery_precedes_ack")
    late = latency_ms > sla_seconds * 1000
    classification = (
        "ack_late_recovered"
        if late and recovery is not None
        else "ack_late_no_recovery"
        if late
        else "ack_on_time"
    )
    return {
        "observation_id": observation["observation_id"],
        "pattern_id": PATTERN_ID,
        "observed_at": request_at.isoformat().replace("+00:00", "Z"),
        "evidence_sha256": sha256(observation),
        "verified": True,
        "classification": classification,
        "ack_latency_ms": latency_ms,
        "sla_ms": sla_seconds * 1000,
        "source_kind": "public_web_chat",
        "source_ids": [request["id"], ack["id"]]
        + ([recovery["id"]] if recovery is not None else []),
    }


def audit_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    _exact_keys(
        bundle,
        {"schema", "pattern_id", "sla_seconds", "observations"},
        "bundle",
    )
    if bundle["schema"] != SCHEMA:
        raise AckLatencyEvidenceError("bundle_schema_invalid")
    if bundle["pattern_id"] != PATTERN_ID:
        raise AckLatencyEvidenceError("pattern_id_invalid")
    sla_seconds = bundle["sla_seconds"]
    if not isinstance(sla_seconds, int) or not 1 <= sla_seconds <= 20:
        raise AckLatencyEvidenceError("sla_seconds_invalid")
    observations = bundle["observations"]
    if not isinstance(observations, list) or len(observations) < 3:
        raise AckLatencyEvidenceError("three_observations_required")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    seen_numeric_ids: set[int] = set()
    seen_legacy_ids: set[str] = set()
    for observation in observations:
        if not isinstance(observation, dict):
            raise AckLatencyEvidenceError("observation_invalid")
        if observation.get("pattern_id") != PATTERN_ID:
            raise AckLatencyEvidenceError("observation_pattern_invalid")
        observation_id = observation.get("observation_id")
        if not isinstance(observation_id, str) or not observation_id:
            raise AckLatencyEvidenceError("observation_id_invalid")
        if observation_id in seen:
            raise AckLatencyEvidenceError("duplicate_observation")
        seen.add(observation_id)
        kind = observation.get("source_kind")
        if kind not in ALLOWED_KINDS:
            raise AckLatencyEvidenceError("source_kind_forbidden")
        result = _public_observation(observation, sla_seconds=sla_seconds)
        rows = [
            observation["request"],
            observation["ack"],
            *([observation["recovery"]] if observation["recovery"] is not None else []),
        ]
        numeric_ids = {row["id"] for row in rows}
        legacy_ids = {row["legacy_id"] for row in rows}
        if len(numeric_ids) != len(rows) or seen_numeric_ids.intersection(numeric_ids):
            raise AckLatencyEvidenceError("duplicate_source_id")
        if len(legacy_ids) != len(rows) or seen_legacy_ids.intersection(legacy_ids):
            raise AckLatencyEvidenceError("duplicate_source_legacy_id")
        seen_numeric_ids.update(numeric_ids)
        seen_legacy_ids.update(legacy_ids)
        results.append(result)
    windows = {
        _utc(_timestamp(item["observed_at"], "observed_at")).date().isoformat()
        for item in results
    }
    if len(windows) < 2:
        raise AckLatencyEvidenceError("two_time_windows_required")
    late_count = sum(
        item["classification"].startswith("ack_late") for item in results
    )
    if late_count < 1:
        raise AckLatencyEvidenceError("no_measured_late_ack")
    result = {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "pattern_id": PATTERN_ID,
        "observations": results,
        "observation_count": len(results),
        "time_windows": sorted(windows),
        "late_ack_count": late_count,
        "authority": "A2_READ_ONLY",
        "allowed_tools": [],
        "network": "none",
        "mutations": 0,
    }
    result["result_sha256"] = sha256(result)
    return result


def _write_private(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(result) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(raw, 0o600)
        os.replace(raw, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(raw):
            os.unlink(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        bundle = json.loads(args.input.read_text(encoding="utf-8"))
        result = audit_bundle(bundle)
    except (OSError, json.JSONDecodeError, AckLatencyEvidenceError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    if args.output is not None:
        _write_private(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
