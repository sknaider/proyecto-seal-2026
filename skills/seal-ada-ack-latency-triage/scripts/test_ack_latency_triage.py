from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest


MODULE = Path(__file__).with_name("ack_latency_triage.py")
SPEC = importlib.util.spec_from_file_location("ack_latency_triage", MODULE)
triage = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(triage)


def _public(observation_id: str, base: int, request_at: str, ack_at: str) -> dict:
    source = f"api_william_{base}"
    return {
        "observation_id": observation_id,
        "pattern_id": triage.PATTERN_ID,
        "source_kind": "public_web_chat",
        "channel": "web_chat",
        "request": {
            "id": base,
            "legacy_id": source,
            "sender": "William",
            "created_at": request_at,
            "in_reply_to": None,
        },
        "ack": {
            "id": base + 1,
            "legacy_id": f"api_ada_{base + 1}",
            "sender": "ADA",
            "created_at": ack_at,
            "in_reply_to": source,
        },
        "recovery": {
            "id": base + 2,
            "legacy_id": f"api_ada_{base + 2}",
            "sender": "ADA",
            "created_at": ack_at,
            "in_reply_to": source,
        },
    }


def _bundle() -> dict:
    return {
        "schema": triage.SCHEMA,
        "pattern_id": triage.PATTERN_ID,
        "sla_seconds": 20,
        "observations": [
            _public(
                "ada-public-ack-110560-110561",
                110560,
                "2026-07-15T21:22:20.955764Z",
                "2026-07-15T21:23:34.721731Z",
            ),
            _public(
                "ada-public-ack-115414-115415",
                115414,
                "2026-07-21T21:31:20.322493Z",
                "2026-07-21T21:32:22.681902Z",
            ),
            _public(
                "ada-public-ack-117182-117183",
                117182,
                "2026-07-23T21:22:20.955764Z",
                "2026-07-23T21:23:34.721731Z",
            ),
        ],
    }


def test_real_pattern_is_classified_and_hash_bound() -> None:
    result = triage.audit_bundle(_bundle())
    assert result["ok"]
    assert result["observation_count"] == 3
    assert result["late_ack_count"] == 3
    assert result["time_windows"] == ["2026-07-15", "2026-07-21", "2026-07-23"]
    assert result["authority"] == "A2_READ_ONLY"
    assert result["allowed_tools"] == []
    assert result["network"] == "none"
    assert result["mutations"] == 0
    assert result["observations"][2]["ack_latency_ms"] == 73766


@pytest.mark.parametrize(
    ("mutator", "error"),
    [
        (
            lambda value: value["observations"][1].__setitem__(
                "channel", "dm:ada:william"
            ),
            "private_or_unknown_channel_forbidden",
        ),
        (
            lambda value: value["observations"][1]["ack"].__setitem__(
                "in_reply_to", "wrong"
            ),
            "ack_reply_binding_invalid",
        ),
        (
            lambda value: value["observations"][1].__setitem__(
                "source_kind", "agent_task"
            ),
            "source_kind_forbidden",
        ),
        (
            lambda value: value.__setitem__("sla_seconds", 60),
            "sla_seconds_invalid",
        ),
        (
            lambda value: value["observations"].pop(),
            "three_observations_required",
        ),
    ],
)
def test_boundary_fails_closed(mutator, error: str) -> None:
    bundle = copy.deepcopy(_bundle())
    mutator(bundle)
    with pytest.raises(triage.AckLatencyEvidenceError, match=error):
        triage.audit_bundle(bundle)


def test_private_output_permissions(tmp_path: Path) -> None:
    result = triage.audit_bundle(_bundle())
    target = tmp_path / "private" / "result.json"
    triage._write_private(target, result)
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700


def test_duplicate_request_with_renamed_observation_is_rejected() -> None:
    bundle = _bundle()
    duplicate = copy.deepcopy(bundle["observations"][0])
    duplicate["ack"]["id"] = 130001
    duplicate["ack"]["legacy_id"] = "api_ada_130001"
    duplicate["recovery"]["id"] = 130002
    duplicate["recovery"]["legacy_id"] = "api_ada_130002"
    duplicate["observation_id"] = "ada-public-ack-110560-130001"
    bundle["observations"][2] = duplicate
    with pytest.raises(triage.AckLatencyEvidenceError, match="duplicate_source_id"):
        triage.audit_bundle(bundle)


def test_observation_id_must_be_derived_from_source_ids() -> None:
    bundle = _bundle()
    bundle["observations"][0]["observation_id"] = "renamed-observation"
    with pytest.raises(
        triage.AckLatencyEvidenceError, match="observation_id_not_canonical"
    ):
        triage.audit_bundle(bundle)


def test_time_windows_are_normalized_to_utc() -> None:
    bundle = _bundle()
    timestamp_sets = (
        ("2026-07-23T23:00:00-02:00", "2026-07-23T23:01:00-02:00", "2026-07-23T23:02:00-02:00"),
        ("2026-07-24T02:00:00+01:00", "2026-07-24T02:01:00+01:00", "2026-07-24T02:02:00+01:00"),
        ("2026-07-24T03:00:00+02:00", "2026-07-24T03:01:00+02:00", "2026-07-24T03:02:00+02:00"),
    )
    for observation, timestamps in zip(bundle["observations"], timestamp_sets):
        observation["request"]["created_at"] = timestamps[0]
        observation["ack"]["created_at"] = timestamps[1]
        observation["recovery"]["created_at"] = timestamps[2]
    with pytest.raises(
        triage.AckLatencyEvidenceError, match="two_time_windows_required"
    ):
        triage.audit_bundle(bundle)


def test_synthetic_legacy_id_is_rejected() -> None:
    bundle = _bundle()
    bundle["observations"][0]["request"]["legacy_id"] = "api_william_test_synthetic"
    bundle["observations"][0]["ack"]["in_reply_to"] = "api_william_test_synthetic"
    bundle["observations"][0]["recovery"]["in_reply_to"] = (
        "api_william_test_synthetic"
    )
    with pytest.raises(
        triage.AckLatencyEvidenceError, match="request_synthetic_id_forbidden"
    ):
        triage.audit_bundle(bundle)


def test_recovery_requires_typed_legacy_id() -> None:
    bundle = _bundle()
    bundle["observations"][0]["recovery"]["legacy_id"] = None
    with pytest.raises(
        triage.AckLatencyEvidenceError, match="recovery_legacy_id_invalid"
    ):
        triage.audit_bundle(bundle)
