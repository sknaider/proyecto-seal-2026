#!/usr/bin/env python3
"""E3 assurance held-out battery for SSAI SHADOW.

The case catalog is frozen outside this module and contains mutation operators
that were not part of the original seven-case E3 battery.  All execution uses
ephemeral files and keys; production ledgers, witnesses, databases and daemons
are never opened.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.ssai_shadow.crypto import (  # noqa: E402
    generate_private_key,
    manifest_digest,
    public_key_b64,
    sign_manifest,
    verify_manifest,
)
from memory.ssai_shadow.ledger import ShadowLedger, WitnessStore  # noqa: E402
from memory.ssai_shadow.manifest import build_genesis_manifest, generate_soul_id  # noqa: E402

CASES_PATH = Path(__file__).with_name("E3_HELDOUT_CASES.json")
DEFAULT_EVIDENCE_PATH = Path(__file__).with_name("E3_HELDOUT_EVIDENCE.json")
EVIDENCE_SCHEMA = "ssai-e3-heldout-evidence-v1"
FRAMEWORK_STATE = "SHADOW/TOFU_UNANCHORED"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_ref(seed: str) -> str:
    return "sha256:" + _sha256(seed.encode("utf-8"))


def _measure(call: Callable[[], object]) -> tuple[object, float]:
    started = time.perf_counter_ns()
    result = call()
    return result, round((time.perf_counter_ns() - started) / 1_000.0, 3)


def load_frozen_cases(path: Path = CASES_PATH) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "ssai-e3-heldout-cases-v1":
        raise ValueError("unsupported held-out case schema")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("held-out cases must be a non-empty list")
    identifiers = [case.get("id") for case in cases]
    if len(identifiers) != len(set(identifiers)) or not all(identifiers):
        raise ValueError("held-out case IDs must be non-empty and unique")
    observed = _sha256(_canonical(cases))
    if document.get("case_set_sha256") != observed:
        raise ValueError("held-out case set hash mismatch")
    return document


def _fresh_ledger(tmp: Path, name: str, events: list[dict]) -> ShadowLedger:
    ledger = ShadowLedger(tmp / f"{name}.jsonl")
    for event in events:
        ledger.append(event)
    return ledger


def _manifest_fixture() -> tuple[dict, object]:
    signing_key = generate_private_key()
    controller_keys = {
        role: public_key_b64(generate_private_key())
        for role in ("genesis_root", "agent_identity", "custodian")
    }
    manifest = build_genesis_manifest(
        display_name="ASSURANCE-SYNTHETIC",
        issued_at="2026-08-20T00:00:00Z",
        constitution={
            "document_hash": _hash_ref("heldout-document"),
            "critical_rules_root": _hash_ref("heldout-rules"),
            "governance_policy_hash": _hash_ref("heldout-policy"),
        },
        identity_state={
            "personality_baseline_hash": _hash_ref("heldout-personality"),
            "ocean_baseline_hash": _hash_ref("heldout-ocean"),
            "relationships_root": _hash_ref("heldout-relationships"),
            "memory_commitment_root": _hash_ref("heldout-memory"),
        },
        evidence=["ssai-e3-heldout:v1"],
        controller_public_keys=controller_keys,
        soul_id=generate_soul_id(timestamp_ms=1_776_297_600_000, random_bits=73),
    )
    return manifest, signing_key


@dataclass(frozen=True)
class Outcome:
    case_id: str
    surface: str
    expected_detector: str
    detected: bool
    detector: str
    verify_state: str
    latency_us: float
    evaluated_sha256: str
    detail: str


def _crypto_outcome(case_id: str, mutate: Callable[[dict, str, object], tuple[dict, str]]) -> Outcome:
    manifest, pinned_key = _manifest_fixture()
    signature = sign_manifest(manifest, pinned_key)
    assert verify_manifest(manifest, signature, pinned_key.public_key())
    candidate, candidate_signature = mutate(manifest, signature, pinned_key)
    accepted, latency = _measure(
        lambda: verify_manifest(candidate, candidate_signature, pinned_key.public_key())
    )
    return Outcome(
        case_id, "signature" if case_id == "valid_signature_wrong_signer" else "manifest",
        "crypto", not bool(accepted), "crypto", "-", latency,
        manifest_digest(candidate).split(":", 1)[1],
        "pinned-key verification rejected mutation" if not accepted else "accepted mutation",
    )


def manifest_identity_state_substitution(_tmp: Path) -> Outcome:
    def mutate(manifest: dict, signature: str, _key: object) -> tuple[dict, str]:
        candidate = json.loads(json.dumps(manifest))
        candidate["identity_state"]["memory_commitment_root"] = _hash_ref("forged-memory")
        return candidate, signature
    return _crypto_outcome("manifest_identity_state_substitution", mutate)


def manifest_controller_key_substitution(_tmp: Path) -> Outcome:
    def mutate(manifest: dict, signature: str, _key: object) -> tuple[dict, str]:
        candidate = json.loads(json.dumps(manifest))
        candidate["controllers"][0]["public_key"] = public_key_b64(generate_private_key())
        return candidate, signature
    return _crypto_outcome("manifest_controller_key_substitution", mutate)


def valid_signature_wrong_signer(_tmp: Path) -> Outcome:
    def mutate(manifest: dict, _signature: str, _key: object) -> tuple[dict, str]:
        attacker_key = generate_private_key()
        return manifest, sign_manifest(manifest, attacker_key)
    return _crypto_outcome("valid_signature_wrong_signer", mutate)


def _verify_mutated_ledger(case_id: str, surface: str, ledger: ShadowLedger) -> Outcome:
    digest = _sha256(ledger.path.read_bytes())
    result, latency = _measure(ledger.verify)
    return Outcome(
        case_id, surface, "verify", not result.ok, "verify",
        "REJECT" if not result.ok else "ACCEPT(!!)", latency, digest,
        result.errors[0] if result.errors else "accepted mutation",
    )


def middle_event_field_injection(tmp: Path) -> Outcome:
    ledger = _fresh_ledger(tmp, "field", [{"type": "a"}, {"type": "b"}, {"type": "c"}])
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    middle = json.loads(lines[1])
    middle["event"]["injected"] = True
    lines[1] = json.dumps(middle, separators=(",", ":"))
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _verify_mutated_ledger("middle_event_field_injection", "event", ledger)


def middle_record_deletion(tmp: Path) -> Outcome:
    ledger = _fresh_ledger(tmp, "delete", [{"type": "a"}, {"type": "b"}, {"type": "c"}])
    lines = ledger.path.read_bytes().splitlines(keepends=True)
    ledger.path.write_bytes(lines[0] + lines[2])
    return _verify_mutated_ledger("middle_record_deletion", "event", ledger)


def cross_ledger_prev_hash_graft(tmp: Path) -> Outcome:
    ledger = _fresh_ledger(tmp, "graft-a", [{"type": "a1"}, {"type": "a2"}])
    donor = _fresh_ledger(tmp, "graft-b", [{"type": "b1"}, {"type": "b2"}])
    donor_first = json.loads(donor.path.read_text(encoding="utf-8").splitlines()[0])
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["prev_hash"] = donor_first["event_hash"]
    lines[1] = json.dumps(second, separators=(",", ":"))
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _verify_mutated_ledger("cross_ledger_prev_hash_graft", "tree_head", ledger)


def _witness_fork(case_id: str, honest_events: list[dict], forged_events: list[dict], tmp: Path) -> Outcome:
    honest = _fresh_ledger(tmp, f"{case_id}-honest", honest_events)
    honest_head = honest.verify()
    witness = WitnessStore(tmp / f"{case_id}.witness")
    witness.record(honest_head.sequence, honest_head.head_hash)
    forged = _fresh_ledger(tmp, f"{case_id}-forged", forged_events)
    honest.path.write_bytes(forged.path.read_bytes())
    internal = honest.verify()
    witness_result, latency = _measure(lambda: witness.verify(internal))
    return Outcome(
        case_id,
        "rollback" if case_id == "same_sequence_history_replacement" else "insider_fork",
        "witness", internal.ok and not witness_result.ok, "witness",
        "ACCEPT(blind)" if internal.ok else "REJECT", latency,
        _sha256(honest.path.read_bytes()),
        witness_result.errors[0] if witness_result.errors else "witness accepted fork",
    )


def same_sequence_history_replacement(tmp: Path) -> Outcome:
    return _witness_fork(
        "same_sequence_history_replacement",
        [{"type": "a"}, {"type": "b"}, {"type": "c"}, {"type": "d"}],
        [{"type": "old-a"}, {"type": "old-b"}, {"type": "old-c"}, {"type": "old-d"}],
        tmp,
    )


def insider_middle_recompute_fork(tmp: Path) -> Outcome:
    return _witness_fork(
        "insider_middle_recompute_fork",
        [{"type": "a"}, {"type": "b", "value": "honest"}, {"type": "c"}, {"type": "d"}],
        [{"type": "a"}, {"type": "b", "value": "forged"}, {"type": "c"}, {"type": "d"}],
        tmp,
    )


CASE_FUNCTIONS: dict[str, Callable[[Path], Outcome]] = {
    function.__name__: function
    for function in (
        manifest_identity_state_substitution,
        manifest_controller_key_substitution,
        valid_signature_wrong_signer,
        middle_event_field_injection,
        middle_record_deletion,
        cross_ledger_prev_hash_graft,
        same_sequence_history_replacement,
        insider_middle_recompute_fork,
    )
}


def _honest_control(tmp: Path) -> bool:
    """Prove that the battery accepts an untampered ledger and witness."""

    ledger = _fresh_ledger(tmp, "honest-control", [{"type": "a"}, {"type": "b"}])
    head = ledger.verify()
    witness = WitnessStore(tmp / "honest-control.witness")
    witness.record(head.sequence, head.head_hash)
    return witness.verify(ledger.verify()).ok


def run_assurance(cases_path: Path = CASES_PATH) -> dict:
    catalog = load_frozen_cases(cases_path)
    with tempfile.TemporaryDirectory(prefix="ssai_e3_heldout_") as directory:
        tmp = Path(directory)
        outcomes = [CASE_FUNCTIONS[case["id"]](tmp) for case in catalog["cases"]]
        honest_ok = _honest_control(tmp)
    expected = {case["id"]: case["expected_detector"] for case in catalog["cases"]}
    detectors_match = all(outcome.detector == expected[outcome.case_id] for outcome in outcomes)
    all_detected = all(outcome.detected for outcome in outcomes)
    return {
        "schema": EVIDENCE_SCHEMA,
        "framework_state": FRAMEWORK_STATE,
        "case_set_sha256": catalog["case_set_sha256"],
        "assurance_design": {
            "new_operators_relative_to_original_e3": True,
            "externally_anchored_preregistration": False,
            "independent_reviewer_challenge_required": True,
        },
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "summary": {
            "attacks_total": len(outcomes),
            "attacks_detected": sum(int(outcome.detected) for outcome in outcomes),
            "detectors_match_preregistered": detectors_match,
            "honest_control_accepted": honest_ok,
            "fail_closed_pass": all_detected and detectors_match and honest_ok,
        },
        "outcomes": [asdict(outcome) for outcome in outcomes],
        "limits": [
            "SHADOW/TOFU_UNANCHORED only; no production or mainnet claim.",
            "Ephemeral local witness models an independently protected witness but does not provide one.",
            "Held-out refers to new mutation operators relative to the original seven-case E3 battery.",
            "The local case catalog was not externally anchored before execution; this is not a blinded preregistered trial.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=CASES_PATH)
    parser.add_argument("--json", type=Path, default=DEFAULT_EVIDENCE_PATH)
    args = parser.parse_args()
    evidence = run_assurance(args.cases)
    args.json.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = evidence["summary"]
    print(
        f"E3 held-out: {summary['attacks_detected']}/{summary['attacks_total']} detected; "
        f"honest_control={summary['honest_control_accepted']}; "
        f"detectors_match={summary['detectors_match_preregistered']}"
    )
    return 0 if summary["fail_closed_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
