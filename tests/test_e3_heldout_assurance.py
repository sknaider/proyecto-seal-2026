from __future__ import annotations

import json
from pathlib import Path

import pytest

from docs.thesis.ssai.experiments import e3_heldout_assurance as heldout


def test_frozen_catalog_is_hash_bound_and_disjoint_from_original() -> None:
    catalog = heldout.load_frozen_cases()
    assert catalog["externally_anchored_preregistration"] is False
    ids = {case["id"] for case in catalog["cases"]}
    original = {
        "event_payload_tamper", "hashchain_head_tamper", "record_reorder",
        "record_replay", "rollback_truncate", "insider_recompute_fork",
        "manifest_signature_tamper",
    }
    assert len(ids) == 8
    assert ids.isdisjoint(original)


def test_all_heldout_attacks_detected_by_preregistered_detector() -> None:
    evidence = heldout.run_assurance()
    assert evidence["assurance_design"] == {
        "new_operators_relative_to_original_e3": True,
        "externally_anchored_preregistration": False,
        "independent_reviewer_challenge_required": True,
    }
    assert evidence["summary"] == {
        "attacks_total": 8,
        "attacks_detected": 8,
        "detectors_match_preregistered": True,
        "honest_control_accepted": True,
        "fail_closed_pass": True,
    }
    assert all(outcome["detected"] for outcome in evidence["outcomes"])


def test_witness_cases_are_internally_valid_but_externally_rejected() -> None:
    evidence = heldout.run_assurance()
    witness = [row for row in evidence["outcomes"] if row["detector"] == "witness"]
    assert len(witness) == 2
    assert all(row["verify_state"] == "ACCEPT(blind)" for row in witness)
    assert all("fork" in row["detail"].lower() for row in witness)


def test_honest_control_is_non_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(heldout, "_honest_control", lambda _tmp: False)
    evidence = heldout.run_assurance()
    assert evidence["summary"]["honest_control_accepted"] is False
    assert evidence["summary"]["fail_closed_pass"] is False


def test_case_catalog_tamper_fails_closed(tmp_path: Path) -> None:
    catalog = json.loads(heldout.CASES_PATH.read_text(encoding="utf-8"))
    catalog["cases"][0]["expected_detector"] = "witness"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        heldout.load_frozen_cases(path)


def test_cli_writes_evidence_and_returns_nonzero_on_miss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "evidence.json"
    monkeypatch.setattr("sys.argv", ["e3-heldout", "--json", str(output)])
    assert heldout.main() == 0
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["fail_closed_pass"] is True

    original = heldout.CASE_FUNCTIONS["middle_record_deletion"]
    monkeypatch.setitem(
        heldout.CASE_FUNCTIONS,
        "middle_record_deletion",
        lambda _tmp: heldout.Outcome(
            "middle_record_deletion", "event", "verify", False, "verify",
            "ACCEPT(!!)", 1.0, "0" * 64, "forced miss",
        ),
    )
    monkeypatch.setattr("sys.argv", ["e3-heldout", "--json", str(output)])
    assert heldout.main() == 1
    monkeypatch.setitem(heldout.CASE_FUNCTIONS, "middle_record_deletion", original)
