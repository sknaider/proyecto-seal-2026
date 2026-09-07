from __future__ import annotations

from datetime import datetime, timezone

from memory.ssai_runtime import build_identity_projection, projection_artifact


def _identity(**updates):
    row = {
        "id": 1,
        "agent": "ADA",
        "personality": {"role": "Engineer", "private_note": "never persist raw"},
        "boot_context": "William and ADA private continuity text",
        "philosophy": "direct and protective",
        "ocean_scores": {"O": 0.81, "C": 1.0},
        "ocean_baseline": {"O": 0.81, "C": 1.0},
        "ocean_lock_hash": "sha256:" + "1" * 64,
        "updated_at": datetime(2026, 7, 17, tzinfo=timezone.utc),
    }
    row.update(updates)
    return row


def test_projection_is_deterministic_and_privacy_minimized() -> None:
    first_bytes, first_hash = projection_artifact(_identity())
    second_bytes, second_hash = projection_artifact(_identity())
    assert first_bytes == second_bytes
    assert first_hash == second_hash
    assert first_hash.startswith("sha256:")
    assert b"private continuity" not in first_bytes
    assert b"private_note" not in first_bytes
    assert b"never persist raw" not in first_bytes


def test_identity_change_changes_projection_without_timestamp_noise() -> None:
    _, baseline = projection_artifact(_identity())
    _, same_identity_new_timestamp = projection_artifact(
        _identity(updated_at=datetime(2026, 7, 18, tzinfo=timezone.utc))
    )
    _, changed = projection_artifact(
        _identity(ocean_scores={"O": 0.82, "C": 1.0})
    )
    assert same_identity_new_timestamp == baseline
    assert changed != baseline


def test_projection_contract_has_only_hash_commitments() -> None:
    projection = build_identity_projection(_identity())
    assert set(projection) == {"schema", "agent", "source", "commitments"}
    assert set(projection["commitments"]) == {
        "personality",
        "boot_context",
        "philosophy",
        "ocean_scores",
        "ocean_baseline",
        "ocean_lock",
    }
    assert all(value.startswith("sha256:") for value in projection["commitments"].values())
